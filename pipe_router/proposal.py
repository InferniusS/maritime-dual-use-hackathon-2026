"""Bounded, assembly-aware alternatives for a single pressure pipe.

Every accepted shape is rechecked by the independent circular-fillet planner.
This is a geometric review proposal, not a fitting catalogue or global solver.
"""
from copy import deepcopy
import itertools
import math
import time

from . import core

MAX_ALTERNATIVES = 600
MAX_SEARCH_SECONDS = 15.0


def _compact(points):
    # Remove numerical duplicate points in the movable portion only. Exact
    # protected endpoint records are reattached by the caller afterwards.
    answer = []
    for point in points:
        point = list(point)
        if answer and math.dist(answer[-1], point) < 1e-6:
            continue
        if len(answer) >= 2:
            a = [b-a for a,b in zip(answer[-2],answer[-1])]
            b = [b-a for a,b in zip(answer[-1],point)]
            la, lb = math.sqrt(sum(x*x for x in a)), math.sqrt(sum(x*x for x in b))
            if la and lb and sum((x/la-y/lb)**2 for x,y in zip(a,b)) < 1e-18:
                answer[-1] = point
                continue
        answer.append(point)
    return answer


def _manhattan(start, end, order):
    point = list(start)
    answer = [point.copy()]
    for axis in order:
        point[axis] = end[axis]
        answer.append(point.copy())
    return _compact(answer)


def _offset(point, previous, distance):
    vector = [a-b for a,b in zip(point, previous)]
    length = math.sqrt(sum(x*x for x in vector))
    if length < 1e-6:
        return None
    return [a + distance*b/length for a,b in zip(point,vector)]


def _alternatives(layout, radius):
    original = layout['pipe']['original_route']
    protected = layout.get('protected_endpoints', [])
    start = next((r['points'] for r in protected if r['endpoint']=='start'), [original[0]])
    end = next((r['points'] for r in protected if r['endpoint']=='end'), [original[-1]])
    a, b = start[-1], end[0]
    offsets = [radius+25, radius+250, 2*radius+250]
    start_offsets = offsets if len(start)>1 else [0]
    end_offsets = offsets if len(end)>1 else [0]
    margin = layout['pipe']['diameter']/2 + layout['rules']['clearance']
    low = [v+margin+1 for v in layout['bounds']['min']]
    high = [v-margin-1 for v in layout['bounds']['max']]
    # Corridor planes follow object faces with room for a tangent bend. The
    # bounded set is independent of source handles or a particular drawing.
    planes = []
    for axis in range(3):
        values = {low[axis], high[axis], (a[axis]+b[axis])/2}
        for source in (a[axis], b[axis]):
            values.update((source-radius*2-250, source+radius*2+250))
        for obstacle in layout['obstacles']:
            values.update((obstacle['min'][axis]-margin-radius-1,
                           obstacle['max'][axis]+margin+radius+1))
        values.update(p[axis] for p in original)
        values = sorted(v for v in values if low[axis] <= v <= high[axis])
        # Preserve coverage of distant corridors; nearest faces alone can all
        # lie inside a dense equipment bank and miss its clear outer side.
        sampled = {values[round(i*(len(values)-1)/15)] for i in range(16)} if values else set()
        sampled.update(sorted(values,key=lambda v:abs(v-(a[axis]+b[axis])/2))[:6])
        planes.append(sorted(sampled, key=lambda v: abs(v-(a[axis]+b[axis])/2)))
    pairs = sorted(itertools.product(start_offsets,end_offsets), key=lambda pair: sum(pair))
    for sa, eb in pairs:
        first = _offset(a, start[-2], sa) if sa else list(a)
        last = _offset(b, end[1], eb) if eb else list(b)
        if first is None or last is None:
            continue
        if any(not low[i] <= p[i] <= high[i] for p in (first,last) for i in range(3)):
            continue
        def wrapped(middle):
            # Keep stub vertices verbatim; do not collapse their boundaries.
            middle = _compact([a] + middle + [b])
            return deepcopy(start[:-1]) + middle + deepcopy(end[1:])
        for order in itertools.permutations(range(3)):
            yield wrapped(_manhattan(first,last,order))
        # Move onto a corridor plane, cross it in either order, return to port.
        for axis in range(3):
            other = [i for i in range(3) if i!=axis]
            for plane in planes[axis]:
                left, right = list(first), list(last)
                left[axis] = right[axis] = plane
                for order in (other,other[::-1]):
                    yield wrapped([first]+_manhattan(left,right,order)+[last])
        # Two corridor offsets permit an out-of-plane route around a rack.
        for primary in range(3):
            other = [i for i in range(3) if i!=primary]
            for u,v in itertools.product(planes[other[0]][:4],planes[other[1]][:4]):
                left, right = list(first),list(last)
                left[other[0]]=right[other[0]]=u
                left[other[1]]=right[other[1]]=v
                for order in (other,other[::-1]):
                    middle = _manhattan(first,left,order)+[right]+_manhattan(right,last,order[::-1])
                    yield wrapped(middle)


def _score(assembly):
    components = assembly['components']
    # Elevation changes count straight runs, not the many samples of an arc.
    vertical = 0
    previous_vertical = False
    for component in components:
        current_vertical = component['kind']=='straight' and abs(component['end'][2]-component['start'][2])>1e-6
        if current_vertical and not previous_vertical:
            vertical += 1
        previous_vertical = current_vertical
    assembly['metrics']['elevation_runs'] = vertical
    return (assembly['metrics']['bend_count'], vertical, assembly['metrics']['centerline_length_mm'])


def add_pipe_proposal(result):
    """Replace a centerline-only result with a checked rounded proposal opt-in."""
    from .assembly import plan_assembly, render_reference, reconstruct_sampled_arcs
    from .workflow import _validate_normalized
    layout = result['normalized_layout']
    options = layout.get('proposal', {})
    if options.get('mode') != 'rounded_pipe':
        return result
    radius = options.get('bend_radius_mm', max(304.8,1.5*layout['pipe']['diameter'],layout['rules'].get('bend_radius',0)))
    radius = core._number(radius, 'proposal.bend_radius_mm', positive=True)
    if radius < layout['rules'].get('bend_radius',0):
        raise ValueError('proposal.bend_radius_mm cannot be smaller than the declared rules.bend_radius in millimeters')
    started = time.monotonic()
    original = result['original']
    result['centerline_search'] = deepcopy(result.get('search', {}))
    recognition_warning = None
    try:
        sampled = reconstruct_sampled_arcs(original['points'], layout.get('protected_endpoints', []))
    except ValueError as error:
        # The original reference may be malformed even when the separately
        # searched replacement is usable. Do not clean protected source points
        # or turn optional recognition into a fatal workflow failure.
        recognition_warning = str(error)
        sampled = {'points':None, 'arcs':[], 'limit_reached':False,
                   'status':'needs_review', 'reason':recognition_warning}
    if sampled['arcs']:
        # Source samples describe an existing curved centerline, not many tiny
        # fabricated straights. Refilleting each sample would invent a source
        # defect; replacing its observed radius would invent a baseline.
        baseline = {'status':'needs_review', 'findings':[{
            'code':'baseline_sampled_arcs', 'status':'needs_review',
            'message':'Circular samples were recognized in the original reference. Their observed radii are not a verified fitting inventory; the original is not re-filleted at every sample or assigned fabricated-piece savings.',
            'recognized_arc_count':len(sampled['arcs']),
            'observed_radii_mm':[arc['observed_radius_mm'] for arc in sampled['arcs']]}]}
    else:
        try:
            baseline = plan_assembly(layout,original['points'],bend_radius_mm=radius)
        except ValueError as error:
            baseline = {'status':'blocked','findings':[{'code':'baseline_assembly_unavailable','status':'needs_review','message':str(error)}]}
    best = None
    diagnostics = []
    seen = set()
    evaluated = 0
    limited = False
    if baseline['status']=='ready' and original['valid']:
        best = (_score(baseline), original['points'], baseline, True)
    initial = ([sampled['points']] if sampled['points'] else [])
    if result.get('candidate'):
        initial.append(result['candidate']['points'])
    routes = itertools.chain(initial, _alternatives(layout,radius)) if layout['system_type']=='pressurized' else initial
    for points in routes:
        if evaluated >= MAX_ALTERNATIVES or time.monotonic()-started >= MAX_SEARCH_SECONDS:
            limited=True
            break
        key = tuple(tuple(round(v,6) for v in p) for p in points)
        if key in seen:
            continue
        seen.add(key)
        evaluated+=1
        if len(points)<2:
            continue
        check = _validate_normalized(layout,points,allow_pressure_polyline=True)
        if not check['valid']:
            for issue in check.get('issues', []):
                finding = {**deepcopy(issue), 'status': 'fail', 'scope': 'candidate_validation'}
                if len(diagnostics) < 8 and finding not in diagnostics:
                    diagnostics.append(finding)
            continue
        try:
            assembly = plan_assembly(layout,points,bend_radius_mm=radius)
        except ValueError as error:
            if len(diagnostics)<8:
                diagnostics.append({'code':'assembly_input','status':'fail','message':str(error)})
            continue
        if assembly['status']=='ready':
            score = _score(assembly)
            if best is None or score < best[0]:
                best = (score,points,assembly,False)
        elif len(diagnostics)<8:
            diagnostics.extend(assembly['findings'][:2])
    result['centerline_review'] = deepcopy(result.get('candidate'))
    try:
        reference = (baseline['meshes'] if baseline['status']=='ready' and layout['provenance']=='synthetic'
                     else render_reference(_compact(original['points']),layout['pipe']['diameter'])['meshes'])
        reference_warning = None
    except ValueError as error:
        reference = []
        reference_warning = str(error)
    if best is not None:
        _,points,assembly,is_original = best
        # Rounded-path metrics live separately; points retain exact endpoint
        # stubs and sharp design vertices for identity and designer review.
        checked = _validate_normalized(layout,points,allow_pressure_polyline=True)
        result['candidate'] = {'points':deepcopy(points), **checked,
            'route_representation':'rounded_pipe_design_vertices',
            'bend_count_kind':'design_direction_changes_see_assembly_metrics'}
        result['status'] = 'unchanged' if is_original else 'improved'
        if is_original:
            change='unchanged'
        elif not original['valid']:
            change='clearance_repair'
        elif baseline['status']=='ready':
            change=('fewer_bends' if assembly['metrics']['bend_count']<baseline['metrics']['bend_count'] else
                    'fewer_elevation_changes' if assembly['metrics']['elevation_runs']<baseline['metrics']['elevation_runs'] else 'shorter_length')
        else:
            change='review_proposal'
        assembly['change_type']=change
        assembly['baseline_metrics']=deepcopy(baseline['metrics']) if baseline['status']=='ready' and original['valid'] else None
        result['search']['reason'] = 'Rounded pipe proposal fits the supplied envelopes and fixed stubs; fitting availability and engineering approval remain open.'
    else:
        assembly = {'status':'blocked','units':'mm','outer_diameter_mm':layout['pipe']['diameter'],
            'bend_radius_mm':radius,'catalog_verified':False,'components':[],'meshes':[],
            'findings':diagnostics, 'metrics':{}, 'change_type':'no_rounded_proposal'}
        result['candidate']=None
        result['status']='no_route'
        reason = 'No rounded pipe proposal passed this bounded search. A centerline alone is not accepted as a physical pipe proposal.'
        blocked = result['centerline_search'].get('blocked_endpoints', [])
        if blocked:
            contacts = ', '.join(dict.fromkeys(
                str(item['endpoint']) + ': ' + (str(item['source_handle']) + ' (' + str(item['obstacle']) + ')'
                                               if item.get('source_handle') else str(item['obstacle']))
                for item in blocked))
            reason = ('The centerline search could not start because fixed endpoints touch obstacle envelopes '
                      '(' + contacts + '). Verify intended connections and clearance. ' + reason)
        result['search']['reason'] = reason
    assembly['reference_meshes']=reference
    assembly['reference_representation'] = 'rounded_baseline' if baseline['status']=='ready' and layout['provenance']=='synthetic' else 'unfitted_polyline'
    if reference_warning:
        assembly['findings'].append({'code':'reference_unavailable','status':'needs_review','message':reference_warning})
    assembly['baseline_status']=baseline['status']
    assembly['sampled_curve_review'] = sampled
    if recognition_warning:
        assembly['findings'].append({'code':'sampled_curve_recognition_unavailable',
            'status':'needs_review', 'scope':'baseline_reconstruction',
            'message':'The original reference could not be recognized as circular samples: ' + recognition_warning})
    # Reconstruction failures describe our interpretation of the reference, not
    # a verified defect in the source CAD or the cause of candidate rejection.
    assembly['baseline_findings'] = [
        {**deepcopy(finding), 'scope': 'baseline_reconstruction'}
        for finding in baseline.get('findings', [])
    ]
    assembly['search']={'evaluated_candidates':evaluated,'limit_reached':limited,
        'elapsed_seconds':round(time.monotonic()-started,3),'max_candidates':MAX_ALTERNATIVES}
    result['assembly']=assembly
    result['comparison_objective']='rounded_bends_elevation_length'
    result['method']='Bounded corridor alternatives ranked by rounded bend count, vertical straight runs, then rounded length. Every accepted circular-fillet envelope is checked against supplied obstacles and allowed spaces.'
    result['assumptions']=[a for a in result['assumptions'] if not a.startswith('CAD-derived route comparisons')]
    result['limitations']=[a for a in result['limitations'] if not a.startswith(('Bend radius and fitting space have not','The optional bend radius only','Pressure search does not optimize'))]
    result['limitations'].extend([
        'Rounded pipe output is a geometric outer envelope. No wall thickness, vendor fitting catalogue, pressure rating or fabrication approval is established.',
        'Alternatives are bounded in number and time. A blocked result does not prove that no feasible route exists.',
        'An existing CAD sampled curve is not a verified fitting count. Savings in physical bends are reported only when the original can also be planned with the same assumptions.'
    ])
    result['checks'].update({'rounded_bend_envelopes':assembly['status']=='ready','bend_tangent_space':assembly['status']=='ready'})
    return result
