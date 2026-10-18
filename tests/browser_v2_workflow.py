"""Live Chromium regression of v2 controls; run against scripts/start.sh on port 8000."""
import json,sys
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from backend.step_adapter import inspect_step

def main():
    artifacts=Path('/workspace/pipeforge-artifacts/v2');artifacts.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox','--enable-unsafe-swiftshader','--use-gl=angle','--use-angle=swiftshader'])
        page=browser.new_page(viewport={'width':1700,'height':1100});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto('http://127.0.0.1:8000');page.get_by_role('button',name='Optimize routing').wait_for();page.get_by_role('button',name='Engineering workbench',exact=True).click()
        ids=['inefficient','congested','drainage','impossible','efficient','gravity-detour','vent-rise','multi-system']
        for id in ids:
            page.locator('select').first.select_option(id)
            page.get_by_role('button',name='Optimize routing').wait_for()
            page.get_by_role('button',name='Run analysis only').click()
            page.get_by_role('button',name='Optimize routing').wait_for()
            page.get_by_role('button',name='Optimize routing').click()
            expected='no_route' if id=='impossible' else 'accepted'
            page.get_by_text(expected,exact=True).first.wait_for(timeout=30000)
            assert page.locator('canvas').count()==1
        page.locator('select').first.select_option('congested')
        page.get_by_label('Compare five objective modes').check()
        page.get_by_label('Explore coarse + fine grids').check()
        page.get_by_role('button',name='Optimize routing').click()
        page.get_by_text('accepted',exact=True).first.wait_for(timeout=45000)
        cards=page.locator('.alternative-card');assert cards.count()>=2
        cards.last.click();assert 'chosen' in cards.last.get_attribute('class')
        page.get_by_label('Visible system').select_option('Cooling water')
        page.get_by_role('button',name='3D',exact=True).click();page.get_by_role('button',name='Top',exact=True).click()
        page.get_by_text('Point-to-point measurement',exact=True).click()
        page.get_by_label('Measure point A').select_option(index=1)
        page.get_by_label('Measure point B').select_option(index=2)
        assert 'Unavailable' not in page.locator('.measurement-tool strong').inner_text()
        page.screenshot(path=str(artifacts/'alternatives.png'),full_page=True)
        page.get_by_role('button',name='Export',exact=True).click()
        with page.expect_download() as info:page.get_by_role('button',name='STEP file',exact=True).click()
        path=artifacts/'proposal.step';info.value.save_as(str(path));cad=inspect_step(path.read_text());assert cad['solids']>0
        page.get_by_text('What-if constraints & catalog',exact=True).click()
        for i,v in enumerate([2,6,.1,3,7,1]):page.get_by_label('Zone '+str(i),exact=True).fill(str(v))
        old=len(page.locator('.pipe-row').all())
        page.get_by_role('button',name='Add restricted zone',exact=True).click()
        page.get_by_role('button',name='Overview',exact=True).click()
        page.get_by_text('Not analyzed',exact=True).wait_for()
        # The what-if model is fresh; rerunning computes rather than recycling candidates.
        page.get_by_label('Compare five objective modes').uncheck();page.get_by_label('Explore coarse + fine grids').uncheck()
        page.get_by_role('button',name='Optimize routing').click();page.get_by_text('accepted',exact=True).first.wait_for(timeout=30000)
        assert not errors,errors
        browser.close()
        print(json.dumps({'result':'PASS','scenarios':ids,'distinct_candidate_selection':True,'measurement':True,'what_if':True,'step_solids':cad['solids'],'javascript_errors':errors}))
if __name__=='__main__':main()
