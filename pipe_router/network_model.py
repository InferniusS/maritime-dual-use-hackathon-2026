"""Confirmed engineering topology layered on the existing CAD inspection.

Coordinates are global WCS. Proximity produces review questions, never edges.
Every public function copies its input; original CAD snapshots are immutable.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import hashlib
import itertools
import json
import math

import networkx as nx

from . import core

SCHEMA = "pipeforge.network/4.0"
MAX_NODES = 256
MAX_SPANS = 128
MAX_POINTS = 4096
MAX_OBSTACLES = 128
MAX_CAPSULES = 4096
NODE_TYPES = {"equipment_port", "valve", "tee", "junction", "fitting", "endpoint", "system_boundary"}


def _identifier(value, label):
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError(f"{label} needs a nonempty identifier of at most 256 characters")
    return value


def _records(value, label, maximum):
    if not isinstance(value, list) or len(value) > maximum or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"{label} must be an object list with at most {maximum} records")
    return value


def normalize_network(raw):
    """Validate resource bounds and normalize declared coordinate units to mm.

Fields explicitly suffixed ``_mm`` always mean mm. Coordinate fields and box
envelopes use ``units``. Missing engineering metadata remains missing.
"""
    core._mapping(raw, "network")
    # Reject NaN in metadata too, and bound a direct Python caller like HTTP.
    try:
        encoded = json.dumps(raw, allow_nan=False)
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise ValueError("Network must contain finite JSON values") from None
    if len(encoded) > 8_000_000:
        raise ValueError("Network exceeds the 8 MB analysis budget")
    net = deepcopy(raw)
    if net.get("schema_version", SCHEMA) != SCHEMA:
        raise ValueError("Unsupported network schema_version")
    net["schema_version"] = SCHEMA
    _identifier(net.get("id"), "network.id")
    units = net.get("units")
    if units not in ("mm", "m", "in"):
        raise ValueError("Network units must be explicitly mm, m, or in")
    scale = {"mm": 1., "m": 1000., "in": 25.4}[units]
    if net.get("provenance") not in ("synthetic", "cad-derived"):
        raise ValueError("Network provenance must be synthetic or cad-derived")
    coordinate_system = core._mapping(net.setdefault("coordinate_system", {}), "coordinate_system")
    for axis in ("up", "fore_aft", "athwartship"):
        if axis in coordinate_system:
            coordinate_system[axis] = list(core._point(coordinate_system[axis], axis))
    if coordinate_system.get("frame", "WCS") != "WCS":
        raise ValueError("Transform local CAD coordinates into global WCS before inspection")
    drawing = core._mapping(net.get("drawing"), "drawing")
    for key in ("id", "revision"):
        _identifier(drawing.get(key), f"drawing.{key}")

    def point(value, label):
        result = [item * scale for item in core._point(value, label)]
        if any(abs(item) > 1e9 for item in result):
            raise ValueError("Global coordinates exceed the 1e9 mm analysis limit")
        return result

    def box(record, label):
        core._mapping(record, label)
        for key in ("min", "max"):
            record[key] = point(record.get(key), f"{label}.{key}")
        if any(a >= b for a, b in zip(record["min"], record["max"])):
            raise ValueError(f"{label} needs min < max on each axis")

    def dimensions(record, label):
        core._mapping(record, label)
        for key, value in record.items():
            if key.endswith("_mm") and value is not None:
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    core._number(value, f"{label}.{key}", positive=True)
                elif not isinstance(value, (list, dict)):
                    raise ValueError(f"{label}.{key} must be a positive dimension")

    box(net.get("bounds"), "bounds")
    nodes = _records(net.get("nodes"), "nodes", MAX_NODES)
    spans = _records(net.get("spans"), "spans", MAX_SPANS)
    if not nodes or not spans:
        raise ValueError("A network needs nodes and spans")
    node_map = {}
    for node in nodes:
        identifier = _identifier(node.get("id"), "node.id")
        if identifier in node_map:
            raise ValueError("Duplicate node identifier")
        node_map[identifier] = node
        if node.get("type") not in NODE_TYPES:
            raise ValueError(f"Unsupported node type: {node.get('type')}")
        _identifier(node.get("system_id"), "node.system_id")
        node["position"] = point(node.get("position"), "node.position")
        fixed = node.setdefault("fixed", True)
        if not isinstance(fixed, bool):
            raise ValueError("node.fixed must be boolean")
        if node["type"] == "equipment_port" and not fixed:
            raise ValueError("Equipment ports must remain fixed")
        if node.get("movement_region") is not None:
            box(node["movement_region"], "movement_region")
        if not fixed and not node.get("movement_region"):
            raise ValueError("Movable nodes need an explicit permitted movement_region")
        positions = node.get("allowed_positions", [])
        if not isinstance(positions, list) or len(positions) > 16:
            raise ValueError("At most 16 permitted node positions may be searched")
        node["allowed_positions"] = [point(p, "allowed_position") for p in positions]
        if node.get("movement_region"):
            region = node["movement_region"]
            if any(not all(a <= c <= b for c, a, b in zip(p, region["min"], region["max"])) for p in node["allowed_positions"]):
                raise ValueError("Allowed positions must be inside the node movement_region")
        for key in ("nominal_diameter_mm",):
            if node.get(key) is not None:
                node[key] = core._number(node[key], key, positive=True)
        for key in ("dimensions", "constraints"):
            core._mapping(node.setdefault(key, {}), f"node.{key}")
        dimensions(node["dimensions"], "node.dimensions")
        if "body_radius_mm" in node["dimensions"]:
            core._number(node["dimensions"]["body_radius_mm"], "node body_radius_mm", positive=True)
        if "stem_height_mm" in node:
            node["stem_height_mm"] = core._number(node["stem_height_mm"], "stem_height_mm")
        if node.get("fitting_catalog") is not None:
            core._mapping(node["fitting_catalog"], "node.fitting_catalog")
        contact = node["constraints"].get("contact_radius_mm")
        if contact is not None and not 0 < core._number(contact, "contact_radius_mm") <= 1000:
            raise ValueError("Port contact radius must be positive and at most 1000 mm")
        for key in ("cad_handle",):
            if node.get(key) is not None:
                _identifier(node[key], key)
    total_points = 0
    span_ids = set()
    for span in spans:
        identifier = _identifier(span.get("id"), "span.id")
        if identifier in span_ids:
            raise ValueError("Duplicate span identifier")
        span_ids.add(identifier)
        for key in ("from_node", "to_node"):
            if span.get(key) not in node_map:
                raise ValueError(f"Span {identifier} references an unknown {key}")
        if span["from_node"] == span["to_node"]:
            raise ValueError("Closed spans need intermediate engineering nodes")
        _identifier(span.get("system_id"), "span.system_id")
        if any(node_map[span[key]]["system_id"] != span["system_id"] for key in ("from_node", "to_node")):
            raise ValueError("Span cannot silently connect different systems")
        points = span.get("points")
        if not isinstance(points, list) or not 2 <= len(points) <= MAX_POINTS:
            raise ValueError("Each span needs 2 to 4096 centerline points")
        total_points += len(points)
        span["points"] = [point(p, "span point") for p in points]
        if any(math.dist(a, b) < 1e-8 for a, b in zip(span["points"], span["points"][1:])):
            raise ValueError("Consecutive duplicate route points are not permitted")
        span["outer_diameter_mm"] = core._number(span.get("outer_diameter_mm"), "outer_diameter_mm", positive=True)
        if span.get("nominal_diameter_mm") is not None:
            span["nominal_diameter_mm"] = core._number(span["nominal_diameter_mm"], "nominal_diameter_mm", positive=True)
        if not isinstance(span.setdefault("modifiable", True), bool):
            raise ValueError("span.modifiable must be boolean")
        core._mapping(span.setdefault("constraints", {}), "span.constraints")
        required = span["constraints"].get("required_compartments", [])
        if not isinstance(required,list) or len(required)>128 or any(not isinstance(i,str) or not i for i in required):
            raise ValueError("required_compartments must be a bounded list of compartment identifiers")
        if span.get("system_type") is not None and span["system_type"] not in ("pressurized", "drainage", "vent", "gravity"):
            raise ValueError("Unknown system_type must be omitted and reviewed, not guessed")
        if span.get("fitting_catalog") is not None:
            catalog = core._mapping(span["fitting_catalog"], "fitting_catalog")
            if catalog.get("source") is not None and (not isinstance(catalog["source"],str) or not catalog["source"].strip() or len(catalog["source"])>2048):
                raise ValueError("Catalog source must be a nonempty reference string")
            for key in ("radius_mm", "body_radius_mm"):
                if catalog.get(key) is not None:
                    catalog[key] = core._number(catalog[key], key, positive=True)
            angles = catalog.get("angles_deg", [])
            if not isinstance(angles, list) or len(angles) > 72 or any(not 0 < core._number(a,"catalog angle") < 180 for a in angles):
                raise ValueError("Catalog angles must be positive turns below 180 degrees")
            if catalog.get("status") not in (None,"verified","provisional","missing","unverified"):
                raise ValueError("Unknown catalog verification status")
        if span.get("manufacturing") is not None:
            manufacturing = core._mapping(span["manufacturing"], "manufacturing")
            for piece in _records(manufacturing.get("pieces",[]), "manufactured pieces", 256):
                if "points" in piece:
                    piece["points"] = [point(p,"manufactured piece point") for p in piece["points"]]
    if total_points > MAX_POINTS:
        raise ValueError("Network exceeds the total 4096 point validation budget")
    total_capsules = 0
    for obstacle in _records(net.setdefault("obstacles", []), "obstacles", MAX_OBSTACLES):
        _identifier(obstacle.get("id"), "obstacle.id")
        box(obstacle, "obstacle")
        if "floor_point" in obstacle:
            obstacle["floor_point"] = point(obstacle["floor_point"], "walkway.floor_point")
        if "floor_elevation_mm" in obstacle:
            obstacle["floor_elevation_mm"] = core._number(obstacle["floor_elevation_mm"], "floor_elevation_mm")
        if "capsules" in obstacle:
            capsules = _records(obstacle["capsules"], "capsules", 512)
            if not capsules:
                raise ValueError("Capsule representations may not be empty")
            total_capsules += len(capsules)
            for cap in capsules:
                for key in ("start", "end", "clip_min", "clip_max"):
                    if key in cap:
                        cap[key] = point(cap[key], f"capsule.{key}")
                cap["radius"] = core._number(cap.get("radius"), "capsule.radius", positive=True) * scale
        # Reuse the team's exact capsule validation, including broad-box bounds.
        core._prepare_obstacle(obstacle, "obstacle", 0.)
    if len({o["id"] for o in net["obstacles"]}) != len(net["obstacles"]):
        raise ValueError("Duplicate obstacle identifier")
    if total_capsules > MAX_CAPSULES:
        raise ValueError("Network exceeds the total capsule budget")
    for room in _records(net.setdefault("compartments", []), "compartments", 128):
        _identifier(room.get("id"), "compartment.id")
        box(room, "compartment")
    for key in ("supports", "penetrations"):
        for record in _records(net.setdefault(key, []), key, 512):
            for field in ("position", "point", "center", "structure_point", "pipe_center", "floor_point"):
                if field in record:
                    record[field] = point(record[field], f"{key}.{field}")
            for field in ("radius_mm", "pipe_outer_diameter_mm"):
                if record.get(field) is not None:
                    record[field] = core._number(record[field], field, positive=True)
            for field in ("center_to_structure_mm", "edge_to_structure_mm"):
                if record.get(field) is not None and core._number(record[field], field) < 0:
                    raise ValueError("Support distances cannot be negative")
            if record.get("surface_normal") is not None:
                record["surface_normal"] = list(core._point(record["surface_normal"], "surface_normal"))
            if "offset_required" in record and not isinstance(record["offset_required"], bool):
                raise ValueError("offset_required must be boolean")
    for record in _records(net.setdefault("unresolved_connections", []), "unresolved_connections", 512):
        _identifier(record.get("id"), "connection.id")
        ids = record.get("endpoint_ids")
        if not isinstance(ids, list) or len(ids) < 2 or any(i not in node_map for i in ids):
            raise ValueError("Unresolved connections must name at least two existing endpoints")
    coverage = core._mapping(net.setdefault("coverage", {"complete": False, "notes": ["Extraction coverage was not declared"]}), "coverage")
    if "complete" in coverage and not isinstance(coverage["complete"], bool):
        raise ValueError("coverage.complete must be boolean")
    for field in ("omitted_count", "unknown_count"):
        if field in coverage and (isinstance(coverage[field],bool) or not isinstance(coverage[field],int) or coverage[field] < 0):
            raise ValueError("Coverage counts must be nonnegative integers")
    access = core._mapping(net.setdefault("accessibility_coverage", {}), "accessibility_coverage")
    if "complete" in access and not isinstance(access["complete"], bool):
        raise ValueError("accessibility_coverage.complete must be boolean")
    net["tolerance_mm"] = core._number(net.get("tolerance_mm", 1.), "tolerance_mm", positive=True)
    if net["tolerance_mm"] > 25:
        raise ValueError("Connectivity tolerance may not exceed 25 mm")
    net.setdefault("standards_profile", "fincantieri-track2-user-transcription-v1")
    if net["standards_profile"] != "fincantieri-track2-user-transcription-v1":
        raise ValueError("Unsupported standards profile; no requirement set may be silently substituted")
    net.setdefault("coordinate_system", {})
    systems = net.get("systems", [])
    if isinstance(systems, dict):
        systems = [{"id": k, **({"type":v} if isinstance(v,str) else v)} for k,v in systems.items()]
        net["systems"] = systems
    for record in _records(systems, "systems", 128):
        _identifier(record.get("id"), "system.id")
    search = core._mapping(net.setdefault("search", {}), "search")
    if "grid_step_mm" in search:
        search["grid_step_mm"] = core._number(search["grid_step_mm"], "grid_step_mm", positive=True)
    net["input_units"] = net.get("input_units", units)
    net["units"] = "mm"
    return net


def network_digest(network):
    """Bind review/export to every supplied constraint and source revision."""
    net = normalize_network(network)
    return hashlib.sha256(json.dumps(net, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def engineering_graph(network):
    graph = nx.MultiGraph()
    for node in network["nodes"]:
        graph.add_node(node["id"], **deepcopy(node))
    for span in network["spans"]:
        graph.add_edge(span["from_node"], span["to_node"], key=span["id"], **deepcopy(span))
    return graph


def graph_summary(network):
    graph = engineering_graph(network)
    return {"nodes": graph.number_of_nodes(), "spans": graph.number_of_edges(),
            "components": [sorted(c) for c in nx.connected_components(graph)],
            "branches": sorted(n for n, d in graph.degree if d >= 3),
            "unresolved_connections": sum(c.get("status", "unresolved") == "unresolved" for c in network.get("unresolved_connections", []))}


def reconstruct_network(cad, tolerance_mm=1.):
    """Translate CAD route candidates, preserving each instance and ambiguity.

The existing geometric inspector supplies candidate junctions. Its geometric
chain acceptance is deliberately not treated as semantic confirmation here.
Explicit confirmed port IDs may be supplied by a designer in candidate data.
"""
    from .cad_network import inspect_network
    inspected = inspect_network(cad, tolerance_mm)
    candidates = cad.get("route_candidates", [])
    if len(candidates) > MAX_SPANS:
        raise ValueError("Select at most 128 CAD spans for connected inspection")
    nodes, spans, source_ids = {}, [], set()
    invalid_ids = {x.get("candidate_id") for x in inspected.get("invalid_candidates", [])}
    duplicate_ids = {x.get("candidate_id") for x in inspected.get("duplicate_candidates", []) if x.get("candidate_id") != x.get("retained_id")}
    for candidate in candidates:
        cid = candidate.get("id")
        if cid in source_ids or cid in invalid_ids or cid in duplicate_ids:
            continue
        source_ids.add(cid)
        points = candidate.get("points", [])
        sources = candidate.get("source_instances") or [{"source_handle": h, "instance_path": candidate.get("instance_path", [])} for h in candidate.get("source_handles", [])]
        if not sources or len(points) < 2:
            raise ValueError("CAD spans require traceable source instances and geometry")
        system = candidate.get("system_id") or "UNVERIFIED-SYSTEM"
        endpoint_ids = []
        for side, p in (("start", points[0]), ("end", points[-1])):
            confirmed = candidate.get("confirmed_ports", {}).get(side)
            nid = confirmed["id"] if isinstance(confirmed, dict) else f"{cid}:{side}"
            node = {"id": nid, "type": "endpoint", "system_id": system, "position": deepcopy(p), "fixed": True,
                    "cad_handle": sources[0]["source_handle"], "instance_path": sources[0].get("instance_path", []),
                    "constraints": {"source_candidate": cid}}
            if isinstance(confirmed, dict):
                node.update(deepcopy(confirmed))
                node.setdefault("constraints", {})["connection_evidence"] = "designer-confirmed port metadata"
            if nid in nodes and (nodes[nid]["system_id"] != node["system_id"] or math.dist(nodes[nid]["position"], node["position"]) > tolerance_mm):
                raise ValueError("Confirmed port identity has inconsistent geometry or system")
            nodes.setdefault(nid, node)
            endpoint_ids.append(nid)
        spans.append({"id": cid, "from_node": endpoint_ids[0], "to_node": endpoint_ids[1],
                      "system_id": system, "points": deepcopy(points), "outer_diameter_mm": candidate.get("geometric_diameter"),
                      "nominal_diameter_mm": candidate.get("nominal_diameter_mm"), "cad_handle": sources[0]["source_handle"],
                      "instance_path": sources[0].get("instance_path", []), "source_instances": deepcopy(sources),
                      "modifiable": True, "constraints": {"metadata_unverified": True}})
    unresolved = []
    for junction in inspected.get("junctions", []):
        endpoint_ids = []
        for endpoint in junction["endpoints"]:
            cid, side = endpoint["candidate_id"], endpoint["side"]
            span = next((s for s in spans if s["id"] == cid), None)
            if span:
                endpoint_ids.append(span["from_node" if side == "start" else "to_node"])
        endpoint_ids = list(dict.fromkeys(endpoint_ids))
        if len(endpoint_ids) >= 2:
            unresolved.append({"id": junction["id"], "endpoint_ids": endpoint_ids, "status": "unresolved",
                               "reason": "Geometric proximity requires designer confirmation of system, ports and fitting", "geometric_hint": deepcopy(junction)})
    drawing = deepcopy(cad.get("drawing") or {"id": cad.get("document", "UNVERIFIED-DRAWING"), "revision": cad.get("source_revision", "UNVERIFIED-REVISION")})
    if not isinstance(drawing.get("id"), str):
        drawing["id"] = "UNVERIFIED-DRAWING"
    bounds = deepcopy(cad.get("bounds"))
    if not bounds:
        raise ValueError("CAD inspection needs explicit permitted ship bounds; model extents are not permission")
    obstacles = deepcopy(cad.get("obstacles", []))
    coverage = deepcopy(cad.get("coverage", {"complete": False, "notes": ["CAD obstacle and semantic coverage requires designer review"]}))
    if "objects" in cad:
        from .cad_layout import _solid_boxes, SOLID_TYPES
        boxes, counts = _solid_boxes(cad)
        selected = {(s["source_handle"], tuple(s.get("instance_path",[]))) for span in spans for s in span["source_instances"]}
        known_keys = set()
        fallback_count = 0
        for obstacle in boxes:
            key = tuple(obstacle.pop("key"))
            key = (key[0], tuple(key[1]))
            known_keys.add(key)
            if key in selected:
                continue
            obstacle["kind"] = "unknown"
            obstacle["id"] = "environment:"+obstacle["id"]
            if obstacle.get("obstacle_method") != "supported_surface_capsules":
                fallback_count += 1
            obstacles.append(obstacle)
        omitted = counts.get("solid_geometry_objects_unread",0)
        for index,obj in enumerate(cad.get("objects",[])):
            key = (obj.get("source_handle"),tuple(obj.get("instance_path",[])))
            if key in selected or key in known_keys or obj.get("type") in SOLID_TYPES:
                continue
            geometry = obj.get("geometry",{})
            pts = geometry.get("vertices", geometry.get("points"))
            if not isinstance(pts,list) or not pts:
                omitted += 1
                continue
            pts = [core._point(p,"unknown CAD geometry") for p in pts]
            low = [min(p[i] for p in pts) for i in range(3)]
            high = [max(p[i] for p in pts) for i in range(3)]
            for i in range(3):
                if high[i]-low[i]<.001: low[i]-=.0005; high[i]+=.0005
            obstacles.append({"id":f"environment:unknown:{index}","kind":"unknown","source_handle":obj.get("source_handle"),"instance_path":list(key[1]),"min":low,"max":high,"geometry_note":"Retained extracted unknown geometry; enclosure fidelity requires source review"})
            fallback_count += 1
        coverage["omitted_count"] = coverage.get("omitted_count",0)+omitted
        coverage["unknown_count"] = coverage.get("unknown_count",0)+fallback_count
        if omitted or fallback_count:
            coverage["complete"] = False
            coverage.setdefault("notes",[]).append("Unsupported and sample-only CAD geometry prevents complete clearance verification; reliable extracted bounds remain active obstacles")
    return normalize_network({"schema_version": SCHEMA, "id": cad.get("id", "cad-network"), "units": "mm", "provenance": "cad-derived",
        "drawing": drawing, "coordinate_system": deepcopy(cad.get("coordinate_system", {})), "bounds": bounds,
        "nodes": list(nodes.values()), "spans": spans, "obstacles": obstacles,
        "coverage": coverage,
        "unresolved_connections": unresolved, "tolerance_mm": tolerance_mm})


def confirm_connections(raw, decisions):
    """Apply explicit, auditable designer connections or separate nearby ends."""
    net = normalize_network(raw)
    _records(decisions, "decisions", 512)
    for decision in decisions:
        connection_id = decision.get("connection_id", decision.get("id"))
        record = next((c for c in net["unresolved_connections"] if c["id"] == connection_id and c.get("status", "unresolved") == "unresolved"), None)
        if record is None:
            raise ValueError("Unknown or already resolved connection")
        action = decision.get("action")
        if action == "separate":
            record.update({"status": "separate", "decision": deepcopy(decision)})
            continue
        if action not in ("connect", "confirm"):
            raise ValueError("Connection decision must be connect or separate")
        ids = decision.get("node_ids", decision.get("endpoint_ids", record["endpoint_ids"]))
        if not isinstance(ids, list) or len(set(ids)) < 2 or not set(ids) <= set(record["endpoint_ids"]):
            raise ValueError("Choose at least two distinct endpoints from the unresolved connection")
        members = [next(n for n in net["nodes"] if n["id"] == i) for i in ids]
        if len({n["system_id"] for n in members}) != 1:
            raise ValueError("Different systems cannot be joined without an explicit reviewed system model")
        anchor = list(core._point(decision.get("position", members[0]["position"]), "confirmed global position"))
        if any(math.dist(n["position"], anchor) > net["tolerance_mm"] for n in members):
            raise ValueError("Confirmed connection exceeds the declared numerical tolerance")
        if any(n["type"] == "equipment_port" and math.dist(n["position"], anchor) > 1e-7 for n in members):
            raise ValueError("Confirmation cannot move a fixed equipment port")
        nid = _identifier(decision.get("node_id", f"confirmed:{connection_id}"), "confirmed node id")
        if any(n["id"] == nid and n["id"] not in ids for n in net["nodes"]):
            raise ValueError("Confirmed node ID is already in use")
        merged = deepcopy(members[0])
        merged.update({"id": nid, "position": anchor, "type": decision.get("type", "tee" if len(ids) > 2 else "junction"),
                       "source_endpoints": deepcopy(members), "fixed": True,
                       "constraints": {"connection_evidence": "explicit designer confirmation", "fitting_compatibility": "UNVERIFIED"}})
        net["nodes"] = [n for n in net["nodes"] if n["id"] not in ids] + [merged]
        for span in net["spans"]:
            for key, index in (("from_node", 0), ("to_node", -1)):
                if span[key] in ids:
                    span[key] = nid
                    span["points"][index] = list(anchor)
        # Other unresolved clusters follow merged identity rather than losing it.
        for cluster in net["unresolved_connections"]:
            cluster["endpoint_ids"] = list(dict.fromkeys(nid if i in ids else i for i in cluster["endpoint_ids"]))
            if len(cluster["endpoint_ids"]) < 2:
                cluster["status"] = "confirmed"
        record["decision"] = deepcopy(decision)
        record["status"] = "confirmed" if len(record["endpoint_ids"]) < 2 else "unresolved"
        net.setdefault("connection_decisions", []).append({"connection_id": connection_id, "node_id": nid, "members": ids, "position": anchor})
    # Resolved records retain their audit history without being re-validated as
    # unresolved multi-endpoint groups.
    completed = [c for c in net["unresolved_connections"] if c.get("status") != "unresolved"]
    net["resolved_connections"] = net.get("resolved_connections", []) + completed
    net["unresolved_connections"] = [c for c in net["unresolved_connections"] if c.get("status") == "unresolved"]
    return normalize_network(net)
