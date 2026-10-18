"""Run the public V3 regression examples and save separate review reports."""
from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import sys
import uuid
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipe_router.workflow import run_workflow
from pipe_router.review_export import write_review_xlsx
from run_demo import write_dxf, write_html


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'data/generated/challenge-checks')
    args = parser.parse_args()
    source = ROOT / 'examples/challenge'
    manifest = json.loads((source / 'manifest.json').read_text())
    folder = args.output / (datetime.now(timezone.utc).strftime('run-%Y%m%dT%H%M%S-') + uuid.uuid4().hex[:8])
    folder.mkdir(parents=True)
    outcomes = []
    for case in manifest['cases']:
        layout = json.loads((source / case['layout']).read_text())
        started = time.perf_counter()
        result = run_workflow(layout)
        elapsed = time.perf_counter() - started
        normalized = result['normalized_layout']
        findings = {f['id']: f['status'] for f in result['challenge_review']['original']['findings']}
        passed = result['status'] == case['expected_status'] and all(findings.get(k) == v for k,v in case['original_findings'].items())
        target = folder / case['id']
        target.mkdir()
        (target/'result.json').write_text(json.dumps({'layout':normalized,'result':result},indent=2,allow_nan=False))
        write_dxf(normalized,result,target/'route-centerlines.dxf')
        write_html(normalized,result,elapsed,target/'report.html')
        write_review_xlsx(normalized,result,target/'route-review.xlsx')
        outcomes.append({'case':case['id'],'passed':passed,'status':result['status'],'expected_status':case['expected_status'],'findings':findings})
        print(('PASS' if passed else 'FAIL')+' '+case['id']+' ('+result['status']+')',flush=True)
    (folder/'summary.json').write_text(json.dumps(outcomes,indent=2))
    print('Reports: '+str(folder.resolve()))
    return 0 if all(r['passed'] for r in outcomes) else 1

if __name__ == '__main__':
    raise SystemExit(main())
