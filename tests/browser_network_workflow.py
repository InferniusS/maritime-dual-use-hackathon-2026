"""Connected-network browser regression against a running local workspace.

Only synthetic scenarios are loaded. Exports are local review packages, and no
CAD source file is written or changed. Also works on scripts/run_web.py.
"""
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright, expect


def main():
    base = os.environ.get('PIPEFORGE_UI_BASE_URL','http://127.0.0.1:8000')
    artifacts = Path(os.environ.get('PIPEFORGE_UI_ARTIFACTS','/workspace/pipeforge-artifacts/edge-4-ui'))
    artifacts.mkdir(parents=True,exist_ok=True)
    summary = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        page = browser.new_page(viewport={'width':1500,'height':1050},device_scale_factor=1)
        errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(base+'/connected',wait_until='networkidle')
        expect(page.locator('#network-summary')).to_contain_text('nodes')
        page.select_option('#example','a-already-good')
        page.click('#load-example')
        expect(page.locator('#network-json')).to_have_value(__import__('re').compile(r'a-already-good|already-good',__import__('re').I))
        page.click('#optimize')
        expect(page.locator('#outcome')).to_have_text('NO IMPROVEMENT FOUND',timeout=120000)
        expect(page.locator('#progress')).to_contain_text('Completed')
        assert page.locator('#checks .check').count() >= 20
        expect(page.locator('#export')).to_be_disabled()
        page.check('#approve')
        expect(page.locator('#export')).to_be_enabled()
        page.click('#export')
        expect(page.locator('#downloads a')).to_have_count(8,timeout=30000)
        assert any(link.endswith('.xlsx') for link in page.locator('#downloads a').evaluate_all('(nodes)=>nodes.map(n=>n.getAttribute("href"))'))
        summary['already_good']={'status':page.locator('#outcome').inner_text(),'checks':page.locator('#checks .check').count(),'reviewed_exports':8}
        # Editing source metadata invalidates any reviewed result on the page.
        page.get_by_text('Edit network and connection metadata',exact=True).click()
        original=json.loads(page.input_value('#network-json'))
        original['drawing']['revision']='browser-revision-two'
        page.locator('#network-json').fill(json.dumps(original))
        page.click('#apply-json')
        expect(page.locator('#source-info')).to_contain_text('browser-revision-two')
        expect(page.locator('#export')).to_be_disabled()
        expect(page.locator('#downloads a')).to_have_count(0)
        summary['revision_invalidation']=True
        # Designer resolves a geometric ambiguity explicitly. No proximity join.
        fixture=json.loads(page.input_value('#network-json'))
        first=fixture['spans'][0]
        midpoint=[(a+b)/2 for a,b in zip(first['points'][0],first['points'][-1])]
        template=fixture['nodes'][0]
        fixture['nodes']=[dict(template,id='one',type='equipment_port',position=first['points'][0]),dict(template,id='near-a',type='endpoint',position=midpoint),dict(template,id='near-b',type='endpoint',position=midpoint),dict(template,id='two',type='equipment_port',position=first['points'][-1])]
        fixture['spans']=[dict(first,id='left',from_node='one',to_node='near-a',points=[first['points'][0],midpoint]),dict(first,id='right',from_node='near-b',to_node='two',points=[midpoint,first['points'][-1]])]
        fixture['unresolved_connections']=[{'id':'browser-near','endpoint_ids':['near-a','near-b'],'reason':'Synthetic ambiguous endpoints','status':'unresolved'}]
        page.locator('#network-json').fill(json.dumps(fixture));page.click('#apply-json')
        expect(page.locator('#network-summary')).to_contain_text('4 nodes')
        expect(page.locator('#connections')).to_contain_text('Unresolved browser-near')
        page.get_by_role('button',name='Confirm selected connection').click()
        expect(page.locator('#network-summary')).to_contain_text('3 nodes')
        expect(page.locator('#connections .connection')).to_have_count(0)
        summary['explicit_topology_confirmation']=True
        page.select_option('#example','c-connected-branch');page.click('#load-example')
        expect(page.locator('#network-name')).to_contain_text('Connected branch')
        page.wait_for_function("JSON.parse(document.getElementById('network-json').value).id === 'c-connected-branch'")
        expect(page.locator('#optimize')).to_be_enabled()
        page.click('#optimize')
        expect(page.locator('#progress')).to_contain_text('Completed',timeout=120000)
        summary['connected_network']={'status':page.locator('#outcome').inner_text(),'metrics':page.locator('#metrics').inner_text()}
        assert summary['connected_network']['status'] == 'IMPROVED',summary
        assert page.locator('#network-plot .scatterlayer').count() > 0 or page.locator('#network-plot canvas').count() > 0
        # Start an uncached bounded search and request cancellation through the UI.
        # A new grid spacing avoids relying on an already warmed local solve cache.
        cancellable=json.loads(page.input_value('#network-json'))
        cancellable['search']['grid_step_mm']=333
        page.locator('#network-json').fill(json.dumps(cancellable));page.click('#apply-json')
        expect(page.locator('#optimize')).to_be_enabled()
        with page.expect_response(lambda r:r.url.endswith('/api/network/jobs')):
            page.click('#optimize')
        page.click('#cancel')
        expect(page.locator('#progress')).to_contain_text('cancelled',timeout=120000)
        summary['cooperative_cancellation']=True
        page.select_option('#example','c-connected-branch');page.click('#load-example')
        expect(page.locator('#optimize')).to_be_enabled();page.click('#optimize')
        expect(page.locator('#progress')).to_contain_text('Completed',timeout=120000)
        page.locator('summary').filter(has_text='Exact original').click()
        assert page.locator('#coordinates table').count() == 2
        page.screenshot(path=str(artifacts/'connected-network.png'),full_page=True)
        page.select_option('#check-filter','UNVERIFIED')
        assert all(value=='UNVERIFIED' for value in page.locator('#checks .badge').all_inner_texts())
        summary['console_errors']=errors
        assert not errors,errors
        (artifacts/'browser-summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
        print(json.dumps(summary,indent=2))
        browser.close()


if __name__=='__main__':main()
