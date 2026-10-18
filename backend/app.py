from pathlib import Path
from fastapi import FastAPI,HTTPException
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,Field
from .models import Model
from .scenarios import scenarios,get_scenario
from .validation import analyze
from .advanced import run_optimization as optimize
from .exports import export_data,import_dxf
from .work_limits import WorkCancelled,WorkLimitExceeded
app=FastAPI(title='PIPEFORGE',version='2.0.0',description='Engineering decision-support prototype; not certified')
class OptimizeRequest(BaseModel):
    model:Model
    pipe_ids:list[str]|None=None
    mode:str='balanced'
    alternatives:bool=False
    multiresolution:bool=False
    try_orders:bool=False
    resolution:float=Field(default=.5,ge=.2,le=2)
    clearance:float=Field(default=.1,ge=0,le=1)
class ExportRequest(BaseModel):
    original:Model
    proposed:Model
    clearance:float=Field(default=.1,ge=0,le=1)
class DXFRequest(BaseModel):
    dxf:str=Field(max_length=10_000_000)
    metadata:Model
@app.get('/api/health')
def health():return {'status':'ok','certified':False}
@app.get('/api/scenarios')
def list_scenarios():return [dict(id=m.id,name=m.name,description='Deterministic development fixture; illustrative standards') for m in scenarios()]
@app.get('/api/scenarios/{id}')
def scenario(id:str):
    m=get_scenario(id)
    if m is None:raise HTTPException(404,'Scenario not found')
    return m
class AnalyzeRequest(BaseModel):
    model:Model
    clearance:float=Field(default=.1,ge=0,le=1)
@app.post('/api/analyze')
def analysis(body:Model|AnalyzeRequest):
    try:return analyze(body.model,body.clearance) if isinstance(body,AnalyzeRequest) else analyze(body)
    except ValueError as e:raise HTTPException(422,str(e)) from e
@app.post('/api/optimize')
def optimization(body:OptimizeRequest):
    try:return optimize(**body.model_dump(exclude={'model'}),model=body.model)
    except ValueError as e:raise HTTPException(422,str(e))
# Bounded in-memory job runner: progress is real expanded search-state count.
import threading,uuid,time
from concurrent.futures import ThreadPoolExecutor
jobs={}
job_lock=threading.Lock()
executor=ThreadPoolExecutor(max_workers=2)
@app.post('/api/jobs')
def create_job(body:OptimizeRequest):
    with job_lock:
        if sum(j['status'] in ('queued','running') for j in jobs.values())>=2:raise HTTPException(429,'Two optimization jobs are already active')
        if len(jobs)>=20:
            completed=next((k for k,v in jobs.items() if v['status'] not in ('queued','running')),None)
            if completed:del jobs[completed]
        id=uuid.uuid4().hex; job={'status':'queued','expanded':0,'cancel':threading.Event(),'result':None,'error':None};jobs[id]=job
    def work():
        job['status']='running'
        try:
            result=optimize(model=body.model,**body.model_dump(exclude={'model'}),cancel=job['cancel'],progress=lambda n:job.update(expanded=n))
            job['result']=result;job['status']='cancelled' if job['cancel'].is_set() else 'complete'
        except WorkCancelled:
            job['result']={'model':body.model.model_dump(mode='json'),'candidates':[],'issues':[]};job['status']='cancelled'
        except Exception as e:job['error']=str(e);job['status']='failed'
    executor.submit(work)
    return {'id':id}
@app.get('/api/jobs/{id}')
def job_status(id:str):
    job=jobs.get(id)
    if job is None:raise HTTPException(404,'Job not found')
    return {k:v for k,v in job.items() if k!='cancel'}
@app.delete('/api/jobs/{id}')
def cancel_job(id:str):
    job=jobs.get(id)
    if job is None:raise HTTPException(404,'Job not found')
    job['cancel'].set();return {'status':'cancellation_requested'}

@app.post('/api/export/{format}')
def export(format:str,body:ExportRequest):
    try:
        data,mime=export_data(body.original,body.proposed,format,body.clearance)
        ext='txt' if format=='report' else format
        return Response(data,media_type=mime,headers={'Content-Disposition':f'attachment; filename="pipeforge.{ext}"'})
    except ValueError as e:raise HTTPException(422,str(e))
@app.post('/api/import/dxf')
def dxf(body:DXFRequest):
    try:return import_dxf(body.dxf,body.metadata)
    except Exception as e:raise HTTPException(422,'DXF import failed: '+str(e))
@app.post('/api/import/json')
def engineering_json(model:Model):return model
from .models import Fitting
from .step_adapter import available as step_available
@app.get('/api/capabilities')
def capabilities():return {'step':step_available(),'native_dwg':False,'autodesk_tested':False,'step_scope':'Named straight pipe solids and obstacle boxes; not manufactured fitting geometry'}
from .validation import validate
class CatalogRequest(BaseModel):
    fittings:list[Fitting]=Field(min_length=1,max_length=100)
@app.post('/api/catalog/validate')
def catalog(body:CatalogRequest):return {'fittings':[f.model_dump() for f in body.fittings],'approval':'Input metadata only; commercial approval is not independently verified'}
class ValidationRequest(ExportRequest):
    pipe_id:str
@app.post('/api/validate')
def validation(body:ValidationRequest):
    try:return {'checks':validate(body.original,body.proposed,body.pipe_id,body.clearance)}
    except ValueError as e:raise HTTPException(422,str(e)) from e
from .edge_api import router as edge_router
app.include_router(edge_router)
from .network_api import router as network_router
app.include_router(network_router)
web=Path(__file__).resolve().parent.parent/'frontend'/'dist'
if web.exists():app.mount('/',StaticFiles(directory=web,html=True),name='workspace')
