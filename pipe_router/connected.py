"""Bounded coordinated routing using the team's existing route search.

Search only proposes. The independent whole-network validator decides whether
an alternative is acceptable; individual route success is never sufficient.
"""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import hashlib
import itertools
import json
import math
import threading
import time

from .network_model import normalize_network, network_digest, graph_summary
from .workflow import run_workflow
from .network_standards import requirement, bend_requirement
from .network_runtime import cancellation_scope
from .network_objective import network_metrics, resolve_objective

_CACHE = OrderedDict()
_CACHE_LOCK = threading.Lock()
CACHE_LIMIT = 128
CACHE_BYTES_LIMIT = 16 * 1024 * 1024
MAX_EVALUATIONS = 64
MAX_SEARCH_SPANS = 128
MAX_LOCAL_SPANS = 16
MAX_SECONDS = 120.
DEFAULT_SECONDS = 30.


def _length(points):
    return sum(math.dist(a, b) for a, b in zip(points, points[1:]))


def _bends(points):
    count = 0
    for a, b, c in zip(points, points[1:], points[2:]):
        u, v = [y-x for x,y in zip(a,b)], [y-x for x,y in zip(b,c)]
        la, lb = math.sqrt(sum(x*x for x in u)), math.sqrt(sum(x*x for x in v))
        if la and lb and sum(x*y for x,y in zip(u,v))/(la*lb) < 1-1e-8:
            count += 1
    return count


def metrics(network, reference=None, *, strategy=None, objective_weights=None, validation=None):
    """Source-normalized comparison; hard constraints are never penalties."""
    return network_metrics(network, reference, strategy=strategy, objective_weights=objective_weights, validation=validation)


def _at_distance(points, amount):
    for i, (a,b) in enumerate(zip(points,points[1:])):
        length = math.dist(a,b)
        if amount < length:
            fraction = amount/length
            return [[x+(y-x)*fraction for x,y in zip(a,b)]] + deepcopy(points[i+1:])
        amount -= length
    return []


def _neighbor_obstacle(other, span, network):
    points = deepcopy(other["points"])
    nodes = {n["id"]: n for n in network["nodes"]}
    common = {other["from_node"],other["to_node"]} & {span["from_node"],span["to_node"]}
    contacts = []
    for nodeid in common:
        # Explicit locally bounded port contact only. Remote parts of incident
        # spans remain active obstacles, including folds back toward the route.
        from .network_components import connection_contact_radius
        radius = connection_contact_radius(nodes[nodeid], other, span, float(requirement("clearance.shock")["value"]), include_fitting_allowance=True)
        contacts.append((nodes[nodeid]["position"], radius))
        if radius > 0:
            reverse = other["to_node"] == nodeid
            if reverse:
                points.reverse()
            points = _at_distance(points, radius+0.01)
            if reverse:
                points.reverse()
    if len(points) < 2:
        return None
    radius = max(other["outer_diameter_mm"]/2., (other.get("fitting_catalog") or {}).get("body_radius_mm") or 0.)
    caps = [{"start":a,"end":b,"radius":radius} for a,b in zip(points,points[1:])]
    # Rounded elbows occupy the inside of a corner, away from both sharp
    # centerline legs. Reserve their actual conservative arc sweeps too; merely
    # thickening the sharp legs can miss this volume. Only arcs entirely inside
    # an explicit connected-port neighbourhood are exempted during planning.
    from .network_fittings import fitting_envelopes
    for fitting in fitting_envelopes(other)["fittings"]:
        for cap in fitting["capsules"]:
            if not any(max(math.dist(cap["start"], center), math.dist(cap["end"], center))+cap["radius"] <= contact_radius for center, contact_radius in contacts):
                caps.append(deepcopy(cap))
    item = {"id":f"network-span:{other['id']}",
            "min":[min(min(cap["start"][i],cap["end"][i])-cap["radius"] for cap in caps) for i in range(3)],
            "max":[max(max(cap["start"][i],cap["end"][i])+cap["radius"] for cap in caps) for i in range(3)]}
    if len(caps) <= 256:
        item["capsules"] = caps
    else:
        # Preserve the entire envelope as a conservative box when the reused
        # router's per-obstacle representation budget would be exceeded.
        item["geometry_note"] = "Conservative whole-span box because detailed fitting reservations exceed the bounded router capsule budget"
    return item


def _system_type(network, span):
    value = span.get("system_type")
    if not value:
        systems = network.get("systems", [])
        value = next((s.get("type",s.get("system_type")) for s in systems if s.get("id")==span["system_id"]), None)
    return value if value in ("pressurized", "gravity", "drainage", "vent") else None


def _solve_span(network, span, cancelled, *, released_ids=(), bend_penalty=150., industrial_context=None):
    if industrial_context and industrial_context.can_preserve_straight(network, span):
        return deepcopy(span["points"]), {"legs":[{"expanded_states":0,"grid_nodes":0}],
                "reason":"Retained independently checked straight run; no changed finite neighboring envelope intersects its conservative influence bounds",
                "incremental_reuse":True}, False
    obstacles = deepcopy(network["obstacles"])
    for other in network["spans"]:
        if other["id"] != span["id"] and other["id"] not in released_ids:
            item = industrial_context.cached_obstacle(other, span, network, _neighbor_obstacle) if industrial_context else _neighbor_obstacle(other, span, network)
            if item:
                obstacles.append(item)
    from .network_components import component_geometry
    from .industrial_search import component_obstacles
    for component in component_geometry(network):
        if component["min"] is None or component["id"] in (span["from_node"], span["to_node"]):
            continue
        obstacles.extend(component_obstacles(component, span, float(requirement("clearance.shock")["value"])))
    from .core import MAX_OBSTACLES
    if len(obstacles) > MAX_OBSTACLES and industrial_context is None:
        return None, {"reason": "Combined supplied environment, pipe and component envelopes exceed the bounded router obstacle budget; select a smaller affected environment", "bounded_search": True, "obstacle_count": len(obstacles), "max_obstacles": MAX_OBSTACLES}, False
    clearance = float(requirement("clearance.shock")["value"])
    # Extra fitting body allowance helps the route search find buildable
    # alternatives. The separate validator checks actual swept fitting arcs.
    catalog = span.get("fitting_catalog") or {}
    body = max(0., (catalog.get("body_radius_mm") or span["outer_diameter_mm"]/2.)-span["outer_diameter_mm"]/2.)
    rules = {"clearance":clearance+body, "grid_step":network.get("search",{}).get("grid_step_mm",500.),"bend_penalty":bend_penalty}
    if catalog.get("radius_mm"):
        rules["bend_radius"] = catalog["radius_mm"]
    kind = _system_type(network,span)
    gravity = kind in ("gravity","drainage","vent")
    layout = {"id":span["id"],"units":"mm","provenance":network["provenance"],
              "system_type":"gravity" if gravity else "pressurized", "bounds":deepcopy(network["bounds"]),
              "pipe":{"start":span["points"][0],"end":span["points"][-1], "diameter":span["outer_diameter_mm"],"original_route":deepcopy(span["points"])},
              "rules":rules, "obstacles":obstacles}
    # Existing gravity solver assumes +Z up. Rotate an explicitly declared
    # orthonormal ship frame first and conservatively rotate obstacle boxes.
    axes = None
    reverse = gravity and span.get("flow_direction",span.get("constraints",{}).get("flow_direction")) == "to_from"
    if reverse:
        layout["pipe"]["original_route"].reverse()
        layout["pipe"]["start"],layout["pipe"]["end"] = layout["pipe"]["end"],layout["pipe"]["start"]
    if gravity:
        frame = network.get("coordinate_system",{})
        if not all(k in frame for k in ("fore_aft","athwartship","up")):
            return None, {"reason":"Directional drainage requires explicit ship axes"}, False
        axes = [frame["fore_aft"],frame["athwartship"],frame["up"]]
        if any(abs(sum(v*v for v in a)-1)>1e-6 for a in axes) or any(abs(sum(x*y for x,y in zip(a,b)))>1e-6 for a,b in itertools.combinations(axes,2)):
            return None, {"reason":"Ship axes must be orthonormal unit vectors"}, False
        def rotate(p): return [sum(x*y for x,y in zip(p,a)) for a in axes]
        layout["pipe"] = {**layout["pipe"], "start":rotate(layout["pipe"]["start"]), "end":rotate(layout["pipe"]["end"]), "original_route":[rotate(p) for p in layout["pipe"]["original_route"]]}
        for box in [layout["bounds"],*layout["obstacles"]]:
            corners = [rotate(p) for p in itertools.product(*zip(box["min"],box["max"]))]
            box["min"] = [min(p[i] for p in corners) for i in range(3)]
            box["max"] = [max(p[i] for p in corners) for i in range(3)]
            for cap in box.get("capsules",[]):
                cap["start"],cap["end"] = rotate(cap["start"]),rotate(cap["end"])
                if "clip_min" in cap:
                    # A rotated clip is not an exact AABB; drop the refinement,
                    # retaining the larger whole capsule as a conservative solid.
                    cap.pop("clip_min",None); cap.pop("clip_max",None)
                    for i in range(3):
                        box["min"][i] = min(box["min"][i],cap["start"][i]-cap["radius"],cap["end"][i]-cap["radius"])
                        box["max"][i] = max(box["max"][i],cap["start"][i]+cap["radius"],cap["end"][i]+cap["radius"])
        # A uniform 4% fall is conservative for both supplied directional
        # ratios. Independent segment checks reject any interpolation failure.
        pitch = requirement("drainage.pitch")
        rules["min_slope"] = max(pitch["fore_aft"],pitch["athwartship"])
    required = span.get("constraints",{}).get("required_compartments",[])
    waypoints = []
    for identifier in required:
        room = next((r for r in network.get("compartments",[]) if r["id"]==identifier),None)
        if room is None:
            return None,{"reason":"Required compartment geometry missing"},False
        center = [(a+b)/2 for a,b in zip(room["min"],room["max"])]
        if axes:
            center = [sum(x*y for x,y in zip(center,a)) for a in axes]
        waypoints.append(center)
    # Cache exact geometry and metadata of this local solve; altered neighbor
    # routes, drawing revisions or rules generate a different key.
    cache_key = hashlib.sha256(json.dumps([network["drawing"],network["standards_profile"],catalog,layout,waypoints,bool(industrial_context)],sort_keys=True,allow_nan=False).encode()).hexdigest()
    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
        if cached:
            _CACHE.move_to_end(cache_key)
            return deepcopy(cached[0]),deepcopy(cached[1]),True
    combined, searches, stage_summaries = [], [], []
    anchors = [layout["pipe"]["start"],*waypoints,layout["pipe"]["end"]]
    if industrial_context:
        from .industrial_search import spatial_search_stages
        stages = spatial_search_stages(layout, waypoints)
    else:
        stages = [(layout, None)]
    for stage_layout, stage_summary in stages:
        if stage_summary:
            stage_summaries.append(stage_summary)
        if len(stage_layout["obstacles"]) > MAX_OBSTACLES:
            searches.append({"reason": "Local environment exceeds the bounded router obstacle budget", "obstacle_count": len(stage_layout["obstacles"]), "max_obstacles": MAX_OBSTACLES, "expanded_states": 0, "grid_nodes": 0})
            continue
        stage_points = []
        for a,b in zip(anchors,anchors[1:]):
            if cancelled and cancelled():
                return None,{"legs":searches,"stages":stage_summaries,"reason":"Cancelled or time budget reached between bounded span searches"},False
            leg = deepcopy(stage_layout)
            leg["pipe"].update({"start":a,"end":b,"original_route":[a,b]})
            try:
                with cancellation_scope(cancelled):
                    if industrial_context and not gravity:
                        from .workflow import validate_layout_route
                        direct = validate_layout_route(leg, [a,b])
                    else:
                        direct = None
                    if direct and direct["valid"]:
                        output = {"candidate":{"points":[a,b]}, "search":{"expanded_states":0,"grid_nodes":0,"method":"continuously checked direct leg", "reason":"Straight fixed-endpoint leg is already the geometric length lower bound within supplied local obstacles"}}
                    else:
                        output = run_workflow(leg)
            except (ValueError, RuntimeError) as error:
                searches.append({"reason":str(error),"bounded_search":True,"expanded_states":0,"grid_nodes":0})
                break
            searches.append(output["search"])
            candidate = output.get("candidate")
            if not candidate:
                break
            stage_points.extend(candidate["points"] if not stage_points else candidate["points"][1:])
        else:
            combined = stage_points
            break
    if not combined:
        return None,{"legs":searches,"stages":stage_summaries,"reason":"Bounded spatial and resolution stages found no acceptable span route"},False
    if axes:
        combined = [[sum(p[i]*axes[i][j] for i in range(3)) for j in range(3)] for p in combined]
    if reverse:
        combined.reverse()
    # Keep source endpoints bit-for-bit to avoid numerical WCS roundoff.
    combined[0],combined[-1] = deepcopy(span["points"][0]),deepcopy(span["points"][-1])
    explanation = {"legs":searches,"reason":"Existing bounded router solved all mandatory legs"}
    if stage_summaries:
        explanation["stages"] = stage_summaries
    with _CACHE_LOCK:
        weight = len(json.dumps([combined,explanation],allow_nan=False).encode())
        if weight <= CACHE_BYTES_LIMIT // 4:
            _CACHE[cache_key] = deepcopy((combined,explanation,weight))
        while len(_CACHE)>CACHE_LIMIT or sum(item[2] for item in _CACHE.values())>CACHE_BYTES_LIMIT:
            _CACHE.popitem(last=False)
    return combined,explanation,False


def _node_variants(network, selected_ids=None):
    yield deepcopy(network), None
    movable = []
    for node in network["nodes"]:
        if node["fixed"] or not node.get("movement_region"):
            continue
        incident = [s for s in network["spans"] if node["id"] in (s["from_node"],s["to_node"])]
        if any(not s["modifiable"] or s.get("constraints",{}).get("preserve_existing") or selected_ids is not None and s["id"] not in selected_ids for s in incident):
            continue
        region = node["movement_region"]
        options = node.get("allowed_positions") or [[(a+b)/2 for a,b in zip(region["min"],region["max"])]]
        options = [p for p in options if math.dist(p,node["position"])>=1e-6]
        if not options:
            continue
        movable.append((node, options))
        for position in options:
            if math.dist(position,node["position"])<1e-6:
                continue
            variant = deepcopy(network)
            next(n for n in variant["nodes"] if n["id"]==node["id"])["position"] = list(position)
            for span in variant["spans"]:
                if span["from_node"]==node["id"]: span["points"][0]=list(position)
                if span["to_node"]==node["id"]: span["points"][-1]=list(position)
            yield variant,node["id"]
    # Joint alternatives for several movable valves are generated lazily;
    # max_evaluations bounds exploration of this potentially large product.
    for size in range(2,len(movable)+1):
        for group in itertools.combinations(movable,size):
            for positions in itertools.product(*(entry[1] for entry in group)):
                variant = deepcopy(network)
                moved = {entry[0]["id"]:p for entry,p in zip(group,positions)}
                for node in variant["nodes"]:
                    if node["id"] in moved: node["position"] = list(moved[node["id"]])
                for span in variant["spans"]:
                    if span["from_node"] in moved: span["points"][0]=list(moved[span["from_node"]])
                    if span["to_node"] in moved: span["points"][-1]=list(moved[span["to_node"]])
                yield variant,sorted(moved)


def resolve_selection(network, selected_span_ids=None, selected_system_ids=None):
    """Resolve explicit span/system union without changing source permissions."""
    known_spans = {s["id"] for s in network["spans"]}
    known_systems = {s["system_id"] for s in network["spans"]}
    for value, known, label in ((selected_span_ids, known_spans, "selected_span_ids"), (selected_system_ids, known_systems, "selected_system_ids")):
        if value is not None and (not isinstance(value, list) or not 1 <= len(value) <= 128 or any(not isinstance(i, str) or i not in known for i in value) or len(set(value)) != len(value)):
            raise ValueError(f"{label} must be a nonempty bounded list of unique known identifiers")
    explicit = selected_span_ids is not None or selected_system_ids is not None
    selected = set(selected_span_ids or []) | {s["id"] for s in network["spans"] if s["system_id"] in (selected_system_ids or [])}
    if not explicit:
        selected = known_spans
    permitted = {s["id"] for s in network["spans"] if s["id"] in selected and s["modifiable"] and not s.get("constraints", {}).get("preserve_existing")}
    return {"selected_span_ids": sorted(selected), "selected_system_ids": sorted({s["system_id"] for s in network["spans"] if s["id"] in selected}),
            "modifiable_span_ids": sorted(permitted), "explicit": explicit,
            "scope": "Selected permitted spans may change; all supplied systems and obstacles remain independently validated"}


def _routing_orders(network, identifiers):
    """Deterministic prioritized alternatives, bounded independently of n!."""
    if not identifiers:
        return [[]]
    spans = {s["id"]: s for s in network["spans"]}
    def priority(identifier):
        span = spans[identifier]
        radius = span["outer_diameter_mm"]/2 + float(requirement("clearance.shock")["value"])
        low = [min(p[i] for p in span["points"])-radius for i in range(3)]
        high = [max(p[i] for p in span["points"])+radius for i in range(3)]
        conflicts = sum(all(low[i] <= o["max"][i] and high[i] >= o["min"][i] for i in range(3)) for o in network["obstacles"])
        # Larger diameter and greater detour indicate more constrained routing.
        direct = max(1., math.dist(span["points"][0], span["points"][-1]))
        return (-conflicts, -span["outer_diameter_mm"], -_length(span["points"])/direct, identifier)
    candidates = [list(identifiers), list(reversed(identifiers)), sorted(identifiers, key=priority)]
    for offset in range(1, min(len(identifiers), 5)):
        candidates.append(list(identifiers[offset:])+list(identifiers[:offset]))
    unique = []
    for order in candidates:
        if order not in unique:
            unique.append(order)
    return unique[:8]


def _candidate_plans(network, identifiers, selected_ids):
    orders = _routing_orders(network, identifiers)
    first = None
    for variant, moved in _node_variants(network, selected_ids):
        if first is None:
            first = variant
        # Retained-space passes preserve the established V4 behavior. Additional
        # reservation passes can vacate still-unplanned selected routes, which
        # breaks the circular blocking in independent single-pipe optimization.
        for order in orders[:2]:
            yield variant, moved, order, "retain_existing"
        if len(identifiers) > 1:
            for order in orders[:2]:
                yield variant, moved, order, "prioritized_reservations"
    if first is not None and len(identifiers) > 1:
        for order in orders[2:]:
            yield first, None, order, "prioritized_reservations"


def _local_bend_penalty(config, before, span):
    """Match the local additive length/turn search to normalized objectives."""
    weights, scales = before["effective_objective_weights"], before["normalization_scales"]
    per_mm, per_bend = weights["length"]/scales["length"], weights["bends"]/scales["bends"]
    row = next((row for row in before["hydraulic_evidence"]["spans"] if row["span_id"] == span["id"]), {})
    data = span.get("hydraulics", {})
    if weights["hydraulic"] and row.get("status") == "ESTIMATED":
        dynamic_pressure = data["density_kg_m3"]*row["velocity_m_s"]**2/2
        hydraulic_weight = weights["hydraulic"]/scales["hydraulic"]
        per_mm += hydraulic_weight*row["darcy_friction_factor"]*dynamic_pressure/data["inner_diameter_mm"]
        per_bend += hydraulic_weight*data["bend_loss_coefficient"]*dynamic_pressure
    # A finite cap protects numerical search; all full candidate objective
    # terms, including modification/elevation, are recomputed independently.
    return min(100000., per_bend/per_mm) if per_mm > 0 else 100000. if per_bend > 0 else 150.


def optimize_network(raw, *, max_evaluations=24, cancelled=None, strategy=None, objective_weights=None,
                     max_seconds=None, selected_span_ids=None, selected_system_ids=None, progress=None):
    from .network_validation import validate_network, validation_delta
    from .network_fittings import fitting_envelopes
    started = time.perf_counter()
    if isinstance(max_evaluations, bool) or not isinstance(max_evaluations, int) or not 1 <= max_evaluations <= MAX_EVALUATIONS:
        raise ValueError("max_evaluations must be an integer from 1 to 64")
    if progress is not None and not callable(progress):
        raise ValueError("progress must be a callable receiving a progress record")
    network = normalize_network(raw)
    settings = network.get("search", {})
    config = resolve_objective(strategy if strategy is not None else settings.get("strategy"), objective_weights if objective_weights is not None else settings.get("objective_weights"))
    seconds = max_seconds if max_seconds is not None else settings.get("max_seconds", DEFAULT_SECONDS)
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or not 0 < seconds <= MAX_SECONDS:
        raise ValueError("max_seconds must be finite, positive and at most 120")
    selection = resolve_selection(network, selected_span_ids, selected_system_ids)
    selected = set(selection["modifiable_span_ids"])
    modifiable = [s["id"] for s in network["spans"] if s["id"] in selected]
    digest = network_digest(network)
    progress_errors = 0
    def initial_progress():
        nonlocal progress_errors
        if progress:
            try:
                progress({"phase":"initial_validation", "evaluations":0, "max_evaluations":max_evaluations,
                          "selected_span_count":len(modifiable), "completed_neighborhoods":0, "total_neighborhoods":0,
                          "neighborhood_index":0, "active_span_ids":[], "elapsed_seconds":time.perf_counter()-started})
            except Exception:
                progress_errors += 1
    initial_progress()
    original_validation = validate_network(network, reference=network, permitted_span_ids=selected)
    score_options = {"strategy": config["strategy"], "objective_weights": config["weights"]}
    before = metrics(network, reference=network, validation=original_validation, **score_options)
    best, best_validation, after = None, original_validation, before
    attempts, evaluations, cache_hits = [], 0, 0
    expanded_states = grid_nodes_peak = local_solves = 0
    evaluation_limit_reached = search_completed = False
    active_order = []
    controller = None
    if len(modifiable) > MAX_LOCAL_SPANS:
        from .industrial_search import NeighborhoodSearch
        controller = NeighborhoodSearch(network, modifiable, original_validation, before)
    was_cancelled, timed_out = False, False
    deadline = started + seconds
    def emit(phase):
        nonlocal progress_errors
        if progress:
            try:
                progress({"phase":phase, "evaluations":evaluations, "max_evaluations":max_evaluations,
                          "selected_span_count":len(modifiable), "completed_neighborhoods":controller.completed_neighborhoods if controller else int(search_completed),
                          "total_neighborhoods":controller.total_neighborhoods if controller else 1,
                          "neighborhood_index":controller.active_index if controller else int(bool(active_order)),
                          "active_span_ids":list(active_order), "expanded_states":expanded_states, "grid_nodes_peak":grid_nodes_peak,
                          "local_solves":local_solves, "elapsed_seconds":time.perf_counter()-started})
            except Exception:
                # UI reporting cannot change an engineering acceptance decision.
                progress_errors += 1
    def interrupted():
        nonlocal was_cancelled, timed_out
        if cancelled and cancelled():
            was_cancelled = True
        if time.perf_counter() >= deadline:
            timed_out = True
        return was_cancelled or timed_out
    blocked = any(c.get("status", "unresolved") == "unresolved" for c in network.get("unresolved_connections", []))
    if any(not _system_type(network, s) for s in network["spans"] if s["id"] in selected):
        blocked = True
        attempts.append({"reason": "Confirm every affected span's system type before routing"})
    if blocked:
        attempts.append({"reason": "Unresolved connections or search budget prevent safe network modification"})
    else:
        plans = _candidate_plans(network, modifiable, selected)
        # In addition to cold prioritized passes, refine an accepted network with
        # a bounded extra sweep; this can release space that was occupied by an
        # earlier candidate. No intermediate route is accepted independently.
        def all_plans():
            if controller:
                yield from controller.plans(_routing_orders)
            else:
                for variant, moved_node, order, mode in plans:
                    yield variant, moved_node, order, mode, None
                if best is not None and len(modifiable) > 1:
                    for order in _routing_orders(best, modifiable)[:2]:
                        yield best, None, order, "incumbent_reroute", None
        for variant, moved_node, order, mode, neighborhood in all_plans():
            if evaluations >= max_evaluations:
                evaluation_limit_reached = True
                break
            if interrupted():
                break
            active_order = list(order)
            emit("neighborhood_search" if controller else "coordinated_search")
            candidate = deepcopy(variant)
            solves = []
            for index, spanid in enumerate(order):
                if interrupted():
                    break
                span = next(s for s in candidate["spans"] if s["id"] == spanid)
                bend_penalty = _local_bend_penalty(config, before, span)
                released = set(order[index+1:]) if mode == "prioritized_reservations" else set()
                solve_options = {"released_ids":released, "bend_penalty":bend_penalty}
                if controller:
                    solve_options["industrial_context"] = controller
                points, search, hit = _solve_span(candidate, span, interrupted, **solve_options)
                cache_hits += int(hit)
                local_solves += 1
                if not hit:
                    expanded_states += sum(leg.get("expanded_states", 0) for leg in search.get("legs", []))
                grid_nodes_peak = max([grid_nodes_peak, *[leg.get("grid_nodes", 0) for leg in search.get("legs", [])]])
                solves.append({"span_id": spanid, "cache_hit": hit, "released_unplanned_span_ids": sorted(released), **search})
                if points:
                    span["points"] = points
                emit("span_search_complete")
            if interrupted():
                break
            evaluations += 1
            if neighborhood:
                neighborhood["evaluations"] += 1
            emit("independent_validation")
            try:
                validation = validate_network(candidate, reference=network, permitted_span_ids=selected)
            except ValueError as error:
                attempts.append({"evaluation": evaluations, "moved_node_id": moved_node, "accepted": False, "selected": False,
                                 "reservation_mode": mode, "reason": "Candidate could not complete independent validation", "detail": str(error)})
                continue
            score = metrics(candidate, reference=network, validation=validation, **score_options)
            failures = validation["hard_failures"]
            reference_spans = {s["id"]: s for s in network["spans"]}
            changed = [s for s in candidate["spans"] if s["points"] != reference_spans[s["id"]]["points"]]
            missing_physical = any(issue["code"] in ("missing_fitting_radius", "missing_fitting_body") for span in changed for issue in fitting_envelopes(span)["issues"])
            uncovered_size = any(_bends(span["points"]) and bend_requirement(span.get("nominal_diameter_mm")) is None for span in changed)
            improvement = bool(changed) and (original_validation["hard_failures"] > 0 or score["objective"] < before["objective"]-1e-9)
            accept = failures == 0 and not missing_physical and not uncovered_size and improvement and (best is None or score["objective"] < after["objective"]-1e-9)
            retained_repair = controller.observe(candidate, validation, score, not missing_physical and not uncovered_size) if controller else False
            candidate_digest = network_digest(candidate)
            attempts.append({"evaluation": evaluations, "moved_node_id": moved_node, "order": list(order), "reservation_mode": mode,
                             "objective": score["objective"], "metrics": score, "candidate_digest": candidate_digest,
                             "changed_span_ids": [s["id"] for s in changed], "hard_failures": failures,
                             "missing_physical_fitting_geometry": missing_physical, "uncovered_nominal_size": bool(uncovered_size),
                             "accepted": accept, "selected": False, "span_searches": solves})
            if neighborhood:
                attempts[-1].update({"neighborhood_index":neighborhood["index"], "neighborhood_span_ids":list(neighborhood["span_ids"]),
                                     "neighborhood_phase":neighborhood["phase"], "internal_repair_retained":retained_repair,
                                     "internal_repair_hard_failures":controller.validation["hard_failures"]})
            if accept:
                best, best_validation, after = candidate, validation, score
        else:
            search_completed = True
    termination = "CANCELLED" if was_cancelled else "TIME_BUDGET" if timed_out else "EVALUATION_LIMIT" if evaluation_limit_reached else "BLOCKED_METADATA" if blocked else "BOUNDED_SEARCH_COMPLETED"
    status = "CANCELLED" if was_cancelled else "IMPROVED" if best else "TIME BUDGET EXCEEDED" if timed_out else "SEARCH LIMIT REACHED" if evaluation_limit_reached else "NO IMPROVEMENT FOUND" if original_validation["hard_failures"] == 0 and not blocked else "NO FEASIBLE ROUTE FOUND"
    if was_cancelled:
        best, best_validation, after = None, original_validation, before
    best_digest = network_digest(best) if best else None
    selected_attempt = next((attempt for attempt in reversed(attempts) if best_digest and attempt.get("accepted") and attempt.get("candidate_digest") == best_digest), None)
    if selected_attempt:
        selected_attempt["selected"] = True
    changed = [s["id"] for s in best["spans"] if s["points"] != next(o for o in network["spans"] if o["id"] == s["id"])["points"]] if best else []
    notes = [c for c in best_validation["checks"] if c["status"] in ("FAIL", "UNVERIFIED")]
    quality = "HEURISTICALLY IMPROVED" if best and original_validation["hard_failures"] == 0 else "FEASIBLE NOT PROVEN OPTIMAL" if best else "NO OPTIMALITY OR INFEASIBILITY PROOF"
    reason = "A whole-network checked coordinated proposal is available" if best else "Original preserved. No acceptable improvement was found within the stated search limits"
    if timed_out:
        reason += "; the search time budget was reached"
    if before["unverified_objective_terms"]:
        reason += "; unavailable objective evidence was excluded explicitly: " + ", ".join(before["unverified_objective_terms"])
    emit("cancelled" if was_cancelled else "complete")
    return {"schema_version": "pipeforge.proposal/4.0", "status": status, "proposal_id": hashlib.sha256((digest+json.dumps(best, sort_keys=True)).encode()).hexdigest()[:24],
            "source_digest": digest, "source_revision": network["drawing"]["revision"], "source_drawing_id": network["drawing"]["id"],
            "original_network": network, "proposed_network": best, "before": before, "after": after,
            "original_validation": original_validation, "validation": best_validation, "changed_span_ids": changed,
            "validation_delta":validation_delta(original_validation, best_validation),
            "strategy": config["strategy"], "objective_config": config, "selection": selection, "optimization_quality": quality,
            "topology": graph_summary(best or network), "unresolved_issues": notes,
            "release_status": "REVIEW REQUIRED" if best_validation.get("unverified", 0) or best_validation["hard_failures"] else "DESIGNER REVIEW REQUIRED",
            "explanation": reason,
            "search": {"evaluations": evaluations, "max_evaluations": max_evaluations, "max_seconds": float(seconds), "time_budget_reached": timed_out,
                       "evaluation_budget_reached": evaluation_limit_reached, "max_modifiable_spans": MAX_SEARCH_SPANS,
                       "termination_reason":termination, "bounded_search_completed":search_completed,
                       "max_neighborhood_spans":MAX_LOCAL_SPANS, "selected_span_count":len(modifiable),
                       "expanded_states":expanded_states, "grid_nodes_peak":grid_nodes_peak, "local_solves":local_solves,
                       "completed_neighborhoods":controller.completed_neighborhoods if controller else int(search_completed),
                       "total_neighborhoods":controller.total_neighborhoods if controller else 1,
                       "neighborhoods":controller.neighborhoods if controller else [],
                       "interaction_graph":controller.graph_summary if controller else None,
                       "internal_repair_updates":controller.accepted_repairs if controller else 0,
                       "geometry_cache_hits":controller.geometry_cache_hits if controller else 0,
                       "geometry_cache_bytes":controller.geometry_cache_bytes if controller else 0,
                       "progress_delivery_errors":progress_errors,
                       "cache_hits": cache_hits, "cache_capacity": CACHE_LIMIT, "cache_bytes_limit": CACHE_BYTES_LIMIT,
                       "elapsed_seconds": time.perf_counter()-started, "attempts": attempts, "cancelled": was_cancelled,
                       "method": "Topology/spatial large-neighborhood coordinated repair with local multiresolution routing and independent complete-network acceptance" if controller else "Prioritized multi-system routing with bounded order exploration, temporary release of unplanned permitted runs, incumbent rerouting and independent whole-network acceptance",
                       "limitations": ["Bounded search is not a proof of global optimality or continuous infeasibility", "Valve moves sample declared allowed positions or permitted-space center", "No source CAD entities are modified", "Time limits are cooperative; independent validation is completed before any candidate is accepted", "Hydraulic loss estimates require supplied fluid, flow, inner diameter, roughness and bend-loss evidence; no flow-distribution solve is performed"]}}
