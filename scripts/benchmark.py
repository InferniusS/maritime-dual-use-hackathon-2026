"""Repeatable actual geometry/search/validation benchmark. Run from repository root."""
import json,time,tracemalloc,platform,statistics,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from backend.scenarios import scenarios,get_scenario
from backend.optimizer import optimize
from backend.advanced import run_optimization
from backend.models import Model
from backend.validation import validate
rows=[]
for original in scenarios():
    samples=[]
    for _ in range(3):
        start=time.perf_counter();result=optimize(original);samples.append(time.perf_counter()-start)
    tracemalloc.start();result=optimize(original);_,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
    proposed=Model.model_validate(result['model']);start=time.perf_counter()
    checks=[r for c in result['candidates'] for r in validate(proposed,proposed,c['pipe_id'])]
    rows.append(dict(scenario=original.id,median_elapsed_s=statistics.median(samples),samples_s=samples,validation_elapsed_s=time.perf_counter()-start,python_peak_bytes=peak,statuses=[c['status'] for c in result['candidates']],expanded=sum(c.get('expanded',0) for c in result['candidates']),original_length_m=sum(c['before']['length'] for c in result['candidates']),proposed_length_m=sum(c['after']['length'] for c in result['candidates']),final_issues=len(result['issues']),failed_checks=sum(r['status']=='FAIL' for r in checks)))
advanced=run_optimization(get_scenario('congested'),alternatives=True,multiresolution=True)
output=dict(platform=platform.platform(),python=platform.python_version(),resolution_m=.5,clearance_m=.1,note='Python traced memory excludes native kernel/NumPy allocations; tracing perturbs runtime. Runtime medians use untraced runs.',scenarios=rows,advanced=dict(runtime_s=advanced['runtime_s'],attempts=len(advanced['exploration']),distinct_candidates=len(advanced['alternatives']),candidates=[{k:c[k] for k in ('name','metrics','nondominated','network_cost')} for c in advanced['alternatives']]))
Path('docs/v2/benchmark.json').write_text(json.dumps(output,indent=2));print(json.dumps(output,indent=2))
