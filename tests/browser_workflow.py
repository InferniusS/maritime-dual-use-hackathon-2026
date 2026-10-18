"""Optional actual Chromium smoke: pip install -r requirements-browser.txt first.
Run against the started app: python tests/browser_workflow.py.
"""
import json,os
from pathlib import Path
from playwright.sync_api import sync_playwright

def main():
    artifacts=Path('/workspace/pipeforge-artifacts');artifacts.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH','/usr/bin/chromium'),headless=True,args=['--no-sandbox','--enable-unsafe-swiftshader','--use-gl=angle','--use-angle=swiftshader'])
        page=browser.new_page(viewport={'width':1600,'height':1050});errors=[]
        page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto('http://127.0.0.1:8000')
        page.get_by_role('button',name='Optimize routing').wait_for()
        page.wait_for_function("document.querySelector('canvas') !== null")
        page.get_by_role('button',name='Run analysis only').click()
        page.get_by_role('button',name='Optimize routing').wait_for()
        page.get_by_role('button',name='Optimize routing').click()
        page.get_by_text('accepted',exact=True).wait_for(timeout=20000)
        page.get_by_role('button',name='Coordinates',exact=True).click()
        assert page.locator('.coordinates tbody tr').count()==2,'Optimized straight fixture must have two endpoints'
        page.get_by_role('button',name='Overview',exact=True).click()
        page.get_by_text('No clashes',exact=True).wait_for()
        page.screenshot(path=str(artifacts/'optimized.png'),full_page=True)
        for format in ('json','dxf','csv','report'):
            page.get_by_role('button',name='Export',exact=True).click()
            name='REPORT report' if format=='report' else format.upper()+' file'
            with page.expect_download() as download:page.get_by_role('button',name=name,exact=True).click()
            download.value.save_as(str(artifacts/download.value.suggested_filename))
        page.locator('select').first.select_option('congested')
        page.get_by_role('button',name='Optimize routing').wait_for()
        page.get_by_role('button',name='Run analysis only').click()
        page.get_by_text('Insufficient separation from equipment',exact=True).first.wait_for()
        page.get_by_role('button',name='Optimize routing').click()
        page.get_by_text('accepted',exact=True).wait_for(timeout=20000)
        page.screenshot(path=str(artifacts/'congested.png'),full_page=True)
        page.locator('select').first.select_option('impossible')
        page.get_by_role('button',name='Optimize routing').click()
        page.get_by_text('no_route',exact=True).wait_for(timeout=20000)
        assert not errors,errors
        browser.close()
        print(json.dumps({'browser':'Chromium','javascript_errors':errors,'scenarios':['inefficient','congested','impossible'],'downloads':['json','dxf','csv','report'],'result':'PASS'}))
if __name__=='__main__':main()
