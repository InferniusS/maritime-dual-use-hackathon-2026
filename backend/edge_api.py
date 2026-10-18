"""CAD companion APIs: explicit source revisions, background work and audited decisions."""
import copy,json,platform,threading,time,uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Literal
from fastapi import APIRouter,HTTPException,Query
from fastapi.responses import Response
from pydantic import BaseModel,ConfigDict,Field
from .models import Model,Pipe,Obstacle
from .edge_store import SessionStore,RevisionConflict,ApprovalError,NotFound
from .edge_remote import execute_remote_or_local,probe_remote,PROTOCOL
from .exports import export_data
from .cad_exchange import CoordinateTransform,import_exchange_dxf
from .extraction import ExtractionCoverage,coverage_validation
from .work_limits import WorkLimits,work_scope,preflight_model,check_result,checkpoint,WorkCancelled

class StrictRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
class CreateSession(StrictRequest):
    drawing_id:str=Field(min_length=1,max_length=256)
    model:Model
    object_map:dict[str,str|list[str]]=Field(default_factory=dict)
    source_adapter:Literal['autocad','json','dxf','procedural']='json'
    coordinate_transform:CoordinateTransform=Field(default_factory=CoordinateTransform)
    extraction_coverage:ExtractionCoverage|None=None
class Delta(StrictRequest):
    base_revision:int=Field(ge=1)
    upsert_pipes:list[Pipe]=Field(default_factory=list,max_length=100)
    remove_pipe_ids:list[str]=Field(default_factory=list,max_length=100)
    upsert_obstacles:list[Obstacle]=Field(default_factory=list,max_length=1000)
    remove_obstacle_ids:list[str]=Field(default_factory=list,max_length=1000)
    object_map:dict[str,str|list[str]]|None=None
    extraction_coverage:ExtractionCoverage|None=None
    compact:bool=False
class RemoteConfig(StrictRequest):
    enabled:bool=False
    endpoint:str=Field(default='',max_length=2048)
    timeout_s:float=Field(default=30,ge=.05,le=120)
    access_token:str|None=Field(default=None,max_length=4096,repr=False)
class SessionJob(StrictRequest):
    revision:int=Field(ge=1)
    operation:Literal['analyze','optimize']
    pipe_ids:list[str]|None=Field(default=None,max_length=100)
    mode:Literal['shortest','bends','balanced','hydraulic','modification']='balanced'
    resolution:float=Field(default=.5,ge=.2,le=2)
    clearance:float=Field(default=.1,ge=0,le=1)
    alternatives:bool=False
    multiresolution:bool=False
    try_orders:bool=False
    remote:RemoteConfig=Field(default_factory=RemoteConfig)
    limits:WorkLimits=Field(default_factory=WorkLimits)
class DXFExchange(StrictRequest):
    dxf:str=Field(max_length=10_000_000)
    metadata:Model
    source_units:Literal['m','mm','cm','in','ft','km']|None=None
class Decision(StrictRequest):
    revision:int=Field(ge=1)
    decision:Literal['approve','reject']
    engineer:str=Field(min_length=1,max_length=200)
    comment:str=Field(default='',max_length=4000)
class Apply(StrictRequest):
    revision:int=Field(ge=1)
    engineer:str=Field(min_length=1,max_length=200)
    confirmation:bool=False
class Probe(StrictRequest):
    endpoint:str=Field(min_length=1,max_length=2048)
    timeout_s:float=Field(default=3,ge=.05,le=120)
    access_token:str|None=Field(default=None,max_length=4096,repr=False)

def _call(work):
    try:return work()
    except NotFound as e:raise HTTPException(404,'Session or proposal not found') from e
    except RevisionConflict as e:raise HTTPException(409,str(e)) from e
    except ApprovalError as e:raise HTTPException(409,str(e)) from e
    except ValueError as e:raise HTTPException(422,str(e)) from e

def recommendations(analysis,capture,proposals=None):
    result=[]
    for issue in analysis.get('issues',[]):
        object_ids=list(dict.fromkeys(issue.get('object_ids',[issue.get('pipe_id')])))
        handles=list(dict.fromkeys(h for id in object_ids for h in capture['object_map'].get(id,[])))
        matching=[p for p in (proposals or []) if issue.get('pipe_id') in p['affected_component_ids']]
        result.append(dict(id=issue['id'],source_revision=capture['revision'],drawing_id=capture['drawing_id'],pipe_id=issue.get('pipe_id'),object_ids=object_ids,cad_handles=handles,issue_type=issue['type'],severity=issue['severity'],location=issue.get('location'),measured=issue.get('measured'),required=issue.get('required'),explanation=issue['explanation'],suggested_correction=issue.get('suggested_correction'),proposed_modification=[dict(proposal_id=p['id'],coordinates=p['proposed_geometry'].get(issue.get('pipe_id'))) for p in matching],validation_results=[r for p in matching for c in p['candidates'] if c['pipe_id']==issue.get('pipe_id') for r in c['validation']],expected_consequences=[e for p in matching for e in p['explanations']],verification_status=issue.get('verification_status','unverified'),unverified=['CAD object identity'] if not handles else []))
    return result

def create_router(store=None):
    store=store if store is not None else SessionStore()
    router=APIRouter(prefix='/api/edge',tags=['PipeForge Edge'])
    executor=ThreadPoolExecutor(max_workers=2,thread_name_prefix='pipeforge-edge')
    jobs={};lock=threading.RLock()
    @router.get('/health')
    def health():return dict(status='ok',protocol=PROTOCOL,service='revision-companion',architecture=platform.machine(),processor='CPU',gpu=False,npu=False,native_autocad_tested=False,qualcomm_hardware_tested=False)
    @router.post('/remote/health')
    def remote_health(body:Probe):return _call(lambda:probe_remote(body.endpoint,body.timeout_s,body.access_token))
    @router.post('/import/dxf')
    def import_exchange(body:DXFExchange):
        try:return import_exchange_dxf(body.dxf,body.metadata,body.source_units)
        except Exception as e:raise HTTPException(422,str(e)) from e
    @router.post('/sessions')
    def create(body:CreateSession):return _call(lambda:store.create(**body.model_dump(exclude={'model'}),model=body.model))
    @router.get('/sessions/{id}')
    def get(id:str):return _call(lambda:store.get(id))
    @router.get('/sessions/{id}/status')
    def status(id:str):return _call(lambda:store.status(id))
    @router.get('/sessions/{id}/audit')
    def audit(id:str,offset:int=Query(default=0,ge=0),limit:int=Query(default=100,ge=1,le=1000)):return _call(lambda:store.audit(id,offset,limit))
    @router.patch('/sessions/{id}')
    def patch(id:str,body:Delta):
        state=_call(lambda:store.patch(id,**body.model_dump(exclude={'compact'})))
        with lock:
            for job in jobs.values():
                if job['session_id']==id and job['source_revision']!=state['revision'] and job['status'] in ('queued','running'):job['_cancel'].set()
        if body.compact:return {key:state[key] for key in ('id','drawing_id','revision','model_digest','affected_pipe_ids','object_map')}
        return state
    @router.post('/sessions/{id}/jobs')
    def start(id:str,body:SessionJob):
        capture=_call(lambda:store.capture(id))
        if capture['revision']!=body.revision:raise HTTPException(409,'Requested drawing revision is stale')
        model=Model.model_validate(capture['model'])
        if body.pipe_ids is not None and (len(body.pipe_ids)!=len(set(body.pipe_ids)) or any(id not in {p.id for p in model.pipes} for id in body.pipe_ids)):raise HTTPException(422,'Unknown or duplicate pipe IDs')
        with lock:
            if sum(j['status'] in ('queued','running') for j in jobs.values())>=2:raise HTTPException(429,'Two Edge jobs are already active')
            if len(jobs)>=40:
                removable=next((k for k,j in jobs.items() if j['status'] not in ('queued','running')),None)
                if removable:del jobs[removable]
            jid=uuid.uuid4().hex
            job=dict(id=jid,session_id=id,source_revision=body.revision,operation=body.operation,status='queued',result=None,error=None,execution=None,expanded=0,_cancel=threading.Event());jobs[jid]=job
        def work():
            job['status']='running';started=time.perf_counter()
            try:
                with work_scope(body.limits,cancel=job['_cancel']):
                    options={'clearance':body.clearance,'limits':body.limits.model_dump()}
                    if body.operation=='optimize':options.update(body.model_dump(include={'pipe_ids','mode','resolution','alternatives','multiresolution','try_orders'}))
                    cache=None
                    if body.operation=='analyze' and not body.remote.enabled:
                        preflight_model(model,'incremental analysis')
                        result,cache=store.incremental_analysis(capture,body.clearance)
                        elapsed=(time.perf_counter()-started)*1000
                        execution=dict(location='local',processor='CPU',architecture=platform.machine(),local_compute_ms=elapsed,total_ms=elapsed,serialization_ms=0,roundtrip_ms=0,remote_compute_ms=None,decode_ms=0,network_and_overhead_ms=None,local_fallback_reason=None,request_bytes=0,response_bytes=0,payload_mode='in-process incremental',remote_cancel_supported=False)
                    else:
                        computed=execute_remote_or_local(body.operation,model,options,{**body.remote.model_dump(),'source_revision':str(body.revision)},cancel=job['_cancel'])
                        result=computed['result'];execution=computed['execution']
                        if body.operation=='analyze':
                            cache={p.id:dict(clearance=body.clearance,record=dict(issues=[i for i in result['issues'] if i.get('pipe_id')==p.id],metrics=result['metrics'][p.id])) for p in model.pipes}
                            result.update(affected_pipe_ids=[p.id for p in model.pipes],cache=dict(reused=0,recomputed=len(model.pipes),reused_pipe_ids=[],recomputed_pipe_ids=[p.id for p in model.pipes]))
                    job['execution']=execution
                    current=store.capture(id)
                    if current['revision']!=body.revision:job['status']='stale';return
                    if job['_cancel'].is_set():job['status']='cancelled';return
                    result['source_revision']=body.revision;result['drawing_id']=capture['drawing_id']
                    if body.operation=='analyze':
                        result['analysis']={key:result[key] for key in ('issues','metrics')}
                        result['recommendations']=recommendations(result,capture)
                        result['extraction_coverage']=capture['extraction_coverage']
                        result['source_validation']=[coverage_validation(capture['extraction_coverage'])]
                        check_result(result)
                        store.publish_analysis(id,body.revision,result,cache)
                    else:
                        # Existing source findings and precise proposal checks make every recommendation traceable.
                        source_analysis,_=store.incremental_analysis(capture,body.clearance)
                        def validate_publication(proposals):
                            result['proposals']=proposals
                            result['recommendations']=recommendations(source_analysis,capture,proposals)
                            check_result(result)
                        store.publish_proposals(id,body.revision,result,body.clearance,validate_output=validate_publication)
                        job['expanded']=sum(a.get('expanded',0) for a in result.get('exploration',[]))
                    job['result']=result;job['status']='complete'
            except RevisionConflict:job['status']='stale';job['result']=None
            except WorkCancelled:job['status']='cancelled';job['result']=None
            except Exception as e:job['error']=str(e);job['status']='failed'
            finally:job['elapsed_ms']=(time.perf_counter()-started)*1000
        executor.submit(work)
        return dict(id=jid,source_revision=body.revision,session_id=id,status='queued')
    @router.get('/jobs/{id}')
    def get_job(id:str):
        with lock:
            job=jobs.get(id)
            if job is None:raise HTTPException(404,'Job not found')
            public=copy.deepcopy({k:v for k,v in job.items() if not k.startswith('_')})
        current=_call(lambda:store.capture(public['session_id']))
        if public['source_revision']!=current['revision']:
            public['status']='stale';public['result']=None
        return public
    @router.delete('/jobs/{id}')
    def cancel(id:str):
        with lock:
            if id not in jobs:raise HTTPException(404,'Job not found')
            jobs[id]['_cancel'].set()
        return {'status':'cancellation_requested'}
    @router.post('/sessions/{id}/proposals/{pid}/decision')
    def decision(id:str,pid:str,body:Decision):return _call(lambda:store.decision(id,pid,**body.model_dump()))
    @router.post('/sessions/{id}/proposals/{pid}/apply')
    def apply(id:str,pid:str,body:Apply):return _call(lambda:store.apply(id,pid,**body.model_dump()))
    @router.get('/sessions/{id}/proposals/{pid}/export/{format}')
    def export(id:str,pid:str,format:str):
        state,proposal=_call(lambda:store.approved(id,pid))
        original=Model.model_validate(proposal['source_model']);proposed=Model.model_validate(proposal['model'])
        data,mime=_call(lambda:export_data(original,proposed,format,proposal['clearance']))
        approval=dict(drawing_id=state['drawing_id'],source_revision=proposal['source_revision'],source_digest=proposal['source_digest'],proposal_id=pid,status='approved_for_designer_exchange',engineer=proposal['engineer'],comment=proposal.get('comment',''),object_map=proposal['object_map'],coordinate_transform=proposal.get('coordinate_transform',{'source_units':'m','meters_per_unit':1.}),extraction_coverage=state['extraction_coverage'],cad_files_modified=False,construction_approved=False)
        if format=='json':
            package=json.loads(data);package['edge_approval']=approval;package['object_map']=proposal['object_map'];package['drawing_id']=state['drawing_id'];package['extraction_coverage']=state['extraction_coverage'];scale=approval['coordinate_transform']['meters_per_unit'];package['source_coordinate_instructions']=[dict(pipe_id=id,source_units=approval['coordinate_transform']['source_units'],original=[[v/scale for v in point] for point in proposal['original_geometry'][id]],proposed=[[v/scale for v in point] for point in points]) for id,points in proposal['proposed_geometry'].items()]
            for change in package['changes']:change['validation'].append(coverage_validation(state['extraction_coverage']))
            data=json.dumps(package,indent=2)
        elif format=='report':data+='\n\nEDGE DESIGNER REVIEW\n'+json.dumps(approval,indent=2)
        # Export generation can be expensive. Reject a source edit that happened meanwhile.
        _call(lambda:store.approved(id,pid))
        ext='txt' if format=='report' else format
        return Response(data,media_type=mime,headers={'Content-Disposition':f'attachment; filename="pipeforge-edge-{pid[:8]}.{ext}"','X-PipeForge-Revision':str(proposal['source_revision']),'X-PipeForge-Proposal':pid})
    router.store=store
    return router

router=create_router()
