#!/usr/bin/env python3
"""Generate and exercise fresh, invented CAD input through the plugin service.

The DXF files contain independently authored analytic ACIS fixtures, not exported
router output. Native AutoCAD creation scripts are included for a separate test
of AutoCAD's own authoring and DXF export. Python acceptance is not a substitute
for that Windows runtime test.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import sys
import threading
from urllib.request import Request, urlopen

import ezdxf
from ezdxf.acis import api as acis
from ezdxf.render import forms

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CASES = {
    "01-collision-repair": {"expected": "improved", "wall": ((2800, -600, -600), (3200, 600, 600))},
    "02-already-clear": {"expected": "unchanged", "wall": ((2800, 600, -600), (3200, 1000, 600))},
    "03-blocked-port": {"expected": "no_route", "wall": ((-200, -2000, -2000), (200, 2000, 2000))},
    "04-translated-block": {"expected": "improved", "wall": ((2800, -600, -600), (3200, 600, 600)),
                             "translation": (31700, -12200, 4300), "block": True},
}
DIAMETER_MM = 114.3
LENGTH_MM = 6000.0
CLEARANCE_MM = 50.8


def cylinder_sat(radius, length):
    """Closed analytic cylinder, local z mapped to world x.

    Explicit circle edges are shared by the lateral face and two planar caps.
    This is a synthetic parser fixture; no ACIS kernel round-trip is claimed.
    """
    r, h = radius, length
    records = [
        'body $-1 -1 $-1 $1 $-1 $2 #',
        'lump $-1 -1 $-1 $-1 $3 $0 #',
        'transform $-1 -1 0 1 0 0 0 1 1 0 0 0 0 0 1 rotate no_reflect no_shear #',
        'shell $-1 -1 $-1 $-1 $-1 $4 $-1 $1 #',
        'face $-1 -1 $-1 $18 $5 $3 $-1 $6 forward single #',
        'loop $-1 -1 $-1 $7 $8 $4 #',
        f'cone-surface $-1 -1 $-1 0 0 0 0 0 1 {r} 0 0 1 I I 0 1 {r} forward I I I I #',
        'loop $-1 -1 $-1 $-1 $9 $4 #',
        'coedge $-1 -1 $-1 $8 $8 $22 $10 reversed $5 $-1 #',
        'coedge $-1 -1 $-1 $9 $9 $23 $11 forward $7 $-1 #',
        'edge $-1 -1 $-1 $12 0 $12 6.283185307179586 $8 $14 forward @7 unknown #',
        'edge $-1 -1 $-1 $13 0 $13 6.283185307179586 $9 $15 forward @7 unknown #',
        'vertex $-1 -1 $-1 $10 2 $16 #',
        'vertex $-1 -1 $-1 $11 2 $17 #',
        f'ellipse-curve $-1 -1 $-1 0 0 0 0 0 1 {r} 0 0 1 I I #',
        f'ellipse-curve $-1 -1 $-1 0 0 {h} 0 0 1 {r} 0 0 1 I I #',
        f'point $-1 -1 $-1 {r} 0 0 #',
        f'point $-1 -1 $-1 {r} 0 {h} #',
        'face $-1 -1 $-1 $19 $20 $3 $-1 $24 reversed single #',
        'face $-1 -1 $-1 $-1 $21 $3 $-1 $25 forward single #',
        'loop $-1 -1 $-1 $-1 $22 $18 #',
        'loop $-1 -1 $-1 $-1 $23 $19 #',
        'coedge $-1 -1 $-1 $22 $22 $8 $10 forward $20 $-1 #',
        'coedge $-1 -1 $-1 $23 $23 $9 $11 reversed $21 $-1 #',
        'plane-surface $-1 -1 $-1 0 0 0 0 0 1 1 0 0 forward_v I I I I #',
        f'plane-surface $-1 -1 $-1 0 0 {h} 0 0 1 1 0 0 forward_v I I I I #',
        'End-of-ACIS-data',
    ]
    return ['21500 26 1 0 ', '@9 Synthetic @9 Synthetic @9 Synthetic ', '1 1e-7 1e-10 ', *records]


def _point(values):
    return ','.join(f'{v:g}' for v in values)


def make_case(destination: Path, name: str):
    """Write new DXF bytes and native creation script, returning known inputs."""
    spec = CASES[name]
    delta = spec.get('translation', (0, 0, 0))
    moved = lambda p: [a + b for a, b in zip(p, delta)]
    document = ezdxf.new('R2010')
    document.header['$INSUNITS'] = 4
    document.layers.new('SYNTHETIC_PIPE', dxfattribs={'color': 4})
    document.layers.new('SYNTHETIC_WALL', dxfattribs={'color': 2})
    space = document.blocks.new('SYNTHETIC_ROOM') if spec.get('block') else document.modelspace()
    pipe = space.add_3dsolid(dxfattribs={'layer': 'SYNTHETIC_PIPE'})
    pipe.sat = cylinder_sat(DIAMETER_MM / 2, LENGTH_MM)
    low, high = spec['wall']
    mesh = forms.cube(center=False)
    mesh.scale(*(high[i] - low[i] for i in range(3)))
    mesh.translate(*low)
    wall = space.add_3dsolid(dxfattribs={'layer': 'SYNTHETIC_WALL'})
    acis.export_dxf(wall, [acis.body_from_mesh(mesh)])
    placement = document.modelspace().add_blockref('SYNTHETIC_ROOM', delta) if spec.get('block') else None
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / f'{name}.dxf'
    document.saveas(path)
    # Native Windows authoring is an independent check of Autodesk-generated
    # geometry. Run SCRIPT in a fresh blank drawing, then MARITIMEREVIEW.
    script = [
        '_.UCS', '_World', 'INSUNITS', '4', 'OSMODE', '0', 'DYNMODE', '0',
        '_.-LAYER', '_Make', 'SYNTHETIC_PIPE', '_Color', '4', 'SYNTHETIC_PIPE', '',
        '_.CYLINDER', _point(moved((0, 0, 0))), f'{DIAMETER_MM / 2:g}',
        '_Axis', _point(moved((LENGTH_MM, 0, 0))),
        '_.-LAYER', '_Make', 'SYNTHETIC_WALL', '_Color', '2', 'SYNTHETIC_WALL', '',
        '_.BOX', _point(moved(low)), _point(moved((high[0], high[1], low[2]))), f'{high[2] - low[2]:g}',
        '_.VPOINT', '1,-1,1', '_.ZOOM', '_Extents', '',
    ]
    path.with_suffix('.scr').write_text('\n'.join(script), encoding='ascii')
    return {
        'name': name, 'path': str(path), 'expected': spec['expected'],
        'pipe_handle': pipe.dxf.handle, 'wall_handle': wall.dxf.handle,
        'selection_handle': placement.dxf.handle if placement else pipe.dxf.handle,
        'start': moved((0, 0, 0)), 'end': moved((LENGTH_MM, 0, 0)),
        'wall_min': moved(low), 'wall_max': moved(high),
        'diameter_mm': DIAMETER_MM, 'clearance_mm': CLEARANCE_MM,
        'synthetic': True, 'autocad_runtime_verified': False,
    }


def request(base_url, path, payload):
    raw = json.dumps(payload).encode()
    with urlopen(Request(base_url + path, raw, {'Content-Type': 'application/json'}), timeout=240) as response:
        return json.load(response)


def run_case(base_url, fixture):
    """Use the same HTTP snapshot, prepare and run operations as the plugin."""
    source = Path(fixture['path'])
    imported = request(base_url, '/api/autocad/inspect', {
        'filename': source.name, 'content_base64': base64.b64encode(source.read_bytes()).decode('ascii'),
        'source_units': 'mm', 'selected_handles': [fixture['selection_handle']],
        'document': {'name': source.name, 'fingerprint': hashlib.sha256(source.read_bytes()).hexdigest()},
    })
    assert len(imported['candidates']) == 1, imported['candidates']
    selected = imported['candidates'][0]
    prepared = request(base_url, '/api/prepare-cad-layout', {
        'cad_id': imported['cad_id'], 'candidate_id': selected['id'], 'system_type': 'pressurized',
        'clearance': CLEARANCE_MM, 'padding': 1500, 'grid_step': 250, 'rule_profile': 'fmg-v3',
    })
    layout = prepared['layout']
    # Proposal mode is the plugin's normal user setting. Extracted route,
    # diameter, obstacles and connection declarations are never changed here.
    layout['proposal'] = {'mode': 'rounded_pipe', 'bend_radius_mm': 304.8}
    checked = request(base_url, '/api/run', {'layout': layout})
    return {'fixture': fixture, 'imported': imported, 'layout': layout, 'checked': checked}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'data/generated/cad-acceptance')
    parser.add_argument('--generate-only', action='store_true')
    args = parser.parse_args()
    fixtures = [make_case(args.output, name) for name in CASES]
    if args.generate_only:
        print(json.dumps(fixtures, indent=2))
        return
    from run_web import make_server
    server = make_server(port=0, output=args.output / 'reports')
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        records = [run_case(f'http://127.0.0.1:{server.server_port}', fixture) for fixture in fixtures]
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
    summary = []
    for record in records:
        result = record['checked']['result']
        item = {'case': record['fixture']['name'], 'expected': record['fixture']['expected'],
                'actual': result['status'], 'assembly': result['assembly']['status'],
                'pass': result['status'] == record['fixture']['expected'],
                'synthetic': True, 'autocad_runtime_verified': False}
        summary.append(item)
        (args.output / f"{record['fixture']['name']}-result.json").write_text(json.dumps(record, indent=2))
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    if not all(item['pass'] for item in summary):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
