"""Registration and service contracts required by the native coordinated client.

This does not execute AutoCAD. It prevents duplicate native command registration
and exercises the accepted payload contract with independently generated CAD in
all three supported drawing units.
"""
import base64
from collections import defaultdict
import hashlib
from pathlib import Path
import re
import sys
import time

import ezdxf
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from check_cad_acceptance import cylinder_sat
from test_autocad import autocad_workspace
from test_web import json_request

def test_autocad_public_command_names_are_unambiguous():
    owners = defaultdict(list)
    for source in (ROOT / 'autocad-plugin').glob('*.cs'):
        for name in re.findall(r'\[CommandMethod\("([^"]+)"', source.read_text()):
            owners[name.upper()].append(source.name)
    duplicates = {name: files for name, files in owners.items() if len(files) > 1}
    assert not duplicates, f'AutoCAD cannot unambiguously dispatch duplicate commands: {duplicates}'
    assert {'MARITIMESCAN', 'MARITIMEFIND', 'MARITIMEDIMENSIONS', 'MARITIMEOPTIMIZE', 'MARITIMELINEOPTIMIZE', 'MARITIMEREPORT'} <= owners.keys()


@pytest.mark.parametrize('source_units,scale', [('mm', 1), ('in', 25.4), ('m', 1000)])
def test_accepted_async_result_is_native_preview_ready_without_unit_drift(tmp_path, autocad_workspace, source_units, scale):
    source = tmp_path / f'native-contract-{source_units}.dxf'
    document = ezdxf.new('R2010')
    document.header['$INSUNITS'] = 0  # The native prompt explicitly supplies units.
    solid = document.modelspace().add_3dsolid()
    source_length = 10 if source_units == 'm' else 1000
    solid.sat = cylinder_sat(2, source_length)
    document.saveas(source)
    source_bytes = source.read_bytes()
    identity = {'name': source.name, 'fingerprint': f'units-contract-{source_units}'}
    status, imported = json_request(autocad_workspace, 'POST', '/api/autocad/review', {
        'filename': source.name, 'content_base64': base64.b64encode(source_bytes).decode(),
        'source_units': source_units, 'document': identity, 'system_type': 'pressurized',
        'rule_profile': 'fmg-v3', 'clearance': 50.8, 'padding': 1500,
    })
    assert status == 200, imported
    candidate, = imported['candidates']
    section = {'candidate_id': candidate['id'], 'system_type': 'pressurized',
               'clearance': 50.8, 'padding': 1500, 'grid_step': 250,
               'rule_profile': 'fmg-v3', 'reverse_flow': False, 'min_slope': .01,
               'bend_radius_mm': max(304.8, 6 * scale)}
    status, preview = json_request(autocad_workspace, 'POST', '/api/prepare-cad-layout', {
        **section, 'cad_id': imported['cad_id'],
    })
    assert status == 200, preview
    expected = preview['layout']
    status, pending = json_request(autocad_workspace, 'POST', '/api/autocad/optimize', {
        'cad_id': imported['cad_id'], 'source_sha256': imported['source_sha256'],
        'document': identity, 'sections': [section],
    })
    assert status == 202, pending
    assert re.fullmatch(r'[a-f0-9]{32}', pending['job_id'])
    assert pending['status_url'] == f"/api/system-jobs/{pending['job_id']}"
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        status, reply = json_request(autocad_workspace, 'GET', pending['status_url'])
        assert status == 200, reply
        if reply['status'] in ('complete', 'failed'):
            break
        time.sleep(.02)
    assert reply['status'] == 'complete', reply
    assert reply['api_version'] == 1
    assert reply['cad_id'] == imported['cad_id']
    assert reply['source_sha256'] == hashlib.sha256(source_bytes).hexdigest()
    assert reply['document'] == identity
    assert re.fullmatch(r'/downloads/[a-f0-9]{32}/report\.html', reply['exports']['report'])
    assert reply['summary']['complete_system_verified'] is False
    row, = reply['summary']['routes']
    assert row['candidate_id'] == candidate['id']
    assert row['status'] == 'unchanged', row['message']
    assert row['coordination']['accepted'] is True
    assert row['coordination']['original_geometry_revalidated'] is True
    assert row['coordination']['pair_clearance_revalidated'] is True
    layout, result = row['layout'], row['result']
    assert layout['units'] == result['units'] == 'mm'
    assert layout['cad_context']['document'] == identity
    assert layout['cad_context']['source_snapshot_sha256'] == imported['source_sha256']
    assert layout['cad_context']['source_handles'] == expected['cad_context']['source_handles'] == [solid.dxf.handle]
    assert layout['pipe']['diameter'] == expected['pipe']['diameter'] == 4 * scale
    assert result['original']['points'] == expected['pipe']['original_route']
    assert result['candidate']['valid']
    assembly = result['assembly']
    assert assembly['status'] == 'ready' and assembly['units'] == 'mm'
    assert assembly['manufacturing_assessment']['blocking_failure_count'] == 0
    assert assembly['outer_diameter_mm'] == 4 * scale
    for points in (result['candidate']['points'], assembly['centerline_points']):
        assert points[0] == expected['pipe']['start']
        assert points[-1] == expected['pipe']['end']
        # The C# mesh writer divides returned mm by the drawing's source scale.
        assert [v / scale for v in points[-1]] == pytest.approx([source_length, 0, 0])
    for key in ('meshes', 'reference_meshes'):
        assert assembly[key]
        assert sum(len(mesh['vertices']) for mesh in assembly[key]) < 100000
        assert sum(len(mesh['faces']) for mesh in assembly[key]) < 200000
        for mesh in assembly[key]:
            assert isinstance(mesh['id'], str) and isinstance(mesh['kind'], str)
            for face in mesh['faces']:
                assert 3 <= len(face) <= 4
                assert len(set(face)) == len(face)
                assert all(isinstance(index, int) and 0 <= index < len(mesh['vertices']) for index in face)
    assert source.read_bytes() == source_bytes
