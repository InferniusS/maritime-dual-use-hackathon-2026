"""Work limits stop unsafe allocation and incomplete engineering claims."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading

import pytest
from pydantic import ValidationError

from backend.models import Bounds, Model, Pipe
from backend.scenarios import get_scenario
from backend.work_limits import (DEFAULT_LIMITS, WorkLimits, WorkLimitExceeded, WorkCancelled,
    append_issue, check_expanded, check_result, checkpoint, current_limits, preflight_grid,
    preflight_model, remaining_seconds, work_scope)


def test_huge_finite_bounds_are_rejected_before_grid_allocation():
    model = get_scenario('congested')
    model.bounds.max = (1e12, 8., 4.)
    with pytest.raises(WorkLimitExceeded, match='axis'):
        preflight_grid(model, .2, .3)


def test_combined_grid_budget_and_configured_state_limit():
    model = get_scenario('congested')
    with work_scope(dict(max_axis_points=100, max_grid_cells=100, max_expanded=2)):
        with pytest.raises(WorkLimitExceeded, match='grid'):
            preflight_grid(model, .2, .3)
        check_expanded(1)
        with pytest.raises(WorkLimitExceeded, match='expanded'):
            check_expanded(2)


def test_aggregate_segment_work_rejected_before_billion_pair_loop():
    pipes = [Pipe(id=f'p-{i}', system_id='water', points=[(float(j), float(i), 1.) for j in range(1000)]) for i in range(100)]
    model = Model(id='dense', name='Dense valid schema', bounds=Bounds(min=(-1., -1., 0.), max=(1001., 101., 3.)), pipes=pipes)
    with pytest.raises(WorkLimitExceeded, match='pair tests'):
        preflight_model(model)


def test_representative_forty_routes_pass_default_preflight():
    pipes = [Pipe(id=f'p-{i}', system_id='water', points=[(1., 6. * i + 3., 1.), (2., 6. * i + 3., 1.), (3., 6. * i + 3., 1.), (4., 6. * i + 3., 1.)]) for i in range(40)]
    model = Model(id='forty', name='Forty disjoint routes', bounds=Bounds(min=(0., 0., 0.), max=(8., 250., 4.)), pipes=pipes)
    with work_scope():
        report = preflight_model(model)
        assert report['segments'] == 120
        assert report['estimated_pair_tests'] < DEFAULT_LIMITS.max_pair_tests


def test_cancelled_scope_stops_before_body_or_grid():
    event = threading.Event(); event.set()
    ran = []
    with pytest.raises(WorkCancelled):
        with work_scope(cancel=event):
            ran.append(True)
    assert not ran


def test_deadline_checked_cooperatively_and_nested_scope_cannot_extend(monkeypatch):
    clock = [100.]
    monkeypatch.setattr('backend.work_limits.time.monotonic', lambda: clock[0])
    with work_scope(dict(deadline_s=1.)):
        clock[0] = 100.75
        with work_scope(dict(deadline_s=100.)):
            assert remaining_seconds() == pytest.approx(.25)
            clock[0] = 101.1
            with pytest.raises(WorkLimitExceeded, match='deadline'):
                checkpoint('inside analysis')
            clock[0] = 100.9


def test_context_isolation_across_parallel_requests():
    barrier = threading.Barrier(2)
    def read_limit(value):
        with work_scope(dict(max_issues=value)):
            barrier.wait(timeout=2)
            return current_limits().max_issues
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(read_limit, value) for value in (3, 8)]
        assert [future.result(timeout=3) for future in futures] == [3, 8]
    assert current_limits() == DEFAULT_LIMITS


def test_findings_are_capped_before_append_without_fake_truncation():
    issues = []
    with work_scope(dict(max_issues=1)):
        append_issue(issues, {'id': 'first'})
        with pytest.raises(WorkLimitExceeded, match='unresolved'):
            append_issue(issues, {'id': 'second'})
    assert issues == [{'id': 'first'}]


def test_result_item_depth_and_byte_limits():
    with work_scope(dict(max_result_items=100, max_response_bytes=1024)):
        assert check_result({'ok': True}) == len(json.dumps({'ok': True}, separators=(',', ':')).encode())
        with pytest.raises(WorkLimitExceeded, match='items'):
            check_result({'many': list(range(101))})
        with pytest.raises(WorkLimitExceeded, match='bytes'):
            check_result({'large': 'x' * 1024})
        with pytest.raises(WorkLimitExceeded, match='findings'):
            with work_scope(dict(max_issues=1)):
                check_result({'issues': [{'id': 'a'}, {'id': 'b'}]})
    nested = None
    for _ in range(65): nested = [nested]
    with pytest.raises(WorkLimitExceeded, match='nesting'):
        check_result(nested)


@pytest.mark.parametrize('options', [dict(deadline_s=121), dict(deadline_s=float('inf')), dict(max_axis_points=10**9), dict(max_pair_tests=10**12), dict(max_response_bytes=0), dict(max_issues=True), dict(unknown_budget=1)])
def test_limits_remain_bounded_and_strict(options):
    with pytest.raises(ValidationError): WorkLimits.model_validate(options)


def test_catalog_size_is_bounded_before_geometry_processing():
    from pydantic import ValidationError
    from backend.models import Fitting
    with pytest.raises(ValidationError):
        Pipe(id='catalog',system_id='water',points=[(0,0,0),(1,0,0)],fittings=[Fitting() for _ in range(101)])


def test_analysis_caps_aggregate_findings_before_processing_more_pipes(monkeypatch):
    from backend import validation
    model=get_scenario('efficient')
    extra=model.pipes[0].model_copy(deep=True);extra.id='third';model.pipes.append(extra)
    seen=[]
    def record(model,pipe,clearance):
        seen.append(pipe.id)
        return {'issues':[{'id':pipe.id+str(i),'type':'clearance'} for i in range(2)],'metrics':{}}
    monkeypatch.setattr(validation,'analyze_pipe',record)
    with work_scope({'max_issues':3}):
        with pytest.raises(WorkLimitExceeded,match='findings'):validation.analyze(model)
    assert seen==[pipe.id for pipe in model.pipes[:2]]
