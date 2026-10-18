"""Explain the active finite search area without changing it or its obstacles.

This is evidence about the supplied checker representation, not actual ship
containment. A separating box is only diagnosed when its inflated box spans
both transverse dimensions and separates the fixed endpoints. Capsule envelopes
cannot be replaced by their broad-phase boxes for this diagnosis.
"""
from copy import deepcopy

from . import core


def review_search_region(layout, result):
    context = layout.get('cad_context')
    context = context if isinstance(context, dict) else {}
    bounds = layout['bounds']
    diameter = layout['pipe']['diameter']
    clearance = layout['rules']['clearance']
    margin = diameter / 2 + clearance
    start, end = layout['pipe']['start'], layout['pipe']['end']
    interior_low = [v + margin for v in bounds['min']]
    interior_high = [v - margin for v in bounds['max']]
    exempt = {identifier for record in layout.get('protected_endpoints', [])
              for identifier in record['obstacle_ids']}
    sources = {row.get('id', row.get('obstacle_id')): row
               for row in context.get('obstacle_sources', [])}
    barriers = []
    for obstacle in layout['obstacles']:
        if 'capsules' in obstacle or obstacle['id'] in exempt:
            continue
        low = [v - margin for v in obstacle['min']]
        high = [v + margin for v in obstacle['max']]
        for axis in range(3):
            left, right = sorted((start[axis], end[axis]))
            if not (left < low[axis] - core.EPSILON and right > high[axis] + core.EPSILON):
                continue
            cross_axes = [i for i in range(3) if i != axis]
            if all(low[i] <= interior_low[i] + core.EPSILON and
                   high[i] >= interior_high[i] - core.EPSILON for i in cross_axes):
                barriers.append({'obstacle_id': obstacle['id'], 'axis': 'XYZ'[axis],
                    'source_handle': sources.get(obstacle['id'], {}).get('source_handle'),
                    'message': 'This represented box, with pipe radius and clearance, fills the local search cross-section between the endpoints.'})
    accepted = result.get('assembly', {}).get('status') == 'ready'
    blocked = result.get('centerline_search', result.get('search', {})).get('blocked_endpoints', [])
    connection = result.get('connection_selection_review', {})
    if accepted:
        kind, message, action = ('accepted', 'A rounded proposal passed the implemented checks in this search area.',
                                'Review source connections, included surroundings and remaining engineering requirements.')
    elif connection.get('status') == 'blocked':
        kind, message, action = ('fixed_connection_conflict', connection['message'],
                                'Inspect the listed fixed endpoint, unchanged stub and movable anchor conflicts. Padding cannot resolve a collision on an unchanged connection; do not approve unrelated objects to force a result.')
    elif blocked:
        kind, message, action = ('fixed_endpoint_contact', 'A fixed endpoint touches an obstacle envelope or its required clearance.',
                                'Inspect the listed endpoint contacts. Increasing padding does not remove a fixed endpoint conflict; only verified connections may receive bounded fixed-stub treatment.')
    elif barriers:
        kind, message, action = ('represented_barrier_spans_search_region',
            'A represented obstacle fills the available local search cross-section between the fixed endpoints.',
            'Review a larger local padding and rescan so newly nearby objects are included. Keep clearance unchanged. Search padding is not permission to leave required ship spaces; conservative obstacle boxes may also need source review.')
    else:
        kind, message, action = ('bounded_search_exhausted', 'No accepted rounded route was found within the current bounds, constraints and search limits.',
                                'Inspect contact and bend findings, the included context and the search area. This result does not prove that no route exists.')
    return {'bounds_mm': deepcopy(bounds),
            'padding_mm': context.get('padding_mm'),
            'clearance_mm': clearance, 'pipe_outer_diameter_mm': diameter,
            'bend_radius_mm': result.get('assembly', {}).get('bend_radius_mm'),
            'grid_step_mm': layout['rules']['grid_step'],
            'boundary_review': kind, 'message': message, 'next_action': action, 'barriers': barriers,
            'ship_containment_verified': False,
            'scope': 'Local search bounds and supplied obstacle envelopes only. No automatic expansion or contact approval.'}
