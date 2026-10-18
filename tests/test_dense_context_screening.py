"""Whole-drawing screening includes dense supplemental geometry without relaxing search limits."""
from copy import deepcopy

import pytest

from pipe_router import core
from pipe_router.model_context import apply_model_context
from pipe_router.drawing_scan import scan_drawing
from test_model_context import layout, context, compact_drawing, box
from test_drawing_review import drawing


def test_dense_context_preserves_all_shapes_and_still_rejects_bounded_search(layout, context):
    layout["obstacles"] = [box(str(n), (400,50,-10), (401,60,10)) for n in range(300)]
    context["additional_drawings"] = [compact_drawing([box('external-last', (500,-10,-10), (501,10,10))])]
    before = deepcopy((layout, context))
    result = apply_model_context(layout, context, _screening_batches=True)
    assert len(result['obstacles']) == 301
    assert result['obstacles'][-1]['id'] == 'context:drawing:structural:external-last'
    assert result['model_context']['requires_review']
    assert (layout, context) == before
    with pytest.raises(ValueError, match='at most'):
        apply_model_context(layout, context)


def test_context_only_collision_in_last_batch_is_detected(context):
    cad = drawing([[[0,0,0], [1000,0,0]]])
    context['allowed_spaces'] = [box('room', (-1000,-1000,-1000), (2000,1000,1000))]
    context['additional_drawings'] = [compact_drawing([
        *[box(str(n), (400,400,-10), (401,410,10)) for n in range(300)],
        box('external-last', (500,-10,-10), (501,10,10))])]
    result = scan_drawing(cad, model_context=context)
    row, = result['candidates']
    assert row['geometry_checked'], row['review_message']
    assert row['obstacle_count'] == 301
    assert row['obstacle_batches_checked'] == 2
    shock = next(f for f in row['rule_findings'] if f['id'] == 'shock_clearance')
    assert shock['status'] == 'fail'
    assert any(hit['obstacle_id'].endswith(':external-last') for hit in shock['measured']['collisions'])


def test_duplicate_ids_across_batches_and_bad_geometry_are_rejected(layout, context):
    layout['obstacles'] = [box(str(n)) for n in range(300)]
    layout['obstacles'][-1]['id'] = '0'
    with pytest.raises(ValueError, match='unique across'):
        apply_model_context(layout, context, _screening_batches=True)
    layout['obstacles'][-1]['id'] = '299'
    layout['obstacles'][-1]['min'][0] = float('nan')
    with pytest.raises(ValueError):
        apply_model_context(layout, context, _screening_batches=True)


def test_dense_scan_path_does_not_accept_connection_waivers_or_unknown_units(layout, context):
    layout['protected_endpoints'] = [{'endpoint': 'start'}]
    with pytest.raises(ValueError, match='no fixed-contact'):
        apply_model_context(layout, context, _screening_batches=True)
    layout.pop('protected_endpoints')
    layout['units'] = 'm'
    with pytest.raises(ValueError, match='millimetres'):
        apply_model_context(layout, context, _screening_batches=True)
