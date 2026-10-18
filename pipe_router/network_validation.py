"""Independent complete-network engineering checks in global WCS millimetres.

This module never trusts optimizer validity flags. PASS applies only to the
stated checks and supplied geometry, with explicit uncertainty propagated.
"""
from __future__ import annotations
from collections import defaultdict
from contextvars import ContextVar
from copy import deepcopy
import itertools
import json
import math

from backend.geometry import segment_box_distance, segment_distance
from backend.spatial import SpatialCandidates
from .network_fittings import corner_angle, fitting_envelopes, span_occupied_segments
from .network_components import component_geometry, connection_contact_radius, support_contact_radius, PHYSICAL_NODE_TYPES
from .network_standards import PROFILE_ID, bend_requirement, requirement, standards_profile

EPS = 1e-5
MAX_PHYSICAL_PRIMITIVES = 8192
MAX_NARROWPHASE_PAIRS = 250000
_WORK_BUDGET = ContextVar("pipeforge_network_validation_budget",default=None)
_PAIR_INDEXES = ContextVar("pipeforge_validation_pair_indexes",default=None)


def _consume_work():
    budget=_WORK_BUDGET.get()
    if budget is not None:
        budget[0]+=1
        if budget[0]>MAX_NARROWPHASE_PAIRS:
            raise ValueError("Network validation exceeds the 250000 finite-volume pair budget; select a smaller affected subnetwork")


def _preflight(network):
    """Reject pathological geometry before allocating sampled arc envelopes."""
    primitives=0
    self_pair_work=0
    for item in list(network.get("nodes",[]))+list(network.get("supports",[])):
        if item.get("type") in PHYSICAL_NODE_TYPES or item.get("physical_envelope") is not None:
            primitives+=len((item.get("physical_envelope") or {}).get("capsules",[])) or 1
    for span in network.get("spans",[]):
        points=span.get("points",[])
        count=max(0,len(points)-1)
        fitting_count=0
        catalog=span.get("fitting_catalog") or {}
        if catalog.get("radius_mm") and catalog.get("body_radius_mm"):
            for a,b,c in zip(points,points[1:],points[2:]):
                turn=corner_angle(a,b,c)
                if 1e-6<turn<179.999:
                    count+=math.ceil(turn/5)
                    fitting_count+=1
        primitives+=count
        straight_count=max(0,len(points)-1)
        # These loops do not admit useful spatial pruning before examination.
        self_pair_work+=max(0,(straight_count-2)*(straight_count-1)//2)
        self_pair_work+=fitting_count*max(0,straight_count-2)
        self_pair_work+=fitting_count*(fitting_count-1)//2
    if primitives>MAX_PHYSICAL_PRIMITIVES or self_pair_work>MAX_NARROWPHASE_PAIRS:
        raise ValueError("Network validation exceeds the physical primitive or self-pair budget; select a smaller affected subnetwork")
    return primitives


def _check(identifier, status, message, evidence=None, components=None, severity="hard"):
    return {"id":identifier,"status":status,"severity":severity,"message":message,"evidence":evidence or {},"component_ids":list(dict.fromkeys(components or []))}


def _aggregate(identifier, failures, unknown, message, evidence=None, applicable=True, severity="hard"):
    status = "FAIL" if failures else "UNVERIFIED" if unknown else "PASS" if applicable else "NOT_APPLICABLE"
    details = dict(evidence or {})
    if failures: details["violations"] = failures
    if unknown: details["unverified"] = unknown
    ids = [str(item.get("id", item.get("span_id", ""))) for item in failures+unknown if isinstance(item,dict)]
    return _check(identifier,status,message,details,[item for item in ids if item],severity)


def _inside(point, box, margin=0):
    return all(box["min"][i]+margin-EPS <= point[i] <= box["max"][i]-margin+EPS for i in range(3))


def _distance(a,b): return math.dist(a,b)
def _sub(a,b): return [x-y for x,y in zip(a,b)]
def _dot(a,b): return sum(x*y for x,y in zip(a,b))
def _point(a,b,t): return [x+(y-x)*t for x,y in zip(a,b)]


def _outside_sphere(capsule, center, radius):
    """Retain portions outside a finite declared connection neighbourhood."""
    a,b = capsule["start"],capsule["end"]
    delta, relative = _sub(b,a),_sub(a,center)
    aa,bb,cc = _dot(delta,delta),2*_dot(relative,delta),_dot(relative,relative)-radius*radius
    if aa <= 1e-18:
        return [capsule] if cc > EPS else []
    discriminant = bb*bb-4*aa*cc
    if discriminant <= 0: return [capsule]
    root = math.sqrt(discriminant)
    low,high = max(0,(-bb-root)/(2*aa)),min(1,(-bb+root)/(2*aa))
    if low >= high: return [capsule]
    pieces=[]
    if low > 1e-12: pieces.append({**capsule,"end":_point(a,b,low)})
    if high < 1-1e-12: pieces.append({**capsule,"start":_point(a,b,high)})
    return pieces


def _outside_connections(capsules, connections):
    if not connections:return capsules
    result = list(capsules)
    for center,radius in connections:
        result=[part for capsule in result for part in _outside_sphere(capsule,center,radius)]
    return result


def _capsule_gap(a,b):
    _consume_work()
    return float(segment_distance(a["start"],a["end"],b["start"],b["end"]))-a["radius"]-b["radius"]


def _obstacle_gap(capsule, obstacle):
    if obstacle.get("capsules"):
        # Clipped source envelopes remain conservatively bounded by full
        # capsules here. The reported check does not claim exact BRep clearance.
        return min(_capsule_gap(capsule,item) for item in obstacle["capsules"])
    _consume_work()
    return float(segment_box_distance(capsule["start"],capsule["end"],obstacle["min"],obstacle["max"]))-capsule["radius"]


def _minimum_pair(left,right):
    if not left or not right:return math.inf
    if len(right)<8:return min(_capsule_gap(a,b) for a in left for b in right)
    cache=_PAIR_INDEXES.get()
    entry=cache.get(id(right)) if cache is not None else None
    if entry is None:
        index=SpatialCandidates([_capsule_bounds(cap) for cap in right])
        # Retain the list itself to prevent id reuse. The request-local cache
        # cannot retain stale geometry between source revisions or requests.
        if cache is not None and len(cache)<256 and sum(len(item[0]) for item in cache.values())+len(right)<=16384:
            cache[id(right)]=(right,index)
    else:index=entry[1]
    minimum=math.inf
    for cap in left:
        center=[(a+b)/2 for a,b in zip(cap["start"],cap["end"])]
        nearest=int(index.tree.query(center)[1])
        minimum=min(minimum,_capsule_gap(cap,right[nearest]))
        for candidate in index.query(cap["start"],cap["end"],margin=cap["radius"]+max(0,minimum)):
            minimum=min(minimum,_capsule_gap(cap,right[candidate]))
    return minimum


def _capsule_bounds(capsule):
    return ([min(a,b)-capsule["radius"] for a,b in zip(capsule["start"],capsule["end"])],
            [max(a,b)+capsule["radius"] for a,b in zip(capsule["start"],capsule["end"])])


def _union_bounds(capsules):
    boxes=[_capsule_bounds(cap) for cap in capsules]
    return ([min(box[0][axis] for box in boxes) for axis in range(3)],
            [max(box[1][axis] for box in boxes) for axis in range(3)]) if boxes else None


def _box_gap(left,right):
    distances=[max(right[0][i]-left[1][i],left[0][i]-right[1][i]) for i in range(3)]
    return math.sqrt(sum(max(0,value)**2 for value in distances)) if any(value>0 for value in distances) else max(distances)


def _component_pipe_gap(component,capsules):
    if component["representation"]=="aabb":
        return min((_obstacle_gap(cap,component) for cap in capsules),default=math.inf)
    return _minimum_pair(component["capsules"],capsules)


def _component_gap(left,right):
    if left["representation"]=="aabb" and right["representation"]=="aabb":
        _consume_work()
        return _box_gap((left["min"],left["max"]),(right["min"],right["max"]))
    if left["representation"]=="aabb":return _component_pipe_gap(left,right["capsules"])
    return _component_pipe_gap(right,left["capsules"])


def _component_obstacle_gap(component,obstacle):
    if component["representation"]!="aabb":
        return min((_obstacle_gap(cap,obstacle) for cap in component["capsules"]),default=math.inf)
    if obstacle.get("capsules"):
        return min(_obstacle_gap(cap,component) for cap in obstacle["capsules"])
    _consume_work()
    return _box_gap((component["min"],component["max"]),(obstacle["min"],obstacle["max"]))


def _support_contact_parts(component,span,capsules,clearance):
    """Exempt only primitives actually touching the declared attachment point.

    A later returning leg is not the same physical attachment, even if it enters
    the same contact ball. Spatial proximity never authorizes another joint.
    """
    center=component["contact_position"]
    radius=support_contact_radius(component,span,clearance)
    def key(cap):return ("pipe",cap.get("segment_index")) if cap["kind"]=="pipe" else ("fitting",cap["component_id"])
    attached={key(cap) for cap in capsules if float(segment_distance(center,center,cap["start"],cap["end"]))<=cap["radius"]+EPS}
    return [part for cap in capsules for part in (_outside_sphere(cap,center,radius) if key(cap) in attached else [cap])]


class _ObstacleIndex:
    """Conservative broadphase while retaining the exact minimum gap witness."""
    def __init__(self,obstacles):
        self.obstacles=obstacles
        bounds=[]
        for obstacle in obstacles:
            if obstacle.get("capsules"):
                boxes=[_capsule_bounds(cap) for cap in obstacle["capsules"]]
                bounds.append(([min(box[0][axis] for box in boxes) for axis in range(3)],
                               [max(box[1][axis] for box in boxes) for axis in range(3)]))
            else:bounds.append((obstacle["min"],obstacle["max"]))
        self.index=SpatialCandidates(bounds)
        self.total_queries=0
        self.candidate_pairs=0

    def candidates(self,capsules,clearance,force_all=False):
        self.total_queries+=len(self.obstacles)
        if not capsules or not self.obstacles:return []
        if force_all:
            self.candidate_pairs+=len(self.obstacles)
            return self.obstacles
        first=capsules[0]
        center=[(a+b)/2 for a,b in zip(first["start"],first["end"])]
        nearest=int(self.index.tree.query(center)[1])
        seed=min(_obstacle_gap(cap,self.obstacles[nearest]) for cap in capsules)
        selected={nearest}
        for cap in capsules:
            selected.update(self.index.query(cap["start"],cap["end"],margin=cap["radius"]+max(clearance,seed,0)))
        self.candidate_pairs+=len(selected)
        return [self.obstacles[index] for index in sorted(selected)]


def _axis_metadata(network):
    axes=network.get("coordinate_system",{})
    required=("up","fore_aft","athwartship")
    if any(not isinstance(axes.get(key),(list,tuple)) or len(axes[key])!=3 for key in required): return None
    vectors=[axes[key] for key in required]
    if any(abs(_dot(axis,axis)-1)>1e-6 for axis in vectors): return None
    if any(abs(_dot(vectors[a],vectors[b]))>1e-6 for a,b in ((0,1),(0,2),(1,2))): return None
    return axes


def _system_type(network,span):
    if span.get("system_type"): return span["system_type"].lower()
    systems=network.get("systems",[])
    if isinstance(systems,dict):
        item=systems.get(span.get("system_id"),{})
        return (item if isinstance(item,str) else item.get("type",item.get("system_type",""))).lower()
    for item in systems:
        if item.get("id")==span.get("system_id"):
            return str(item.get("type",item.get("system_type",""))).lower()
    return ""


def _connection_checks(nodes, spans):
    """Check engineering interfaces independently of mere graph adjacency.

    A port direction points out of its component into the attached pipe. Size
    changes need explicit per-span port dimensions on the supplied component;
    an anonymous junction never implies a compatible reducer.
    """
    failures, unknown, measurements = [], [], []
    direction_fail, direction_unknown, direction_rows = [], [], []
    flow_fail, flow_unknown, flow_rows = [], [], []
    for span in spans:
        for endpoint in ("from_node", "to_node"):
            node = nodes.get(span.get(endpoint))
            if node is None:
                continue
            constraints = node.get("constraints", {})
            port_sizes = constraints.get("port_nominal_diameters_mm", {})
            required = port_sizes.get(span["id"], node.get("nominal_diameter_mm"))
            actual = span.get("nominal_diameter_mm")
            row = {"id": node["id"], "span_id": span["id"],
                   "required_nominal_diameter_mm": required, "actual_nominal_diameter_mm": actual}
            measurements.append(row)
            if required is None or actual is None:
                unknown.append({**row, "reason": "component port or pipe nominal size missing"})
            elif abs(required-actual) > EPS:
                failures.append({**row, "reason": "pipe and declared component port sizes are incompatible"})
            required_outer = constraints.get("port_outer_diameters_mm", {}).get(span["id"])
            if required_outer is not None and span.get("outer_diameter_mm") is not None and abs(required_outer-span["outer_diameter_mm"]) > EPS:
                failures.append({**row, "required_outer_diameter_mm": required_outer,
                                 "actual_outer_diameter_mm": span["outer_diameter_mm"],
                                 "reason": "pipe outer diameter differs from the declared physical port"})
            directions = constraints.get("port_directions", {})
            expected = directions.get(span["id"], node.get("port_direction"))
            if expected is not None or node.get("type") == "equipment_port":
                tolerance = constraints.get("angular_tolerance_deg")
                if expected is None or tolerance is None:
                    direction_unknown.append({"id": node["id"], "span_id": span["id"],
                                              "reason": "component outward port direction and angular tolerance required"})
                else:
                    points = span.get("points", [])
                    delta = _sub(points[1], points[0]) if endpoint == "from_node" else _sub(points[-2], points[-1])
                    norm, expected_norm = math.sqrt(_dot(delta, delta)), math.sqrt(_dot(expected, expected))
                    if norm <= EPS or expected_norm <= EPS:
                        direction_fail.append({"id": node["id"], "span_id": span["id"], "reason": "zero-length connection tangent or port direction"})
                    else:
                        angle = math.degrees(math.acos(max(-1., min(1., _dot(delta, expected)/(norm*expected_norm)))))
                        row = {"id": node["id"], "span_id": span["id"], "angle_error_deg": angle,
                               "allowed_angle_error_deg": tolerance, "expected_outward_direction": expected}
                        direction_rows.append(row)
                        if angle > tolerance+EPS:
                            direction_fail.append(row)
            permitted = constraints.get("permitted_flow_directions", {}).get(span["id"])
            if permitted is not None:
                flow = span.get("flow_direction", span.get("constraints", {}).get("flow_direction"))
                if flow not in ("from_to", "to_from"):
                    flow_unknown.append({"id": node["id"], "span_id": span["id"], "reason": "declared flow direction required at constrained port"})
                else:
                    actual_flow = "out_of_node" if (flow == "from_to") == (endpoint == "from_node") else "into_node"
                    row = {"id": node["id"], "span_id": span["id"], "actual_flow": actual_flow, "required_flow": permitted}
                    flow_rows.append(row)
                    if actual_flow != permitted:
                        flow_fail.append(row)
    return [
        _aggregate("network.connection_compatibility", failures, unknown, "Pipe-to-component nominal and supplied physical port compatibility", {"connections": measurements}),
        _aggregate("connections.port_direction", direction_fail, direction_unknown, "Endpoint tangents against explicitly declared outward component directions", {"connections": direction_rows}, applicable=bool(direction_rows or direction_unknown or direction_fail)),
        _aggregate("connections.flow_direction", flow_fail, flow_unknown, "Declared flow direction at constrained component ports", {"connections": flow_rows}, applicable=bool(flow_rows or flow_unknown)),
    ]


def _validate_network(network, reference=None, permitted_span_ids=None):
    """Return at least twenty explicit checks; all evidence uses global mm.

    A reference is necessary to certify preservation against source geometry.
    Missing standards interpretation/catalog/environment data stays UNVERIFIED.
    """
    network=deepcopy(network)
    nodes={item["id"]:item for item in network.get("nodes",[])}
    spans=network.get("spans",[])
    reference=reference or None
    reference_nodes={item["id"]:item for item in (reference or {}).get("nodes",[])}
    reference_spans={item["id"]:item for item in (reference or {}).get("spans",[])}
    checks=[]
    selected_profile=network.get("standards_profile")
    checks.append(_check("standards.profile","PASS" if selected_profile==PROFILE_ID else "UNVERIFIED","Versioned supplied engineering standards; authority remains a user transcription",{"requested_profile":selected_profile,"applied_profile":PROFILE_ID,"source_authority":standards_profile()["authority"],"authority_verification_status":standards_profile()["verification_status"]}))
    clearance=requirement("clearance.shock")["value"]
    tolerance=float(network.get("tolerance_mm",network.get("connection_tolerance_mm",0.01)))
    coverage=network.get("coverage") or {}
    coverage_unknown=not coverage.get("complete",False) or coverage.get("omitted_count",0)>0 or coverage.get("unknown_count",0)>0
    fitting_data={span["id"]:fitting_envelopes(span) for span in spans}
    occupancy={span["id"]:span_occupied_segments(span,fitting_data[span["id"]]) for span in spans}
    occupancy_bounds={identifier:_union_bounds(capsules) for identifier,capsules in occupancy.items()}
    fitting_occupancy={identifier:[cap for cap in caps if cap["kind"]=="fitting"] for identifier,caps in occupancy.items()}
    obstacle_index=_ObstacleIndex(network.get("obstacles",[]))
    physical_nodes=[node for node in nodes.values() if node.get("type") in PHYSICAL_NODE_TYPES]
    components=component_geometry(network)
    component_envelopes={item["id"]:item for item in components if item["min"] is not None}
    component_unknown=[{"id":item["id"],"reason":"component physical geometry is incomplete","issues":item["issues"]} for item in components if item["status"]=="UNVERIFIED"]
    component_failures=[{"id":item["id"],"issues":item["issues"]} for item in components if item["status"]=="FAIL"]
    checks.append(_aggregate("components.physical_geometry",component_failures,component_unknown,
                             "Supplied finite component envelopes, dimensions and conservative representation consistency",
                             {"component_count":len(components),"known_envelope_count":len(component_envelopes),"exact_solid_verification":False},applicable=bool(components)))
    # 1. Explicit graph incidence and endpoint geometry. Proximity never creates
    # a graph edge in this independent checker.
    failures,unknown,adjacency=[],[],defaultdict(set)
    for span in spans:
        a,b=span.get("from_node"),span.get("to_node")
        if a not in nodes or b not in nodes:
            failures.append({"id":span["id"],"reason":"missing declared endpoint node"});continue
        if nodes[a].get("system_id")!=span.get("system_id") or nodes[b].get("system_id")!=span.get("system_id"):
            if not any(nodes[node].get("type")=="system_boundary" for node in (a,b)):
                failures.append({"id":span["id"],"reason":"cross-system connection without boundary component"})
        if len(span.get("points",[]))<2:
            failures.append({"id":span["id"],"reason":"missing route geometry"});continue
        errors=[_distance(span["points"][0],nodes[a]["position"]),_distance(span["points"][-1],nodes[b]["position"])]
        if max(errors)>tolerance:
            failures.append({"id":span["id"],"endpoint_error_mm":errors,"tolerance_mm":tolerance})
        adjacency[a].add(b);adjacency[b].add(a)
    for node in nodes.values():
        if not adjacency[node["id"]] and node.get("type")!="support": failures.append({"id":node["id"],"reason":"unconnected component"})
        attached=[span["id"] for span in spans if node["id"] in (span.get("from_node"),span.get("to_node"))]
        if len(attached) > 2 and node.get("type") not in ("tee","junction","system_boundary"):
            ports=node.get("constraints",{}).get("port_nominal_diameters_mm",{})
            if not all(identifier in ports for identifier in attached):
                failures.append({"id":node["id"],"reason":"branch incidence requires a declared tee/junction or explicit component ports","connected_span_ids":attached})
    for item in network.get("unresolved_connections",[]):
        if item.get("status","unresolved")!="resolved": unknown.append({"id":item.get("id"),"reason":"designer connectivity decision required"})
    if reference:
        if set(nodes)!=set(reference_nodes): failures.append({"id":network.get("id"),"reason":"component/node identity set changed"})
        if {item["id"] for item in spans}!=set(reference_spans): failures.append({"id":network.get("id"),"reason":"span identity set changed"})
        for span in spans:
            old=reference_spans.get(span["id"])
            if old and any(span.get(key)!=old.get(key) for key in ("from_node","to_node","system_id","cad_handle","instance_path","source_instances","source_member_ids","system_type","flow_direction","modifiable")):
                failures.append({"id":span["id"],"reason":"source connection/system/CAD identity changed"})
            if old and span.get("constraints",{}) != old.get("constraints",{}):
                failures.append({"id":span["id"],"reason":"source engineering constraints changed"})
            if old and span.get("manufacturing") != old.get("manufacturing"):
                failures.append({"id":span["id"],"reason":"source fabrication/weld evidence changed inside proposal"})
            if old and old.get("modifiable") is False and span.get("points") != old.get("points"):
                failures.append({"id":span["id"],"reason":"unmodifiable source span changed"})
        for node in nodes.values():
            old=reference_nodes.get(node["id"])
            if old and any(node.get(key)!=old.get(key) for key in ("type","system_id","cad_handle","instance_path","source_instances","fixed","constraints","movement_region","allowed_positions","port_direction","fitting_catalog")):
                failures.append({"id":node["id"],"reason":"source component identity or permitted movement changed"})
        if network.get("bounds") != reference.get("bounds"):
            failures.append({"id":network.get("id"),"reason":"permitted source volume changed"})
        if network.get("standards_overrides",{}) != reference.get("standards_overrides",{}):
            failures.append({"id":network.get("id"),"reason":"standards authority changed inside proposal"})
        source_tolerance=float(reference.get("tolerance_mm",reference.get("connection_tolerance_mm",0.01)))
        if tolerance!=source_tolerance:
            failures.append({"id":network.get("id"),"reason":"source connection tolerance changed inside proposal",
                             "source_tolerance_mm":source_tolerance,"actual_tolerance_mm":tolerance})
        for key in ("obstacles","compartments","coverage","supports","penetrations","systems","accessibility_coverage","unresolved_connections","standards_profile"):
            if network.get(key) != reference.get(key):
                failures.append({"id":network.get("id"),"reason":"source engineering context changed inside proposal","field":key})
    checks.append(_aggregate("network.connectivity",failures,unknown,"Declared topology, graph incidence and endpoint agreement",{"node_count":len(nodes),"span_count":len(spans),"connection_tolerance_mm":tolerance}))
    metadata_fail=[]
    if reference:
        fields=[key for key in set(network)|set(reference) if key not in ("nodes","spans") and network.get(key)!=reference.get(key)]
        if fields:metadata_fail.append({"id":network.get("id"),"changed_fields":sorted(fields),"reason":"source network context changed"})
        for items,source_items,geometry in ((spans,reference_spans,"points"),(list(nodes.values()),reference_nodes,"position")):
            for item in items:
                old=source_items.get(item["id"])
                if old is None:continue
                fields=[key for key in set(item)|set(old) if key!=geometry and item.get(key)!=old.get(key)]
                if fields:metadata_fail.append({"id":item["id"],"changed_fields":sorted(fields),"reason":"source engineering/component metadata changed"})
    checks.append(_aggregate("preservation.source_metadata",metadata_fail,
                             [] if reference else [{"id":network.get("id"),"reason":"source snapshot required to verify all engineering metadata"}],
                             "All source engineering metadata is immutable; only route points and permitted component positions may change"))
    scope_fail=[]
    if permitted_span_ids is not None:
        for span in spans:
            old=reference_spans.get(span["id"])
            if old is not None and span["id"] not in permitted_span_ids and span.get("points")!=old.get("points"):
                scope_fail.append({"id":span["id"],"reason":"unselected span geometry changed"})
        for node in nodes.values():
            old=reference_nodes.get(node["id"])
            if old is None or node.get("position")==old.get("position"):continue
            locked=[span["id"] for span in (reference or {}).get("spans",[]) if
                    node["id"] in (span.get("from_node"),span.get("to_node")) and span["id"] not in permitted_span_ids]
            if locked:scope_fail.append({"id":node["id"],"reason":"moved component touches unselected spans","unselected_span_ids":locked})
    checks.append(_aggregate("preservation.optimization_scope",scope_fail,[],
                             "Selected spans and all incident component movements are independently constrained",
                             {"permitted_span_ids":sorted(permitted_span_ids) if permitted_span_ids is not None else None},
                             applicable=permitted_span_ids is not None))
    checks.extend(_connection_checks(nodes, spans))
    # 2. Fixed ports cannot be inferred as preserved without a source snapshot.
    failures,unknown=[],[]
    for node in nodes.values():
        if not (node.get("fixed") or node.get("type")=="equipment_port"):continue
        old=reference_nodes.get(node["id"])
        if reference and old:
            error=_distance(node["position"],old["position"])
            if error>tolerance: failures.append({"id":node["id"],"movement_mm":error})
        elif not reference: unknown.append({"id":node["id"],"reason":"source snapshot required for preservation comparison"})
    checks.append(_aggregate("preservation.fixed_ports",failures,unknown,"Fixed equipment connection preservation",applicable=any(node.get("fixed") or node.get("type")=="equipment_port" for node in nodes.values())))
    failures,unknown,movement_rows=[],[],[]
    movable=[node for node in nodes.values() if node.get("fixed") is False]
    for node in movable:
        region=node.get("movement_region")
        allowed=node.get("allowed_positions") or []
        row={"id":node["id"],"position":node["position"],"movement_region":region,"allowed_positions":allowed}
        movement_rows.append(row)
        if region is None and not allowed:
            unknown.append({"id":node["id"],"reason":"designer-permitted component movement region or positions missing"})
        if region is not None and not _inside(node["position"],region):
            failures.append({**row,"reason":"component lies outside its designer-permitted movement region"})
        if allowed and not any(_distance(node["position"],point)<=tolerance for point in allowed):
            failures.append({**row,"reason":"component position is not an explicitly permitted location"})
    checks.append(_aggregate("preservation.component_movement",failures,unknown,"All movable component types retain designer-permitted locations",{"components":movement_rows},applicable=bool(movable)))
    # 3. Required intermediate compartments must be intersected by route
    # centerlines; the enclosing hull separately validates physical occupancy.
    failures,unknown=[],[]
    compartments={item["id"]:item for item in network.get("compartments",[])}
    from .collision import segment_box_interval
    required_count=0
    for span in spans:
        for identifier in span.get("constraints",{}).get("required_compartments",[]):
            required_count+=1
            box=compartments.get(identifier)
            if not box:unknown.append({"id":span["id"],"compartment":identifier,"reason":"compartment geometry missing"})
            elif not any(interval is not None and (interval[1]-interval[0])*math.dist(a,b)>EPS for a,b in zip(span["points"],span["points"][1:]) for interval in [segment_box_interval(a,b,box["min"],box["max"],tolerance=-EPS)]):
                failures.append({"id":span["id"],"compartment":identifier})
    checks.append(_aggregate("network.required_compartments",failures,unknown,"Required compartment traversal",{"required_count":required_count},applicable=required_count>0))
    # 4,5. Nominal/outer sizes and component dimensions preserve source values.
    for identifier,keys,items,old_items in (("preservation.pipe_diameter",("nominal_diameter_mm","outer_diameter_mm"),spans,reference_spans),("preservation.component_size",("dimensions","nominal_diameter_mm"),list(nodes.values()),reference_nodes)):
        failures,unknown=[],[]
        for item in items:
            old=old_items.get(item["id"])
            for key in keys:
                if key not in item or item[key] is None or (key=="dimensions" and not item[key]):
                    unknown.append({"id":item["id"],"missing":key})
                elif not reference:unknown.append({"id":item["id"],"reason":"source snapshot required for size preservation"})
                elif old is None or item.get(key)!=old.get(key):failures.append({"id":item["id"],"field":key,"source":(old or {}).get(key),"actual":item.get(key)})
        if identifier=="preservation.component_size" and reference:
            for span in spans:
                old=reference_spans.get(span["id"])
                if old and span.get("fitting_catalog")!=old.get("fitting_catalog"):
                    failures.append({"id":span["id"],"reason":"source catalog dimensions/specification changed"})
        checks.append(_aggregate(identifier,failures,unknown,"Source nominal/physical dimensions preserved"))
    # 6-10. Continuous finite-volume checks, with only finite connection-region
    # exceptions. Shared topology never excludes an entire neighboring span.
    pair_collisions,pair_clearance,fitting_collisions=[],[],[]
    minimum_pipe_gap=math.inf
    contact_exemptions=[]
    pruned_span_pairs=0
    component_measurements=defaultdict(list)
    for i,span in enumerate(spans):
        own=occupancy[span["id"]]
        for other in spans[i+1:]:
            if _box_gap(occupancy_bounds[span["id"]],occupancy_bounds[other["id"]])>max(clearance,minimum_pipe_gap):
                pruned_span_pairs+=1;continue
            shared=set((span.get("from_node"),span.get("to_node"))) & set((other.get("from_node"),other.get("to_node")))
            shared.discard(None)
            connections=[]
            for node_id in shared:
                if node_id not in nodes:continue
                region=connection_contact_radius(nodes[node_id],span,other,clearance)
                connections.append((nodes[node_id]["position"],region))
                contact_exemptions.append({"node_id":node_id,"spans":[span["id"],other["id"]],"radius_mm":region})
            left=_outside_connections(own,connections)
            right=_outside_connections(occupancy[other["id"]],connections)
            gap=_minimum_pair(left,right)
            minimum_pipe_gap=min(minimum_pipe_gap,gap)
            evidence={"id":span["id"],"other_id":other["id"],"surface_gap_mm":gap}
            if gap < -EPS:pair_collisions.append(evidence)
            if gap+EPS<clearance:pair_clearance.append(evidence)
            left_fittings=[a for a in left if a["kind"]=="fitting"] if connections else fitting_occupancy[span["id"]]
            right_fittings=[b for b in right if b["kind"]=="fitting"] if connections else fitting_occupancy[other["id"]]
            fitting_gap=min(_minimum_pair(left_fittings,right),_minimum_pair(left,right_fittings))
            if fitting_gap < -EPS:fitting_collisions.append({**evidence,"surface_gap_mm":fitting_gap})
        # Self-intersections away from adjacent straight/arc connections.
        straights=[item for item in own if item["kind"]=="pipe"]
        for aidx,a in enumerate(straights):
            for b in straights[aidx+2:]:
                gap=_capsule_gap(a,b)
                minimum_pipe_gap=min(minimum_pipe_gap,gap)
                row={"id":span["id"],"reason":"self-intersection","segments":[a["segment_index"],b["segment_index"]],"surface_gap_mm":gap}
                if gap < -EPS:pair_collisions.append(row)
                if gap+EPS<clearance:pair_clearance.append({**row,"reason":"nonincident portions of same route lack shock clearance"})
        elbows=fitting_data[span["id"]]["fittings"]
        for fitting_index,fitting in enumerate(elbows):
            for straight in straights:
                if straight["segment_index"] in (fitting["vertex_index"]-1,fitting["vertex_index"]):continue
                gap=_minimum_pair(fitting["capsules"],[straight])
                minimum_pipe_gap=min(minimum_pipe_gap,gap)
                if gap < -EPS:fitting_collisions.append({"id":span["id"],"fitting_id":fitting["id"],"segment_index":straight["segment_index"],"reason":"fitting intersects nonincident own pipe","surface_gap_mm":gap})
                if gap+EPS<clearance:pair_clearance.append({"id":span["id"],"fitting_id":fitting["id"],"segment_index":straight["segment_index"],"reason":"fitting lacks clearance to nonincident own pipe","surface_gap_mm":gap})
            for other_fitting in elbows[fitting_index+1:]:
                gap=_minimum_pair(fitting["capsules"],other_fitting["capsules"])
                if gap < -EPS:fitting_collisions.append({"id":span["id"],"fitting_id":fitting["id"],"other_id":other_fitting["id"],"reason":"fitting-to-fitting self-collision","surface_gap_mm":gap})
                if abs(fitting["vertex_index"]-other_fitting["vertex_index"])>1:
                    minimum_pipe_gap=min(minimum_pipe_gap,gap)
                    if gap+EPS<clearance:pair_clearance.append({"id":span["id"],"fitting_id":fitting["id"],"other_id":other_fitting["id"],"reason":"fitting-to-fitting nonincident same-route clearance","surface_gap_mm":gap})
    unknown_geometry=[{"id":span["id"],"reason":"physical outer diameter missing"} for span in spans if not span.get("outer_diameter_mm")]
    for node_id,envelope in component_envelopes.items():
        for span in spans:
            threshold=max(clearance,minimum_pipe_gap,envelope["constraints"].get("installation_clearance_mm",0))
            if _box_gap((envelope["min"],envelope["max"]),occupancy_bounds[span["id"]])>threshold:continue
            capsules=occupancy[span["id"]]
            endpoint_segments=set()
            if envelope["type"]=="support" and span["id"] in envelope["incident_span_ids"] and envelope.get("contact_radius_mm") and envelope.get("contact_position") is not None:
                capsules=_support_contact_parts(envelope,span,capsules,clearance)
            elif node_id in (span.get("from_node"),span.get("to_node")):
                # Only the actual endpoint leg connects to the body. Excluding
                # every primitive of an incident span hides returning legs and
                # elbows that penetrate their own valve or junction.
                if node_id==span.get("from_node"):endpoint_segments.add(0)
                if node_id==span.get("to_node"):endpoint_segments.add(len(span["points"])-2)
                body_extent=max(math.dist(envelope["position"],point) for point in itertools.product(*zip(envelope["min"],envelope["max"])))
                region=body_extent+(span.get("outer_diameter_mm") or 0)/2+clearance
                capsules=[part for cap in capsules for part in (
                    _outside_sphere(cap,envelope["position"],region)
                    if cap["kind"]=="pipe" and cap.get("segment_index") in endpoint_segments else [cap])]
            gap=_component_pipe_gap(envelope,capsules)
            minimum_pipe_gap=min(minimum_pipe_gap,gap)
            row={"id":node_id,"other_id":span["id"],"surface_gap_mm":gap,"reason":"physical component body versus pipe outside its endpoint contact leg"}
            if gap < -EPS:pair_collisions.append(row);fitting_collisions.append(row)
            if gap+EPS<clearance:pair_clearance.append(row)
            if envelope["type"]=="flange":
                installation_caps=[cap for cap in occupancy[span["id"]] if not (cap["kind"]=="pipe" and cap.get("segment_index") in endpoint_segments)]
                installation_gap=_component_pipe_gap(envelope,installation_caps)
                component_measurements[node_id].append({**row,"surface_gap_mm":installation_gap})
    bodies=list(component_envelopes.values())
    for index,left in enumerate(bodies):
        for right in bodies[index+1:]:
            if _box_gap((left["min"],left["max"]),(right["min"],right["max"]))>max(clearance,minimum_pipe_gap,left["constraints"].get("installation_clearance_mm",0),right["constraints"].get("installation_clearance_mm",0)):continue
            gap=_component_gap(left,right)
            minimum_pipe_gap=min(minimum_pipe_gap,gap)
            row={"id":left["id"],"other_id":right["id"],"surface_gap_mm":gap,"reason":"physical component body pair"}
            if gap < -EPS:pair_collisions.append(row);fitting_collisions.append(row)
            if gap+EPS<clearance:pair_clearance.append(row)
            component_measurements[left["id"]].append(row)
            component_measurements[right["id"]].append({**row,"id":right["id"],"other_id":left["id"]})
    checks.append(_aggregate("collision.pipe_to_pipe",pair_collisions,unknown_geometry,"Finite pipe/fitting envelopes against all other network spans",{"minimum_surface_gap_mm":minimum_pipe_gap if math.isfinite(minimum_pipe_gap) else None,"bounded_port_exemptions":contact_exemptions},applicable=len(spans)>1 or bool(pair_collisions)))
    obstacle_collisions=defaultdict(list)
    obstacle_clearance=[]
    obstacle_gaps=[]
    minimum_obstacle_gap=math.inf
    for span in spans:
        declared_contact=any(nodes.get(identifier,{}).get("constraints",{}).get("connected_obstacle_ids") for identifier in (span.get("from_node"),span.get("to_node")))
        for obstacle in obstacle_index.candidates(occupancy[span["id"]],clearance,force_all=declared_contact):
            capsules=occupancy[span["id"]]
            connected=[]
            for endpoint in (span.get("from_node"),span.get("to_node")):
                node=nodes.get(endpoint,{})
                constraints=node.get("constraints",{})
                if obstacle.get("id") in constraints.get("connected_obstacle_ids",[]):
                    radius=constraints.get("contact_radius_mm")
                    if radius is not None and 0<radius<=1000:connected.append((node["position"],radius))
            capsules=_outside_connections(capsules,connected)
            gap=min((_obstacle_gap(cap,obstacle) for cap in capsules),default=math.inf)
            minimum_obstacle_gap=min(minimum_obstacle_gap,gap)
            if math.isfinite(gap):obstacle_gaps.append({"id":span["id"],"obstacle_id":obstacle.get("id"),"kind":obstacle.get("kind","unknown"),"surface_gap_mm":gap})
            record={"id":span["id"],"obstacle_id":obstacle.get("id"),"kind":obstacle.get("kind","unknown"),"surface_gap_mm":gap}
            if gap < -EPS:
                obstacle_collisions[obstacle.get("kind","unknown")].append(record)
            if gap+EPS<clearance:obstacle_clearance.append(record)
            fitting_gap=min((_obstacle_gap(cap,obstacle) for cap in capsules if cap["kind"]=="fitting"),default=math.inf)
            if fitting_gap < -EPS:fitting_collisions.append({**record,"surface_gap_mm":fitting_gap})
    for node_id,envelope in component_envelopes.items():
        node=nodes.get(node_id,{})
        # Components are few and retain a full exact scan. Using an enclosing
        # sphere to select only nearby boxes could lose the true minimum gap.
        for obstacle in obstacle_index.candidates([{"start":envelope["position"],"end":envelope["position"],"radius":0}],clearance,force_all=True):
            constraints=envelope["constraints"]
            extent=max(math.dist(envelope["position"],point) for point in itertools.product(*zip(envelope["min"],envelope["max"])))
            if obstacle.get("id") in constraints.get("connected_obstacle_ids",[]) and constraints.get("contact_radius_mm",0)>=extent:
                continue  # Whole body lies within its explicitly bounded source connection.
            gap=_component_obstacle_gap(envelope,obstacle)
            minimum_obstacle_gap=min(minimum_obstacle_gap,gap)
            record={"id":node_id,"obstacle_id":obstacle.get("id"),"kind":obstacle.get("kind","unknown"),"surface_gap_mm":gap,"reason":"physical valve/junction body"}
            obstacle_gaps.append(record)
            component_measurements[node_id].append(record)
            if gap < -EPS:
                obstacle_collisions[obstacle.get("kind","unknown")].append(record)
                fitting_collisions.append(record)
            if gap+EPS<clearance:obstacle_clearance.append(record)
    environment_unknown=[{"id":network.get("id"),"reason":"environment extraction incomplete or unknown objects omitted","coverage":coverage}] if coverage_unknown else []
    duct_kinds={"hvac","duct","wireway"}
    structure_kinds={"structure","stiffener","bulkhead","deck","equipment","unknown","ladder","access","walkway"}
    checks.append(_aggregate("collision.pipe_to_duct",[row for kind,rows in obstacle_collisions.items() if kind in duct_kinds for row in rows],environment_unknown+unknown_geometry,"Continuous clearance to ducts and wireways",applicable=any(item.get("kind") in duct_kinds for item in network.get("obstacles",[])) or bool(environment_unknown)))
    checks.append(_aggregate("collision.pipe_to_structure",[row for kind,rows in obstacle_collisions.items() if kind not in duct_kinds for row in rows],environment_unknown+unknown_geometry,"Continuous clearance to structure, equipment and retained environment geometry",applicable=bool(network.get("obstacles")) or bool(environment_unknown)))
    fitting_unknown=list(component_unknown)
    for span in spans:
        data=fitting_data[span["id"]]
        for issue in data["issues"]:
            if issue["code"].startswith("missing_"):fitting_unknown.append({"id":span["id"],**issue})
    checks.append(_aggregate("collision.fitting_envelopes",fitting_collisions,fitting_unknown+environment_unknown,"Conservative circular elbow sweeps against obstacles and neighboring spans",{"fitting_count":sum(len(item["fittings"]) for item in fitting_data.values()),"arc_chord_max_degrees":5},applicable=any(data["fittings"] or data["issues"] for data in fitting_data.values())))
    minimum_gap=min(minimum_pipe_gap,minimum_obstacle_gap)
    checks.append(_aggregate("clearance.shock",pair_clearance+obstacle_clearance,environment_unknown+unknown_geometry+fitting_unknown,"Minimum surface clearance outside declared connected-port regions",{"required_mm":clearance,"minimum_surface_gap_mm":minimum_gap if math.isfinite(minimum_gap) else None,"obstacle_measurements":obstacle_gaps,"bounded_port_exemptions":contact_exemptions},applicable=bool(network.get("obstacles")) or len(spans)>1 or math.isfinite(minimum_pipe_gap) or bool(environment_unknown)))
    # Physical hull containment includes fitting sweep radii, not just points.
    failures,unknown=[],[]
    bounds=network.get("bounds")
    if not bounds:unknown.append({"id":network.get("id"),"reason":"permitted structure bounds missing"})
    else:
        for span in spans:
            for cap in occupancy[span["id"]]:
                if not all(_inside(point,bounds,cap["radius"]) for point in (cap["start"],cap["end"])):
                    failures.append({"id":span["id"],"component_id":cap["component_id"],"radius_mm":cap["radius"]})
        for node_id,body in component_envelopes.items():
            if not _inside(body["min"],bounds) or not _inside(body["max"],bounds):failures.append({"id":node_id,"reason":"physical component body outside permitted volume","body_min":body["min"],"body_max":body["max"]})
    checks.append(_aggregate("routing.permitted_bounds",failures,unknown,"Finite envelopes inside permitted ship volume"))
    # 11. Every individual flow-direction segment, with explicit ship axes.
    failures,unknown,pitch_rows=[],[],[]
    axes=_axis_metadata(network)
    pitch=requirement("drainage.pitch")
    applicable=False
    for span in spans:
        system_type=_system_type(network,span)
        if not system_type:
            unknown.append({"id":span["id"],"reason":"system type missing; drainage applicability unknown"});continue
        if system_type not in ("drainage","drain","vent","gravity"):continue
        applicable=True
        flow=span.get("flow_direction",span.get("constraints",{}).get("flow_direction"))
        if axes is None or flow not in ("from_to","to_from"):
            unknown.append({"id":span["id"],"reason":"explicit orthonormal ship axes and flow direction required"});continue
        points=span["points"] if flow=="from_to" else list(reversed(span["points"]))
        for index,(a,b) in enumerate(zip(points,points[1:])):
            delta=_sub(b,a)
            fore,athwart=abs(_dot(delta,axes["fore_aft"])),abs(_dot(delta,axes["athwartship"]))
            fall=-_dot(delta,axes["up"])
            required=fore*pitch["fore_aft"]+athwart*pitch["athwartship"]
            record={"id":span["id"],"segment_index":index,"fall_mm":fall,"fore_aft_run_mm":fore,"athwartship_run_mm":athwart,"required_fall_mm":required,"combination":"sum of axis-projected requirements"}
            pitch_rows.append(record)
            if fall+EPS<required:failures.append(record)
    checks.append(_aggregate("drainage.pitch",failures,unknown,"Directional pitch on every segment; intermediate reverse slopes prohibited",{"segments":pitch_rows,"fore_aft_ratio":pitch["fore_aft"],"athwartship_ratio":pitch["athwartship"]},applicable=applicable))
    direction_warnings,direction_unknown=[],[]
    pressure_spans=[span for span in spans if _system_type(network,span) in ("pressurized","pressure")]
    for span in pressure_spans:
        if axes is None:
            direction_unknown.append({"id":span["id"],"reason":"ship axes required for horizontal/vertical routing preference"});continue
        for index,(a,b) in enumerate(zip(span["points"],span["points"][1:])):
            delta=_sub(b,a)
            axis_components=[abs(_dot(delta,axes[key])) for key in ("fore_aft","athwartship","up")]
            if sum(component>EPS for component in axis_components)>1:
                direction_warnings.append({"id":span["id"],"segment_index":index,"ship_axis_components_mm":axis_components})
    checks.append(_aggregate("routing.orthogonal",direction_warnings,direction_unknown,"Prefer horizontal/vertical routing for pressurized systems",applicable=bool(pressure_spans),severity="preference"))
    # 12,13. No nominal-size interpolation; physical elbow takeouts consume
    # available straight lengths before the between-bends standard is checked.
    radius_fail,radius_unknown,straight_fail,straight_unknown=[],[],[],[]
    bend_count=0
    for span in spans:
        data=fitting_data[span["id"]]
        turns=[corner_angle(a,b,c) for a,b,c in zip(span["points"],span["points"][1:],span["points"][2:]) if corner_angle(a,b,c)>EPS]
        if not turns:continue
        bend_count+=len(turns)
        standard=bend_requirement(span.get("nominal_diameter_mm"))
        if standard is None:
            row={"id":span["id"],"nominal_diameter_mm":span.get("nominal_diameter_mm"),"reason":"nominal size not covered by supplied bend-radius table"}
            radius_unknown.append(row);straight_unknown.append(row)
        for issue in data["issues"]:
            target=radius_unknown if issue["code"].startswith("missing_") else radius_fail
            target.append({"id":span["id"],**issue})
        if data["fittings"] and not data["catalog_verified"]:
            radius_unknown.append({"id":span["id"],"reason":"fitting catalog provisional or approval/source missing"})
        if standard:
            for fitting in data["fittings"]:
                if fitting["radius_mm"]+EPS<standard["radius_mm"]:
                    radius_fail.append({"id":fitting["id"],"actual_radius_mm":fitting["radius_mm"],"required_radius_mm":standard["radius_mm"]})
            for straight in data["straight_lengths"]:
                if straight["net_straight_mm"] < -EPS or (straight["between_bends"] and straight["net_straight_mm"]+EPS<standard["straight_mm"]):
                    straight_fail.append({"id":span["id"],**straight,"required_net_straight_mm":standard["straight_mm"] if straight["between_bends"] else 0})
        if not data["fittings"] and turns:straight_unknown.append({"id":span["id"],"reason":"physical fitting tangent geometry unavailable"})
    checks.append(_aggregate("fittings.bend_radius",radius_fail,radius_unknown,"Physical catalog bend radii and supported turn angles",{"bend_count":bend_count},applicable=bend_count>0))
    checks.append(_aggregate("fittings.straight_between_bends",straight_fail,straight_unknown,"Net straights after elbow tangent takeouts",applicable=bend_count>0))
    component_catalog_unknown=[]
    standalone_components=[item for item in components if item["id"] not in nodes]
    for node in physical_nodes+standalone_components:
        catalog=node.get("fitting_catalog") or {}
        if catalog.get("status")!="verified" or not catalog.get("source"):
            component_catalog_unknown.append({"id":node["id"],"reason":"valve/junction component specification and catalog approval not verified"})
    checks.append(_aggregate("fittings.component_catalog",[],component_catalog_unknown,"Physical valve, tee, flange, reducer and support envelopes do not imply commercial catalog approval",applicable=bool(physical_nodes or standalone_components)))
    availability_fail,availability_unknown,availability_rows=[],[],[]
    required_components=[span for span in spans
                         if any(corner_angle(a,b,c)>EPS for a,b,c in zip(span["points"],span["points"][1:],span["points"][2:]))]
    component_nodes=[node for node in nodes.values() if node in physical_nodes or
                     node.get("fitting_catalog") is not None or node.get("constraints",{}).get("requires_fitting") is True]
    for component in required_components+component_nodes+standalone_components:
        catalog=component.get("fitting_catalog") or {}
        row={"id":component["id"],"available":catalog.get("available"),"source":catalog.get("source")}
        availability_rows.append(row)
        if catalog.get("available") is False:
            availability_fail.append({**row,"reason":"required fitting explicitly unavailable in supplied component specification"})
        elif catalog.get("available") is not True or catalog.get("status")!="verified" or not catalog.get("source"):
            availability_unknown.append({**row,"reason":"required fitting availability and approved source not confirmed"})
    checks.append(_aggregate("fittings.availability",availability_fail,availability_unknown,"Required component availability is independent of geometric fit",{"components":availability_rows},applicable=bool(availability_rows)))
    reducer_fail,reducer_unknown,reducer_rows=[],[],[]
    for node in nodes.values():
        constraints=node.get("constraints",{})
        sizes=constraints.get("port_nominal_diameters_mm",{})
        different=len(set(sizes.values()))>1
        if node.get("type")!="reducer" and not different:continue
        incident=[span for span in spans if node["id"] in (span.get("from_node"),span.get("to_node"))]
        row={"id":node["id"],"port_nominal_diameters_mm":sizes,"reducer_approved":constraints.get("reducer_approved"),"source":constraints.get("reducer_source")}
        reducer_rows.append(row)
        if node.get("type")=="reducer" and len(incident)!=2:
            reducer_fail.append({**row,"reason":"a declared inline reducer requires exactly two incident pipe connections"})
        if constraints.get("reducer_approved") is False:
            reducer_fail.append({**row,"reason":"dimensional transition explicitly not approved"})
        elif constraints.get("reducer_approved") is not True or not constraints.get("reducer_source"):
            reducer_unknown.append({**row,"reason":"explicit supplied transition permission/source required; no automatic pipe resizing"})
        if any(span["id"] not in sizes for span in incident):
            reducer_unknown.append({**row,"reason":"all incident reducer/transition nominal port sizes must be supplied"})
    checks.append(_aggregate("components.reducer_compatibility",reducer_fail,reducer_unknown,"Explicit component port transitions and supplied designer permission",{"components":reducer_rows},applicable=bool(reducer_rows)))
    install_fail,install_unknown,install_rows=[],[],[]
    for component in components:
        if component["type"]!="flange":continue
        required=component["constraints"].get("installation_clearance_mm")
        if required is None:
            install_unknown.append({"id":component["id"],"reason":"flange installation/tool clearance requirement not supplied"});continue
        if component["min"] is None:
            install_unknown.append({"id":component["id"],"reason":"physical flange envelope unavailable"});continue
        measurements=[row for row in component_measurements[component["id"]] if math.isfinite(row["surface_gap_mm"])]
        actual=min((row["surface_gap_mm"] for row in measurements),default=None)
        row={"id":component["id"],"required_mm":required,"measured_minimum_mm":actual,"source":"supplied component installation_clearance_mm; not an added official rule","measurements":measurements,"scope":"Conservative offset clearance to other bodies and nonconnecting pipe portions; exact bolting/tool motion not verified"}
        install_rows.append(row)
        if actual is not None and actual+EPS<required:install_fail.append(row)
        if coverage_unknown:install_unknown.append({"id":component["id"],"reason":"installation surrounding geometry extraction incomplete"})
    checks.append(_aggregate("components.installation_clearance",install_fail,install_unknown,"Supplied flange installation offsets, separate from ship shock clearance",{"components":install_rows},applicable=bool(install_rows or install_unknown)))
    support_fail,support_unknown,support_rows=[],[],[]
    span_map={span["id"]:span for span in spans}
    for component in components:
        if component["type"]!="support":continue
        identifiers=component["incident_span_ids"]
        contact,radius=component.get("contact_position"),component.get("contact_radius_mm")
        if len(identifiers)!=1 or identifiers[0] not in span_map or contact is None or radius is None:
            support_unknown.append({"id":component["id"],"reason":"one explicit attached span, contact position and bounded contact radius required"});continue
        span=span_map[identifiers[0]]
        distance=min(float(segment_distance(contact,contact,cap["start"],cap["end"]))-cap["radius"] for cap in occupancy[span["id"]])
        row={"id":component["id"],"span_id":span["id"],"contact_surface_gap_mm":distance,"contact_radius_mm":radius,
             "applied_contact_radius_mm":support_contact_radius(component,span,clearance)}
        support_rows.append(row)
        if distance>tolerance:support_fail.append({**row,"reason":"declared support contact is not on or within its attached physical pipe"})
        if component["min"] is None:
            support_unknown.append({**row,"reason":"support body geometry missing; attachment reach cannot be verified"})
        else:
            body_gap=_component_pipe_gap(component,[{"start":contact,"end":contact,"radius":0}])
            allowance=max((cap["radius"] for cap in occupancy[span["id"]] if float(segment_distance(contact,contact,cap["start"],cap["end"]))<=cap["radius"]+tolerance),default=(span.get("outer_diameter_mm") or 0)/2)
            row["body_to_contact_gap_mm"],row["physical_pipe_allowance_mm"]=body_gap,allowance
            if body_gap>allowance+tolerance:
                support_fail.append({**row,"reason":"declared support body does not reach its pipe attachment"})
    checks.append(_aggregate("components.support_attachment",support_fail,support_unknown,"Support contact exceptions remain local to one explicitly attached pipe",{"components":support_rows},applicable=bool(support_rows or support_unknown)))
    # 14-16 plus manufacturing planarity/cutbacks are computed independently
    # by the manufactured-piece helper (not whole-network length).
    from .network_manufacturing import manufacture_network
    manufacturing=manufacture_network(network,_fitting_assessments=fitting_data)
    checks.extend(manufacturing.get("checks",[]))
    # 17. Headroom/obstruction and supports. Absence of access/support geometry
    # is uncertainty, not an affirmative installation approval.
    access_fail,access_unknown,headroom_rows=[],[],[]
    walkways=[item for item in network.get("obstacles",[]) if item.get("kind")=="walkway"]
    for walkway in walkways:
        if axes is None:
            access_unknown.append({"id":walkway.get("id"),"reason":"explicit orthonormal ship axes required for walkway headroom"});continue
        floor_point=walkway.get("floor_point")
        floor=walkway.get("floor_elevation_mm")
        if floor_point is not None:floor=_dot(floor_point,axes["up"])
        elif axes["up"] != [0,0,1]:
            access_unknown.append({"id":walkway.get("id"),"reason":"rotated ship frame requires a global floor_point"});continue
        if floor is None:
            access_unknown.append({"id":walkway.get("id"),"reason":"walkway floor elevation/point missing"});continue
        frame=[axes["fore_aft"],axes["athwartship"],axes["up"]]
        corners=[[_dot(point,axis) for axis in frame] for point in itertools.product(*zip(walkway["min"],walkway["max"]))]
        caps=[(span["id"],cap) for span in spans for cap in occupancy[span["id"]]]+[(identifier,cap) for identifier,body in component_envelopes.items() for cap in body["capsules"]]
        for component_id,cap in caps:
            start,end=[_dot(cap["start"],axis) for axis in frame],[_dot(cap["end"],axis) for axis in frame]
            low=[min(point[axis] for point in corners)-cap["radius"] for axis in (0,1)]+[-1e30]
            high=[max(point[axis] for point in corners)+cap["radius"] for axis in (0,1)]+[1e30]
            intersection=segment_box_interval(start,end,low,high,tolerance=EPS)
            if intersection is not None:
                actual=min(_point(start,end,parameter)[2] for parameter in intersection)-cap["radius"]-floor
                row={"id":component_id,"walkway_id":walkway.get("id"),"headroom_mm":actual,"required_mm":requirement("access.headroom")["value"],"method":"ship-up projection over conservative walkway footprint"}
                headroom_rows.append(row)
                if actual+EPS<requirement("access.headroom")["value"]:access_fail.append(row)
        walkway_low=[min(point[axis] for point in corners) for axis in (0,1)]
        walkway_high=[max(point[axis] for point in corners) for axis in (0,1)]
        for identifier,body in component_envelopes.items():
            if body["representation"]!="aabb":continue
            projected=[[_dot(point,axis) for axis in frame] for point in itertools.product(*zip(body["min"],body["max"]))]
            low=[min(point[axis] for point in projected) for axis in range(3)]
            high=[max(point[axis] for point in projected) for axis in range(3)]
            if all(low[i]<=walkway_high[i]+EPS and high[i]>=walkway_low[i]-EPS for i in (0,1)):
                actual=low[2]-floor
                row={"id":identifier,"walkway_id":walkway.get("id"),"headroom_mm":actual,"required_mm":requirement("access.headroom")["value"],"method":"ship-up projection of supplied conservative finite component bounds"}
                headroom_rows.append(row)
                if actual+EPS<requirement("access.headroom")["value"]:access_fail.append(row)
    if not network.get("accessibility_coverage",{}).get("complete",False):access_unknown.append({"id":network.get("id"),"reason":"installation access/tripping/passage envelope coverage not confirmed"})
    checks.append(_aggregate("access.accessibility",access_fail,access_unknown,"Walkway headroom and known passage/tripping envelope preservation",{"headroom_measurements":headroom_rows}))
    for orientation,standard_id in (("vertical","supports.vertical"),("horizontal","supports.horizontal")):
        relevant=[item for item in network.get("supports",[]) if item.get("orientation")==orientation]
        failures,unknown=[],[]
        for support in relevant:
            measured=support.get("center_to_structure_mm") if orientation=="vertical" else support.get("edge_to_structure_mm")
            center,structure=support.get("pipe_center"),support.get("structure_point")
            span=next((item for item in spans if item["id"]==support.get("span_id")),None)
            if center is not None and structure is not None and axes is not None and span is not None:
                delta=_sub(structure,center)
                vertical=abs(_dot(delta,axes["up"]))
                horizontal=math.sqrt(max(0,_dot(delta,delta)-vertical*vertical))
                measured=vertical if orientation=="vertical" else horizontal-(span.get("outer_diameter_mm") or 0)/2
                center_gap=min(float(segment_distance(center,center,a,b)) for a,b in zip(span["points"],span["points"][1:]))
                if center_gap>tolerance:unknown.append({"id":support.get("id"),"reason":"declared support pipe center does not lie on referenced span","distance_mm":center_gap})
                if (orientation=="vertical" and horizontal>tolerance) or (orientation=="horizontal" and vertical>tolerance):unknown.append({"id":support.get("id"),"reason":"support geometry not aligned with declared hanger orientation"})
                if measured < -EPS:failures.append({"id":support.get("id"),"measured_mm":measured,"reason":"structure point lies inside pipe physical radius"})
            else:
                unknown.append({"id":support.get("id"),"reason":"support span, center, structure position and explicit ship axes required for geometric verification"})
            if measured is None:unknown.append({"id":support.get("id"),"reason":"required center/edge measurement missing"})
            elif measured>requirement(standard_id)["value"]+EPS:failures.append({"id":support.get("id"),"measured_mm":measured,"limit_mm":requirement(standard_id)["value"]})
        if not relevant:unknown.append({"id":network.get("id"),"reason":"support geometry/layout absent"})
        checks.append(_aggregate(standard_id,failures,unknown,"Hanger distance evaluated using the specified reference surface",severity=requirement(standard_id)["severity"]))
    # 18. Valve motion is allowed inside supplied movement region. Stem height
    # ranges remain preferences, not hard routing constraints.
    failures,unknown,position_fail=[],[],[]
    valves=[node for node in nodes.values() if node.get("type")=="valve"]
    limits=requirement("valves.height")
    for valve in valves:
        orientation,height=valve.get("stem_orientation"),valve.get("stem_height_mm")
        if orientation not in ("horizontal","up","down") or height is None:
            unknown.append({"id":valve["id"],"reason":"stem orientation/height above access surface missing"})
        else:
            rule=limits[orientation]
            if height<rule.get("min",-math.inf)-EPS or height>rule.get("max",math.inf)+EPS:failures.append({"id":valve["id"],"height_mm":height,"recommended_range_mm":rule})
        if not valve.get("fixed",False):
            region=valve.get("movement_region")
            if region and not _inside(valve["position"],region):position_fail.append({"id":valve["id"],"reason":"valve outside permitted movement region"})
            elif not region and not valve.get("allowed_positions"):unknown.append({"id":valve["id"],"reason":"movable valve permitted space unavailable"})
            elif valve.get("allowed_positions") and not any(_distance(valve["position"],point)<=tolerance for point in valve["allowed_positions"]):position_fail.append({"id":valve["id"],"reason":"valve position not in explicit permitted locations"})
    checks.append(_aggregate("valves.height",failures,unknown,"Recommended valve stem heights; outside ranges allowed but discouraged",applicable=bool(valves),severity="preference"))
    checks.append(_aggregate("valves.permitted_space",position_fail,[],"Movable valves remain within designer-defined spaces",applicable=bool(valves)))
    # 19. The inconsistent 2-inch / 50-mm transcription is never resolved by
    # an implicit conversion. A documented designer authority is mandatory.
    penetrations=network.get("penetrations",[])
    failures,unknown,separation_rows=[],[],[]
    authority=network.get("standards_overrides",{}).get("penetrations.separation",{})
    limit=authority.get("value_mm") if authority.get("source_reference") and authority.get("approved_by") else None
    if penetrations and (limit is None or not isinstance(limit,(int,float)) or not math.isfinite(limit) or limit<=0):
        unknown.append({"id":network.get("id"),"reason":"source states inconsistent 2-inch / 50-mm; authoritative value/source/approval required","source_values":requirement("penetrations.separation")["source_values"]})
        limit=None
    for index,penetration in enumerate(penetrations):
        center=penetration.get("position")
        radius=penetration.get("radius_mm")
        if center is None or radius is None:
            unknown.append({"id":penetration.get("id"),"reason":"penetration opening geometry missing"});continue
        probe={"start":center,"end":center,"radius":radius}
        if limit is not None:
            for obstacle in network.get("obstacles",[]):
                if obstacle.get("kind")!="stiffener":continue
                gap=_obstacle_gap(probe,obstacle)
                row={"id":penetration.get("id"),"other_id":obstacle.get("id"),"surface_gap_mm":gap,"required_mm":limit}
                separation_rows.append(row)
                if gap+EPS<limit:failures.append(row)
            for other in penetrations[index+1:]:
                if other.get("position") is None or other.get("radius_mm") is None:continue
                gap=_distance(center,other["position"])-radius-other["radius_mm"]
                row={"id":penetration.get("id"),"other_id":other.get("id"),"surface_gap_mm":gap,"required_mm":limit}
                separation_rows.append(row)
                if gap+EPS<limit:failures.append(row)
    checks.append(_aggregate("penetrations.separation",failures,unknown,"Penetration surface spacing requires resolved authoritative standard",{"measurements":separation_rows,"authority":authority or "UNRESOLVED"},applicable=bool(penetrations)))
    offset_fail,offset_unknown=[],[]
    for penetration in penetrations:
        applies=penetration.get("offset_required")
        if applies is None:offset_unknown.append({"id":penetration.get("id"),"reason":"deck/bulkhead offset applicability not established"});continue
        if not applies:continue
        pair=next((item for item in penetrations if item.get("id")==penetration.get("paired_penetration_id")),None)
        axis=penetration.get("surface_normal")
        diameter=penetration.get("pipe_outer_diameter_mm")
        if not pair or axis is None or diameter is None or penetration.get("position") is None or pair.get("position") is None or abs(_dot(axis,axis)-1)>1e-6:
            offset_unknown.append({"id":penetration.get("id"),"reason":"paired penetration, diameter and unit surface normal required"});continue
        delta=_sub(pair["position"],penetration["position"])
        offset=math.sqrt(max(0,_dot(delta,delta)-_dot(delta,axis)**2))
        if offset+EPS<diameter:offset_fail.append({"id":penetration.get("id"),"paired_id":pair.get("id"),"directional_offset_mm":offset,"required_mm":diameter})
    checks.append(_aggregate("penetrations.directional_offset",offset_fail,offset_unknown,"Prohibited straight deck/bulkhead runs require one-diameter transverse offset",applicable=bool(penetrations)))
    # 20. Canonical representation has exact source-preserving frame/units.
    failures,unknown=[],[]
    if network.get("units")!="mm":failures.append({"id":network.get("id"),"reason":"canonical network must be mm"})
    if network.get("coordinate_system",{}).get("frame")!="WCS":unknown.append({"id":network.get("id"),"reason":"explicit global WCS frame required"})
    if reference:
        if reference.get("drawing")!=network.get("drawing"):failures.append({"id":network.get("id"),"reason":"source drawing identity/revision changed"})
        if reference.get("coordinate_system")!=network.get("coordinate_system"):failures.append({"id":network.get("id"),"reason":"source coordinate frame changed"})
    for span in spans:
        for index,point in enumerate(span.get("points",[])):
            if len(point)!=3 or not all(isinstance(value,(int,float)) and math.isfinite(value) for value in point):failures.append({"id":span["id"],"point_index":index,"reason":"nonfinite or invalid global coordinate"})
    checks.append(_aggregate("export.coordinate_consistency",failures,unknown,"Canonical finite global coordinates and source drawing frame preserved",{"units":network.get("units"),"frame":network.get("coordinate_system",{}).get("frame"),"drawing":network.get("drawing")}))
    checks.append(_aggregate("environment.coverage",[],environment_unknown,"Unknown and omitted CAD geometry surfaced; retained envelopes checked",{"coverage":coverage}))
    preference_failures=sum(item["status"]=="FAIL" and item["severity"]=="preference" for item in checks)
    hard_failures=sum(item["status"]=="FAIL" and item["severity"]=="hard" for item in checks)
    unverified=sum(item["status"]=="UNVERIFIED" for item in checks)
    geometric_ids={"network.connectivity","collision.pipe_to_pipe","collision.pipe_to_duct","collision.pipe_to_structure","collision.fitting_envelopes","routing.permitted_bounds","environment.coverage","components.physical_geometry","components.support_attachment"}
    geometric=all(item["status"] in ("PASS","NOT_APPLICABLE") for item in checks if item["id"] in geometric_ids)
    engineering=hard_failures==0 and not any(item["severity"]=="hard" and item["status"]=="UNVERIFIED" for item in checks)
    manufacturing_checks=[item for item in checks if item["id"].startswith("manufacturing.") or item["id"].startswith("fittings.") or item["id"].startswith("components.") or item["id"]=="network.connection_compatibility"]
    manufacturable=all(item["status"] in ("PASS","NOT_APPLICABLE") for item in manufacturing_checks)
    status="FAIL" if hard_failures else "UNVERIFIED" if unverified else "PASS"
    return {"status":status,"checks":checks,"hard_failures":hard_failures,"unverified":unverified,"preference_warnings":preference_failures,"geometrically_feasible":geometric,"engineering_constraint_feasible":engineering,"manufacturability_verified":manufacturable,"standards_profile":PROFILE_ID,"standards_authority":standards_profile()["authority"],"scope":"Supplied explicit topology and conservative geometry; no installation approval or industrial certification","manufacturing":manufacturing,"spatial_index":{"method":"conservative bounding-sphere cKDTree with exact narrowphase","possible_obstacle_pairs":obstacle_index.total_queries,"evaluated_obstacle_pairs":obstacle_index.candidate_pairs,"possible_span_pairs":len(spans)*(len(spans)-1)//2,"pruned_span_pairs":pruned_span_pairs,"minimum_clearance_scope":"exact over supplied conservative envelopes; full scan retained for bounded source-contact exceptions"}}


def validate_network(network, reference=None, *, permitted_span_ids=None):
    """Validate independently within bounded physical-envelope/pair budgets.

    Budget exhaustion raises ValueError, never returns a partially checked PASS.
    Context-local counters keep simultaneous API validations independent.
    """
    if permitted_span_ids is not None:
        if reference is None:
            raise ValueError("An optimization scope requires the original reference network")
        if not isinstance(permitted_span_ids,(list,tuple,set,frozenset)) or len(permitted_span_ids)>128 or any(not isinstance(item,str) for item in permitted_span_ids):
            raise ValueError("permitted_span_ids must be a bounded list or set of existing span identifiers")
        permitted_span_ids=set(permitted_span_ids)
        if permitted_span_ids-{item["id"] for item in reference.get("spans",[])}:
            raise ValueError("Optimization scope names unknown source spans")
    primitives=_preflight(network)
    counter=[0]
    token=_WORK_BUDGET.set(counter)
    index_token=_PAIR_INDEXES.set({})
    try:
        result=_validate_network(network,reference,permitted_span_ids)
        result["work_budget"]={"physical_primitives":primitives,"max_physical_primitives":MAX_PHYSICAL_PRIMITIVES,"narrowphase_pairs":counter[0],"max_narrowphase_pairs":MAX_NARROWPHASE_PAIRS}
        result["work_budget"]["request_local_index_entries"]=len(_PAIR_INDEXES.get())
        return result
    finally:
        _PAIR_INDEXES.reset(index_token)
        _WORK_BUDGET.reset(token)


def validation_delta(before, after):
    """Compare independent reports without rerunning or trusting the optimizer.

    Measurement changes do not make an existing component-pair violation new.
    A newly failing rule/pair is still introduced even when another rule already
    failed for that pair. Counts describe findings, not numbers of pipe solids.
    """
    identity_fields=("id","span_id","other_id","obstacle_id","fitting_id","node_id","component_id",
                     "paired_id","piece_id","segment_index","segments","vertex_index","code","reason","field","missing")
    def findings(report,status):
        result={}
        for check in report.get("checks",[]):
            if check.get("status")!=status:continue
            rows=check.get("evidence",{}).get("violations" if status=="FAIL" else "unverified") or [{}]
            for row in rows:
                row=row if isinstance(row,dict) else {"reason":str(row)}
                identity={key:row[key] for key in identity_fields if key in row}
                key=json.dumps([check["id"],check.get("component_ids",[]) if not identity else [],identity],sort_keys=True,separators=(",",":"))
                result[key]={"check_id":check["id"],"severity":check.get("severity","hard"),"component_ids":check.get("component_ids",[]),"evidence":deepcopy(row)}
        return result
    old,new=findings(before,"FAIL"),findings(after,"FAIL")
    old_unknown,new_unknown=findings(before,"UNVERIFIED"),findings(after,"UNVERIFIED")
    introduced=[new[key] for key in sorted(new.keys()-old.keys())]
    return {"introduced":introduced,"resolved":[old[key] for key in sorted(old.keys()-new.keys())],
            "persistent":[new[key] for key in sorted(old.keys()&new.keys())],
            "introduced_unverified":[new_unknown[key] for key in sorted(new_unknown.keys()-old_unknown.keys())],
            "resolved_unverified":[old_unknown[key] for key in sorted(old_unknown.keys()-new_unknown.keys())],
            "new_hard_failures":sum(row["severity"]=="hard" for row in introduced),
            "scope":"Stable rule/component/obstacle/segment findings; measured values retained from each independent report"}
