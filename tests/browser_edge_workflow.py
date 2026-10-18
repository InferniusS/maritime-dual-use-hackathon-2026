"""Actual CAD-exchange companion flow against started local app and CPU worker."""
import io,json,sys,time
from pathlib import Path
import httpx
import ezdxf
from playwright.sync_api import sync_playwright

def main():
    artifacts=Path('/workspace/pipeforge-artifacts/edge');artifacts.mkdir(parents=True,exist_ok=True)
    client=httpx.Client(base_url='http://127.0.0.1:8000',timeout=40)
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox','--enable-unsafe-swiftshader','--use-gl=angle','--use-angle=swiftshader'])
        page=browser.new_page(viewport={'width':1750,'height':1150});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto('http://127.0.0.1:8000');page.get_by_role('button',name='Connect workspace drawing',exact=True).wait_for()
        page.get_by_label('Edge drawing identifier').fill('browser-engine-room.dwg')
        with page.expect_response(lambda r:r.url.endswith('/api/edge/sessions') and r.request.method=='POST') as response:
            page.get_by_role('button',name='Connect workspace drawing',exact=True).click()
        source=response.value.json();sid=source['id'];original=source['model']
        page.get_by_role('button',name='Analyze revision',exact=True).wait_for()
        page.get_by_label('Edge import CAD handle map').set_input_files({'name':'handles.json','mimeType':'application/json','buffer':json.dumps({'P-101':['A1'],'P-202':['B1'],'Pump skid':['E1']}).encode()})
        page.get_by_text('CAD object mapping updated. Run a fresh analysis for this revision.',exact=True).wait_for()
        page.get_by_role('button',name='Analyze revision',exact=True).click()
        page.locator('.edge-recommendation').first.wait_for(timeout=20000);page.locator('.edge-recommendation').first.click()
        assert 'Measured' in page.locator('.edge-recommendation').first.inner_text()
        assert 'A1' in page.locator('.edge-findings').inner_text()
        page.get_by_role('button',name='Remote worker',exact=True).click()
        page.get_by_label('Edge remote worker endpoint').fill('http://127.0.0.1:8010')
        page.get_by_role('button',name='Check worker',exact=True).click()
        page.get_by_role('button',name='Analyze revision',exact=True).click()
        # The previous completed job remains visible until POST returns; wait for this execution label.
        page.locator('.edge-job').get_by_text('Remote worker',exact=False).wait_for(timeout=25000)
        page.get_by_text('Analysis · complete',exact=True).wait_for(timeout=25000)
        page.get_by_role('button',name='Propose route',exact=True).click()
        page.get_by_role('button',name='Preview proposal in 3D',exact=True).wait_for(timeout=30000)
        assert client.get('/api/edge/sessions/'+sid).json()['model']==original
        page.get_by_role('button',name='Preview proposal in 3D',exact=True).click()
        assert client.get('/api/edge/sessions/'+sid).json()['model']==original
        page.get_by_label('Edge automatically sync edits').check()
        page.wait_for_timeout(700)
        assert client.get('/api/edge/sessions/'+sid).json()['revision']==source['revision']+1
        page.get_by_label('Edge reviewing engineer').fill('Browser review engineer')
        page.get_by_role('button',name='Approve proposal',exact=True).click()
        page.get_by_text('Approval recorded. Export the reviewed changes for manual CAD application.',exact=True).wait_for()
        reviewed=client.get('/api/edge/sessions/'+sid).json();proposal=reviewed['proposals'][-1]
        assert reviewed['model']==original and proposal['status']=='approved'
        with page.expect_download() as download:page.locator('.edge-export-actions').get_by_role('button',name='JSON',exact=True).click()
        package_path=artifacts/'reviewed-changes.json';download.value.save_as(str(package_path));package=json.loads(package_path.read_text())
        assert package['edge_approval']['construction_approved'] is False
        assert package['edge_approval']['source_revision']==reviewed['revision']
        assert package['object_map']['P-101']==['A1']
        # An external CAD update invalidates prior approvals and read-only previews.
        pipe=json.loads(json.dumps(original['pipes'][0]));pipe['points'][1][1]=5.5
        edited=client.patch('/api/edge/sessions/'+sid,json={'base_revision':reviewed['revision'],'upsert_pipes':[pipe]});assert edited.status_code==200
        assert client.get(f'/api/edge/sessions/{sid}/proposals/{proposal["id"]}/export/json').status_code==409
        page.get_by_role('button',name='Refresh CAD session',exact=True).click()
        page.get_by_role('button',name='Load latest source view',exact=True).click()
        page.get_by_label('Edge automatically sync edits').check();page.get_by_label('Edge live analyze after sync').check()
        page.get_by_role('button',name='Engineering workbench',exact=True).click()
        page.get_by_text('What-if constraints & catalog',exact=True).click()
        for i,v in enumerate([2,6,.1,3,7,1]):page.get_by_label('Zone '+str(i),exact=True).fill(str(v))
        page.get_by_role('button',name='Add restricted zone',exact=True).click()
        page.get_by_role('button',name='CAD companion',exact=True).click()
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            current=client.get('/api/edge/sessions/'+sid).json()
            if current['revision']>edited.json()['revision'] and current.get('analysis'):break
            page.wait_for_timeout(200)
        else:raise AssertionError('Debounced source sync/live analysis did not complete')
        assert len(current['model']['obstacles'])==len(original['obstacles'])+1
        # Transport failure visibly falls back; no remote speedup is assumed.
        page.get_by_label('Edge remote worker endpoint').fill('http://127.0.0.1:1')
        page.get_by_role('button',name='Analyze revision',exact=True).click()
        page.get_by_text('Analysis · complete',exact=True).wait_for(timeout=25000)
        page.get_by_text('Local fallback',exact=False).wait_for()
        page.screenshot(path=str(artifacts/'companion.png'),full_page=True)
        # Supported file exchange uses actual DXF entity handles and explicit millimeter conversion.
        exchange_model=client.get('/api/scenarios/efficient').json()
        with page.expect_response(lambda r:r.url.endswith('/api/edge/sessions') and r.request.method=='POST'):
            page.get_by_label('Edge import drawing file').set_input_files({'name':'metadata.json','mimeType':'application/json','buffer':json.dumps(exchange_model).encode()})
        page.get_by_text('Revision session created. Drawing objects remain under designer control.',exact=True).wait_for()
        dxf=ezdxf.new('R2013');dxf.units=4
        for pipe in exchange_model['pipes']:
            layer='PIPE_'+pipe['id'];dxf.layers.new(layer)
            dxf.modelspace().add_polyline3d([[v*1000 for v in point] for point in pipe['points']],dxfattribs={'layer':layer})
        buffer=io.StringIO();dxf.write(buffer)
        with page.expect_response(lambda r:r.url.endswith('/api/edge/sessions') and r.request.method=='POST') as exchange_response:
            page.get_by_label('Edge import drawing file').set_input_files({'name':'millimeter-source.dxf','mimeType':'application/dxf','buffer':buffer.getvalue().encode()})
        exchange=exchange_response.value.json()
        assert exchange['coordinate_transform']=={'source_units':'mm','meters_per_unit':.001}
        assert exchange['model']==exchange_model
        assert exchange['object_map']['P-101']==[next(iter(dxf.modelspace())).dxf.handle]
        page.get_by_text('Revision session created. Drawing objects remain under designer control.',exact=True).wait_for()
        # The native connector and companion share one session; attaching is read-only.
        native_model=client.get('/api/scenarios/congested').json()
        native_model.update(id='native-browser-source',name='Native CAD source')
        native=client.post('/api/edge/sessions',json={'drawing_id':'native-source.dwg','model':native_model,
            'source_adapter':'autocad','object_map':{'P-101':['1A']},
            'coordinate_transform':{'source_units':'mm','meters_per_unit':.001}}).json()
        native_path='/api/edge/sessions/'+native['id']
        page.get_by_label('Edge existing session ID').fill(native['id'])
        page.get_by_role('button',name='Attach CAD session',exact=True).click()
        page.locator('.model-badge').filter(has_text='Native CAD source').wait_for()
        assert page.get_by_label('Edge automatically sync edits').is_disabled()
        assert client.get(native_path).json()['audit_total']==native['audit_total']
        # Delay scenarios deliberately: its initial response must not overwrite the URL-attached drawing.
        attached=browser.new_page(viewport={'width':1750,'height':1150})
        attached.on('pageerror',lambda e:errors.append(str(e)))
        native_responses=[]
        def record_native_response(response):
            if native_path not in response.url:return
            event={'method':response.request.method,'path':response.url.split(':8000')[-1],'status':response.status}
            try:
                data=response.json();event.update({key:data[key] for key in ('revision','last_event_seq') if key in data})
            except Exception as error:event['read_error']=type(error).__name__
            native_responses.append(event)
        attached.on('response',record_native_response)
        def delay_scenarios(route):
            time.sleep(.8);route.continue_()
        attached.route('**/api/scenarios',delay_scenarios)
        attached.goto('http://127.0.0.1:8000/?edge_session='+native['id'])
        attached.locator('.model-badge').filter(has_text='Native CAD source').wait_for()
        attached.wait_for_timeout(1500)
        assert attached.locator('.model-badge').inner_text()=='Native CAD source'
        assert attached.get_by_label('Edge automatically sync edits').is_disabled()
        assert attached.get_by_label('Edge live analyze after sync').is_disabled()
        assert attached.get_by_role('button',name='Import CAD handle map',exact=True,include_hidden=True).is_disabled()
        assert client.get(native_path).json()['revision']==1
        changed=json.loads(json.dumps(native_model['pipes'][0]));changed['points'][1][1]+=.25
        external_body={'base_revision':1,'upsert_pipes':[changed]}
        external_response=client.patch(native_path,json=external_body)
        assert external_response.status_code==200,external_response.text
        external=external_response.json()
        try:attached.get_by_role('button',name='Load latest source view',exact=True).wait_for(timeout=15000)
        except Exception:
            (artifacts/'native-monitor-failure.json').write_text(json.dumps(dict(
                patch_status=external_response.status_code,patch_body=external_body,
                patch_revision=external.get('revision'),responses=native_responses,
                backend_status=client.get(native_path+'/status').json(),
                browser_notice=attached.locator('.edge-notice').inner_text(),
                native_connection=attached.locator('.edge-cad-source').inner_text()),indent=2))
            attached.screenshot(path=str(artifacts/'native-monitor-failure.png'),full_page=True)
            raise
        assert attached.get_by_role('button',name='Analyze revision',exact=True).is_disabled()
        attached.get_by_role('button',name='Load latest source view',exact=True).click()
        assert attached.get_by_role('button',name='Analyze revision',exact=True).is_enabled()
        # Results published by AutoCAD appear through lightweight status polling, without a browser job.
        native_job=client.post(native_path+'/jobs',json={'revision':external['revision'],'operation':'analyze'}).json()
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            state=client.get('/api/edge/jobs/'+native_job['id']).json()
            if state['status']=='complete':break
            assert state['status'] in ('queued','running'),state
            attached.wait_for_timeout(100)
        else:raise AssertionError('Externally submitted native analysis did not finish')
        attached.locator('.edge-recommendation').first.wait_for(timeout=15000)
        attached.screenshot(path=str(artifacts/'native-session-attachment.png'),full_page=True)
        assert client.get(native_path).json()['model']==external['model']
        assert not errors,errors
        browser.close()
        print(json.dumps({'result':'PASS','source_revision':current['revision'],'reviewed_export':True,'stale_approval_rejected':True,'preview_never_auto_syncs':True,'live_delta_analysis':True,'remote_and_local_fallback':True,'millimeter_dxf_exchange':True,'native_session_attachment':True,'external_revision_and_analysis_monitoring':True,'javascript_errors':errors}))
if __name__=='__main__':main()
