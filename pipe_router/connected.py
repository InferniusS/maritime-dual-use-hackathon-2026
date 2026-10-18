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

_CACHE = OrderedDict()
_CACHE_LOCK = threading.Lock()
CACHE_LIMIT = 128
CACHE_BYTES_LIMIT = 16 * 1024 * 1024
MAX_EVALUATIONS = 64
MAX_SEARCH_SPANS = 16


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


def metrics(network, reference=None):
    refs = {s["id"]: s for s in reference["spans"]} if reference else {}
    length = sum(_length(s["points"]) for s in network["spans"])
    bends = sum(_bends(s["points"]) for s in network["spans"])
    up = network.get("coordinate_system", {}).get("up", [0,0,1])
    elevation = sum(abs(sum((y-x)*z for x,y,z in zip(a,b,up))) for s in network["spans"] for a,b in zip(s["points"], s["points"][1:]))
    modified = sum(_length(s["points"]) for s in network["spans"] if s["id"] in refs and s["points"] != refs[s["id"]]["points"])
    # Transparent numerical objective in equivalent millimetres. Hard checks
    # are gates, never penalty terms. Preferences get a separate small weight.
    score = length + 150.*bends + .05*elevation + .02*modified
    return {"length_mm": length, "direction_changes": bends, "fitting_requirements": bends,
            "elevation_change_mm": elevation, "modified_length_mm": modified, "objective": score,
            "objective_weights": {"length":1., "direction_changes_mm":150., "elevation":.05,"modification":.02,"preference_warning_mm":25.}}


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
    for nodeid in common:
        # Explicit locally bounded port contact only. Remote parts of incident
        # spans remain active obstacles, including folds back toward the route.
        radius = (other["outer_diameter_mm"]+span["outer_diameter_mm"])/2. + float(requirement("clearance.shock")["value"])
        declared = nodes[nodeid].get("constraints", {}).get("contact_radius_mm")
        body = nodes[nodeid].get("dimensions",{}).get("body_radius_mm")
        if declared is not None and body is not None and 0 < declared <= min(1000,body+radius):
            radius = max(radius,declared)
        if radius > 0:
            reverse = other["to_node"] == nodeid
            if reverse:
                points.reverse()
            points = _at_distance(points, radius+0.01)
            if reverse:
                points.reverse()
    if len(points) < 2:
        return None
    radius = max(other["outer_diameter_mm"]/2., other.get("fitting_catalog", {}).get("body_radius_mm", 0.))
    caps = [{"start":a,"end":b,"radius":radius} for a,b in zip(points,points[1:])]
    return {"id":f"network-span:{other['id']}", "min":[min(p[i] for p in points)-radius for i in range(3)],
            "max":[max(p[i] for p in points)+radius for i in range(3)], "capsules":caps}


def _system_type(network, span):
    value = span.get("system_type")
    if not value:
        systems = network.get("systems", [])
        value = next((s.get("type",s.get("system_type")) for s in systems if s.get("id")==span["system_id"]), None)
    return value


def _solve_span(network, span, cancelled):
    obstacles = deepcopy(network["obstacles"])
    for other in network["spans"]:
        if other["id"] != span["id"]:
            item = _neighbor_obstacle(other, span, network)
            if item:
                obstacles.append(item)
    clearance = float(requirement("clearance.shock")["value"])
    # Extra fitting body allowance helps the route search find buildable
    # alternatives. The separate validator checks actual swept fitting arcs.
    catalog = span.get("fitting_catalog", {})
    body = max(0., catalog.get("body_radius_mm", span["outer_diameter_mm"]/2.)-span["outer_diameter_mm"]/2.)
    rules = {"clearance":clearance+body, "grid_step":network.get("search",{}).get("grid_step_mm",500.),"bend_penalty":150.}
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
    cache_key = hashlib.sha256(json.dumps([network["drawing"],network["standards_profile"],catalog,layout,waypoints],sort_keys=True,allow_nan=False).encode()).hexdigest()
    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
        if cached:
            _CACHE.move_to_end(cache_key)
            return deepcopy(cached[0]),deepcopy(cached[1]),True
    combined = []
    searches = []
    anchors = [layout["pipe"]["start"],*waypoints,layout["pipe"]["end"]]
    for a,b in zip(anchors,anchors[1:]):
        if cancelled and cancelled():
            return None,{"reason":"Cancelled between bounded span searches"},False
        leg = deepcopy(layout)
        leg["pipe"].update({"start":a,"end":b,"original_route":[a,b]})
        try:
            with cancellation_scope(cancelled):
                output = run_workflow(leg)
        except (ValueError, RuntimeError) as error:
            return None,{"reason":str(error),"bounded_search":True},False
        searches.append(output["search"])
        candidate = output.get("candidate")
        if not candidate:
            return None,{"legs":searches,"reason":"Bounded search found no acceptable span route"},False
        combined.extend(candidate["points"] if not combined else candidate["points"][1:])
    if axes:
        combined = [[sum(p[i]*axes[i][j] for i in range(3)) for j in range(3)] for p in combined]
    if reverse:
        combined.reverse()
    # Keep source endpoints bit-for-bit to avoid numerical WCS roundoff.
    combined[0],combined[-1] = deepcopy(span["points"][0]),deepcopy(span["points"][-1])
    explanation = {"legs":searches,"reason":"Existing bounded router solved all mandatory legs"}
    with _CACHE_LOCK:
        weight = len(json.dumps([combined,explanation],allow_nan=False).encode())
        if weight <= CACHE_BYTES_LIMIT // 4:
            _CACHE[cache_key] = deepcopy((combined,explanation,weight))
        while len(_CACHE)>CACHE_LIMIT or sum(item[2] for item in _CACHE.values())>CACHE_BYTES_LIMIT:
            _CACHE.popitem(last=False)
    return combined,explanation,False


def _node_variants(network):
    yield deepcopy(network), None
    movable = []
    for node in network["nodes"]:
        if node["fixed"] or not node.get("movement_region"):
            continue
        incident = [s for s in network["spans"] if node["id"] in (s["from_node"],s["to_node"])]
        if any(not s["modifiable"] for s in incident):
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


def optimize_network(raw, *, max_evaluations=24, cancelled=None):
    from .network_validation import validate_network
    started = time.perf_counter()
    if isinstance(max_evaluations,bool) or not isinstance(max_evaluations,int) or not 1 <= max_evaluations <= MAX_EVALUATIONS:
        raise ValueError("max_evaluations must be an integer from 1 to 64")
    network = normalize_network(raw)
    digest = network_digest(network)
    original_validation = validate_network(network,reference=network)
    before = metrics(network)
    original_penalty = sum(c["status"]=="FAIL" and c.get("severity")=="preference" for c in original_validation["checks"])*25.
    before["objective"] += original_penalty
    best, best_validation, after = None,original_validation,before
    attempts, evaluations, cache_hits = [],0,0
    was_cancelled = False
    blocked = any(c.get("status","unresolved")=="unresolved" for c in network.get("unresolved_connections",[]))
    modifiable = [s["id"] for s in network["spans"] if s["modifiable"]]
    # Unknown functional metadata cannot be guessed into pressure routing.
    if any(not _system_type(network,s) for s in network["spans"] if s["modifiable"]):
        blocked = True
        attempts.append({"reason":"Confirm every affected span's system type before routing"})
    if len(modifiable)>MAX_SEARCH_SPANS:
        blocked = True
        attempts.append({"reason":"Select an affected subnetwork with at most 16 modifiable spans; entire supplied network is still validated"})
    if blocked:
        attempts.append({"reason":"Unresolved connections or search budget prevent safe network modification"})
    else:
        orders = [modifiable]
        if len(modifiable)>1: orders.append(list(reversed(modifiable)))
        for variant,moved_node in _node_variants(network):
            for order in orders:
                if evaluations>=max_evaluations:
                    break
                if cancelled and cancelled():
                    was_cancelled=True; break
                candidate = deepcopy(variant)
                solves = []
                for spanid in order:
                    if cancelled and cancelled():
                        was_cancelled=True; break
                    span = next(s for s in candidate["spans"] if s["id"]==spanid)
                    # Designers can lock valid/approved sections independently.
                    if span.get("constraints",{}).get("preserve_existing"):
                        continue
                    points,search,hit = _solve_span(candidate,span,cancelled)
                    cache_hits += int(hit)
                    solves.append({"span_id":spanid,"cache_hit":hit,**search})
                    if points:
                        span["points"] = points
                if cancelled and cancelled():
                    was_cancelled = True
                if was_cancelled: break
                evaluations += 1
                try:
                    validation = validate_network(candidate,reference=network)
                except ValueError as error:
                    attempts.append({"evaluation":evaluations,"moved_node_id":moved_node,"accepted":False,
                                     "reason":"Candidate could not complete independent validation", "detail":str(error)})
                    continue
                score = metrics(candidate,reference=network)
                score["objective"] += sum(c["status"]=="FAIL" and c.get("severity")=="preference" for c in validation["checks"])*25.
                failures = validation["hard_failures"]
                from .network_fittings import fitting_envelopes
                missing_physical = any(issue["code"] in ("missing_fitting_radius","missing_fitting_body") for span in candidate["spans"] if span["points"]!=next(s for s in network["spans"] if s["id"]==span["id"])["points"] for issue in fitting_envelopes(span)["issues"])
                uncovered_size = any(_bends(span["points"]) and bend_requirement(span.get("nominal_diameter_mm")) is None for span in candidate["spans"] if span["points"]!=next(s for s in network["spans"] if s["id"]==span["id"])["points"])
                improvement = original_validation["hard_failures"]>0 or score["objective"]<before["objective"]-1e-6
                accept = failures==0 and not missing_physical and not uncovered_size and improvement and (best is None or score["objective"]<after["objective"]-1e-6)
                attempts.append({"evaluation":evaluations,"moved_node_id":moved_node,"order":order,
                                 "objective":score["objective"],"hard_failures":failures,"missing_physical_fitting_geometry":missing_physical,"uncovered_nominal_size":bool(uncovered_size),"accepted":accept,"span_searches":solves})
                if accept:
                    best,best_validation,after = candidate,validation,score
            if evaluations>=max_evaluations or was_cancelled: break
    status = "CANCELLED" if was_cancelled else "IMPROVED" if best else "NO IMPROVEMENT FOUND" if original_validation["hard_failures"]==0 and not blocked else "NO FEASIBLE ROUTE FOUND"
    if was_cancelled:
        best,best_validation,after = None,original_validation,before
    changed = [s["id"] for s in best["spans"] if s["points"]!=next(o for o in network["spans"] if o["id"]==s["id"])["points"]] if best else []
    notes = [c for c in best_validation["checks"] if c["status"] in ("FAIL","UNVERIFIED")]
    return {"schema_version":"pipeforge.proposal/4.0","status":status,"proposal_id":hashlib.sha256((digest+json.dumps(best,sort_keys=True)).encode()).hexdigest()[:24],
            "source_digest":digest,"source_revision":network["drawing"]["revision"],"source_drawing_id":network["drawing"]["id"],
            "original_network":network,"proposed_network":best,"before":before,"after":after,
            "original_validation":original_validation,"validation":best_validation,"changed_span_ids":changed,
            "topology":graph_summary(best or network),"unresolved_issues":notes,
            "release_status":"REVIEW REQUIRED" if best_validation.get("unverified",0) or best_validation["hard_failures"] else "DESIGNER REVIEW REQUIRED",
            "explanation": "A whole-network checked coordinated proposal is available" if best else "Original preserved. No acceptable improvement was found within the stated search limits",
            "search":{"evaluations":evaluations,"max_evaluations":max_evaluations,"max_modifiable_spans":MAX_SEARCH_SPANS,
                      "cache_hits":cache_hits,"cache_capacity":CACHE_LIMIT,"cache_bytes_limit":CACHE_BYTES_LIMIT,"elapsed_seconds":time.perf_counter()-started,"attempts":attempts,
                      "cancelled":was_cancelled,"method":"Existing team finite-grid router, coordinated span orders and bounded permitted node positions; independent whole-network acceptance",
                      "limitations":["Bounded search is not a proof of global optimality or continuous infeasibility", "Valve moves sample declared allowed positions or permitted-space center", "No source CAD entities are modified"]}}
