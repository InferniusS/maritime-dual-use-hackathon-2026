"""Run the five labeled 3D pipe proposal cases and save review artifacts."""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import json
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from pipe_router.workflow import run_workflow
from scripts.run_demo import write_dxf,write_html
from pipe_router.review_export import write_review_xlsx

CASES=[('01-congested-room','improved','ready'),('02-collision-repair','improved','ready'),
       ('03-blocked-route','no_route','blocked'),('04-elevation-repair','improved','ready'),
       ('05-already-good','unchanged','ready')]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,help='New output directory for the reports')
    args=parser.parse_args()
    folder=args.output or ROOT/'data/generated/assembly-checks'/datetime.now(timezone.utc).strftime('run-%Y%m%dT%H%M%S.%fZ')
    folder.mkdir(parents=True,exist_ok=False)
    summary=[]
    for name,expected_status,expected_assembly in CASES:
        layout=json.loads((ROOT/'examples/assembly'/f'{name}.json').read_text())
        started=time.perf_counter();result=run_workflow(layout);elapsed=time.perf_counter()-started
        result['runtime_seconds']=elapsed
        normalized=result['normalized_layout'];target=folder/name;target.mkdir()
        (target/'result.json').write_text(json.dumps({'generated_at':datetime.now(timezone.utc).isoformat(),'input_layout':layout,'layout':normalized,'result':result},indent=2,allow_nan=False))
        write_dxf(normalized,result,target/'route-centerlines.dxf')
        write_review_xlsx(normalized,result,target/'route-review.xlsx')
        write_html(normalized,result,elapsed,target/'report.html')
        a=result['assembly'];passed=result['status']==expected_status and a['status']==expected_assembly
        if name=='01-congested-room':
            passed=passed and a['metrics']['bend_count']<a['baseline_metrics']['bend_count'] and a['metrics']['centerline_length_mm']<a['baseline_metrics']['centerline_length_mm']
        if name=='04-elevation-repair':passed=passed and a['metrics']['elevation_runs']>0
        record={'case':name,'pass':passed,'status':result['status'],'assembly':a['status'],
                'seconds':round(elapsed,3),'metrics':a['metrics'],'baseline_metrics':a.get('baseline_metrics'),
                'synthetic':True,'report':str(target/'report.html')}
        summary.append(record)
        print(f"{'PASS' if passed else 'FAIL'} {name}: {result['status']}; rounded proposal {a['status']}; {elapsed:.3f} s",flush=True)
    (folder/'summary.json').write_text(json.dumps(summary,indent=2))
    print(f'\nReports: {folder}\nThese are synthetic geometry tests, not complete engineering or AutoCAD runtime approval.')
    return 0 if all(record['pass'] for record in summary) else 1

if __name__=='__main__':raise SystemExit(main())
