"""Read-only git archive comparison: same runtime, fixtures and settings, three runs each."""
import io,json,subprocess,sys,tarfile,tempfile,platform
from pathlib import Path
root=Path(__file__).resolve().parent.parent
baseline='afb9626befed67be329e58e502ead1aafff97fd6'
worker='''import json,time,statistics
from backend.scenarios import get_scenario
from backend.optimizer import optimize
rows=[]
for id in ['inefficient','congested','drainage','impossible','efficient']:
 samples=[]
 for _ in range(3):
  model=get_scenario(id);start=time.perf_counter();result=optimize(model);samples.append(time.perf_counter()-start)
 c=result['candidates'][0]
 rows.append(dict(scenario=id,samples_s=samples,median_s=statistics.median(samples),status=c['status'],length_m=c['after']['length'],bends=c['after']['bends'],expanded=c.get('expanded',0)))
print(json.dumps(rows))'''
archive=subprocess.run(['git','archive',baseline,'backend'],cwd=root,capture_output=True,check=True).stdout
with tempfile.TemporaryDirectory(prefix='pipeforge-v1-reference-') as directory:
 with tarfile.open(fileobj=io.BytesIO(archive)) as files:files.extractall(directory,filter='data')
 old=json.loads(subprocess.run([sys.executable,'-c',worker],cwd=directory,capture_output=True,text=True,check=True).stdout)
new=json.loads(subprocess.run([sys.executable,'-c',worker],cwd=root,capture_output=True,text=True,check=True).stdout)
rows=[]
for a,b in zip(old,new):
 rows.append(dict(scenario=a['scenario'],baseline=a,v2=b,observed_runtime_ratio=a['median_s']/b['median_s'],same_status_and_route_metrics=(a['status'],a['length_m'],a['bends'])==(b['status'],b['length_m'],b['bends'])))
result=dict(baseline_commit=baseline,python=platform.python_version(),platform=platform.platform(),method='Sequential separate subprocesses, same pinned runtime and unchanged fixtures, 3 untraced runs each at 0.5m resolution and 0.1m clearance. Runtime ratios are observations, not guaranteed speedups.',results=rows)
(root/'docs/v2/comparison.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
