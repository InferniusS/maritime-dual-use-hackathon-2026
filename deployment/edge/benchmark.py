"""Compare local CPU and independently hosted HTTP CPU execution without a speedup claim."""
import argparse
import json
from pathlib import Path
import platform
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.edge_remote import execute_remote_or_local, probe_remote, _client_models
from backend.scenarios import get_scenario

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--endpoint', default='http://127.0.0.1:8010')
parser.add_argument('--runs', type=int, default=3)
parser.add_argument('--output', type=Path)
args = parser.parse_args()
if not 1 <= args.runs <= 20:
    parser.error('--runs must be between 1 and 20')
health = probe_remote(args.endpoint)
if not health['available']:
    raise SystemExit('Start the CPU worker before benchmarking: ' + health.get('error', 'unavailable'))
rows = []
for scenario, operation, options in [('inefficient', 'analyze', {}), ('inefficient', 'optimize', {}), ('congested', 'optimize', {'resolution': 1.})]:
    _client_models.pop(args.endpoint.rstrip('/'), None)
    samples = []
    for run in range(args.runs):
        model = get_scenario(scenario)
        local = execute_remote_or_local(operation, model, options, {'enabled': False})
        remote = execute_remote_or_local(operation, model, options, {'enabled': True, 'endpoint': args.endpoint, 'timeout_s': 30, 'source_revision': 'benchmark:1'})
        if remote['execution']['location'] != 'remote':
            raise SystemExit('Remote benchmark fell back: ' + str(remote['execution']['local_fallback_reason']))
        samples.append(dict(local_total_ms=local['execution']['total_ms'], **remote['execution']))
    keys = ['local_total_ms', 'total_ms', 'serialization_ms', 'roundtrip_ms', 'remote_compute_ms', 'decode_ms', 'local_validation_ms', 'network_and_overhead_ms', 'request_bytes', 'response_bytes']
    rows.append(dict(scenario=scenario, operation=operation, options=options, runs=args.runs,
                     medians={key: statistics.median(sample[key] for sample in samples) for key in keys}, samples=samples))
result = dict(scope='Local CPU versus HTTP worker; inspect endpoint and architectures before interpreting. Loopback is not a device or LAN benchmark.',
              client=dict(architecture=platform.machine(), system=platform.system(), python=platform.python_version()), worker=health, measurements=rows)
payload = json.dumps(result, indent=2) + '\n'
if args.output:
    args.output.write_text(payload, encoding='utf-8')
print(payload)
