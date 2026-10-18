"""A refused replay must not inherit reports from a previous successful run."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import check_real_routes as cli


class FrozenDatetime:
    @classmethod
    def now(cls, tz):
        assert tz is timezone.utc
        return datetime(2026, 10, 17, 12, 34, 56, 123456, tzinfo=timezone.utc)


def simple_layout():
    return {
        'name': 'CLI output isolation fixture', 'units': 'mm',
        'provenance': 'synthetic', 'system_type': 'pressurized',
        'bounds': {'min': [0, 0, 0], 'max': [100, 100, 100]},
        'obstacles': [],
        'pipe': {'start': [10, 10, 10], 'end': [90, 10, 10], 'diameter': 2,
                 'original_route': [[10, 10, 10], [90, 10, 10]]},
        'rules': {'clearance': 1, 'grid_step': 20, 'bend_penalty': 0},
        'assumptions': [],
    }


def test_repeated_success_then_refusal_has_distinct_run_folders(tmp_path, monkeypatch, capsys):
    drawing = tmp_path / 'source.dxf'
    drawing.write_bytes(b'unchanged source drawing fixture')
    before = drawing.read_bytes()
    output = tmp_path / 'replays'
    monkeypatch.setattr(sys, 'argv', ['check_real_routes.py', str(drawing),
                        '--candidate', 'fixture', '--output', str(output)])
    monkeypatch.setattr(cli, 'datetime', FrozenDatetime)
    monkeypatch.setattr(cli, 'inspect_cad', lambda *_args, **_kwargs: {
        'source_sha256': hashlib.sha256(before).hexdigest()})
    monkeypatch.setattr(cli, 'prepare_cad_layout', lambda *_args, **_kwargs: simple_layout())
    monkeypatch.setattr(cli, 'write_dxf', lambda _layout, _result, path: path.write_text('successful route'))
    monkeypatch.setattr(cli, 'write_html', lambda _layout, _result, _elapsed, path: path.write_text('successful report'))

    cli.main()
    first, = output.iterdir()
    first_summary = json.loads((first / 'summary.json').read_text())
    first_report = first / first_summary['cases'][0]['case_folder'] / 'report.html'
    first_bytes = first_report.read_bytes()
    assert first_summary['cases'][0]['status'] == 'unchanged'
    assert first_summary['created_utc'] == '2026-10-17T12:34:56.123456Z'
    assert first_summary['run_folder'] == str(first.resolve())

    def refuse(*_args, **_kwargs):
        raise ValueError('The path does not exit the selected connector within the 250 mm stub limit')
    monkeypatch.setattr(cli, 'prepare_cad_layout', refuse)
    cli.main()
    folders = set(output.iterdir())
    assert len(folders) == 2
    second, = folders - {first}
    second_summary = json.loads((second / 'summary.json').read_text())
    case, = second_summary['cases']
    assert '250 mm stub limit' in case['error']
    assert not (second / case['case_folder'] / 'report.html').exists()
    assert not (second / case['case_folder'] / 'result.json').exists()
    assert first_report.read_bytes() == first_bytes
    assert first.name.startswith('run-20261017T123456.123456Z-')
    assert second.name.startswith('run-20261017T123456.123456Z-')
    assert second.name != first.name
    assert drawing.read_bytes() == before
    assert not (output / 'summary.json').exists()
    assert str(second.resolve()) in capsys.readouterr().out


def test_source_guard_still_applies_to_output_parent(tmp_path, monkeypatch):
    output = tmp_path / 'replays'
    drawing = output / 'nested' / 'source.dxf'
    drawing.parent.mkdir(parents=True)
    drawing.write_bytes(b'source')
    monkeypatch.setattr(sys, 'argv', ['check_real_routes.py', str(drawing),
                        '--candidate', 'fixture', '--output', str(output)])
    calls = []
    monkeypatch.setattr(cli, 'inspect_cad', lambda *_args, **_kwargs: calls.append('unexpected import'))
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    assert calls == []
    assert drawing.read_bytes() == b'source'
    assert not list(output.glob('run-*'))
