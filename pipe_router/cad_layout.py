"""Prepare an explicitly provisional routing test from a real CAD candidate.

The local search boundary is assumed. Obstacles enclose the full extracted
samples for each source solid instance. They do not establish complete CAD
coverage, a ship boundary or verified pipe connections.
"""
from __future__ import annotations

from copy import deepcopy
import math

from . import core

SOLID_TYPES = {"3DSOLID", "BODY", "REGION"}
THIN_BOX_PADDING_MM = 0.001


def _instance_key(handle, instance_path, label):
    if not isinstance(handle, str) or not handle.strip():
        raise ValueError(f"{label} needs a source handle. Reimport the full DXF")
    if not isinstance(instance_path, (list, tuple)) or not all(isinstance(item, str) for item in instance_path):
        raise ValueError(f"{label} has an invalid source instance path. Reimport the full DXF")
    return handle, tuple(instance_path)


def _instance_record(key):
    return {"source_handle": key[0], "instance_path": list(key[1])}


def _solid_boxes(cad):
    """Group complete extracted samples by source solid placement, not handle alone."""
    objects = cad.get("objects")
    if not isinstance(objects, list):
        raise ValueError("CAD objects must contain the full imported geometry")
    groups = {}
    explicit_curves = 0
    unread_geometry_objects = 0
    for index, obj in enumerate(objects):
        if not isinstance(obj, dict):
            raise ValueError("CAD objects must be dictionaries from the full importer")
        geometry = obj.get("geometry", {})
        if not isinstance(geometry, dict):
            raise ValueError(f"CAD object {index} has invalid geometry. Reimport the full DXF")
        if obj.get("type") not in SOLID_TYPES:
            if geometry.get("kind") == "polyline":
                explicit_curves += 1
            continue
        key = _instance_key(obj.get("source_handle"), obj.get("instance_path", []), f"CAD object {index}")
        points = (geometry.get("vertices") if geometry.get("kind") == "mesh"
                  else geometry.get("points") if geometry.get("kind") == "polyline" else None)
        if not isinstance(points, (list, tuple)) or not points:
            unread_geometry_objects += 1
            continue
        record = groups.setdefault(key, {
            "min": [math.inf] * 3, "max": [-math.inf] * 3,
            "source_object_count": 0, "source_point_count": 0,
        })
        for point_index, raw in enumerate(points):
            point = core._point(raw, f"CAD object {index} point {point_index}")
            for axis in range(3):
                record["min"][axis] = min(record["min"][axis], point[axis])
                record["max"][axis] = max(record["max"][axis], point[axis])
        record["source_object_count"] += 1
        record["source_point_count"] += len(points)
    envelopes = {}
    for record in cad.get("solid_envelopes", []):
        key = _instance_key(record.get("source_handle"), record.get("instance_path", []), "Solid envelope")
        if key in envelopes:
            raise ValueError("Duplicate solid envelope identity. Reimport the drawing")
        envelopes[key] = record
        # A complete analytic envelope can exist without any display mesh.
        # Keep that component in the collision input rather than losing it.
        if key not in groups and record.get("eligible") is True:
            capsules = record.get("capsules")
            if not isinstance(capsules, list) or not 1 <= len(capsules) <= 256:
                raise ValueError("Invalid supported solid envelope. Reimport the drawing")
            lows, highs = [], []
            for capsule in capsules:
                radius = core._number(capsule.get("radius"), "capsule radius", positive=True)
                for name in ("start", "end"):
                    point = core._point(capsule.get(name), "capsule endpoint")
                    lows.append([v - radius for v in point])
                    highs.append([v + radius for v in point])
            groups[key] = {"min": [min(p[i] for p in lows) for i in range(3)],
                           "max": [max(p[i] for p in highs) for i in range(3)],
                           "source_object_count": 0, "source_point_count": 0}
    boxes = []
    for index, (key, record) in enumerate(groups.items()):
        low, high = list(record["min"]), list(record["max"])
        padded_axes = []
        for axis in range(3):
            if high[axis] - low[axis] < THIN_BOX_PADDING_MM:
                midpoint = (low[axis] + high[axis]) / 2
                low[axis] = midpoint - THIN_BOX_PADDING_MM / 2
                high[axis] = midpoint + THIN_BOX_PADDING_MM / 2
                padded_axes.append(axis)
        box = {
            "id": f"cad-solid-{index + 1}", "key": key, "min": low, "max": high,
            **_instance_record(key), "source_object_count": record["source_object_count"],
            "source_point_count": record["source_point_count"], "thin_axes_padded": padded_axes,
            "obstacle_method": "sample_box",
            "geometry_note": "No complete supported surface envelope. Using the extracted sample box",
        }
        envelope = envelopes.get(key, {})
        if envelope.get("eligible") is True:
            capsules = deepcopy(envelope.get("capsules"))
            if not isinstance(capsules, list) or not 1 <= len(capsules) <= 256:
                raise ValueError("Invalid supported solid envelope. Reimport the drawing")
            for capsule in capsules:
                radius = core._number(capsule.get("radius"), "capsule radius", positive=True)
                a = core._point(capsule.get("start"), "capsule start")
                b = core._point(capsule.get("end"), "capsule end")
                cap_low = [min(a[i], b[i]) - radius for i in range(3)]
                cap_high = [max(a[i], b[i]) + radius for i in range(3)]
                if "clip_min" in capsule and "clip_max" in capsule:
                    clip_low = core._point(capsule["clip_min"], "capsule clip_min")
                    clip_high = core._point(capsule["clip_max"], "capsule clip_max")
                    cap_low = [max(a, b) for a, b in zip(cap_low, clip_low)]
                    cap_high = [min(a, b) for a, b in zip(cap_high, clip_high)]
                for axis in range(3):
                    low[axis] = min(low[axis], cap_low[axis])
                    high[axis] = max(high[axis], cap_high[axis])
            box.update(capsules=capsules, obstacle_method="supported_surface_capsules",
                       geometry_note=envelope.get("reason", "Complete supported surface envelopes"))
        elif envelope.get("reason"):
            box["geometry_note"] = envelope["reason"]
        boxes.append(box)
    return boxes, {"explicit_curve_objects_unchecked": explicit_curves,
                   "solid_geometry_objects_unread": unread_geometry_objects}


def _overlaps(box, bounds):
    return all(box["min"][axis] <= bounds["max"][axis]
               and box["max"][axis] >= bounds["min"][axis] for axis in range(3))


def _endpoint_contacts(points, boxes, envelope):
    contacts = []
    for label, point in (("start", points[0]), ("end", points[-1])):
        for box in boxes:
            shape = core._prepare_obstacle(box, "CAD obstacle", envelope)
            if core._segment_intersects_box(tuple(point), tuple(point), shape):
                contacts.append({
                    "endpoint": label, "obstacle_id": box["id"],
                    "source_handle": box["source_handle"], "instance_path": list(box["instance_path"]),
                    "scope": "Inside the component envelope expanded by pipe radius and clearance",
                    "obstacle_method": box["obstacle_method"],
                })
    return contacts


def prepare_cad_layout(cad, candidate_id, system_type="pressurized", clearance=50.0,
                       padding=500.0, reverse_flow=False, endpoint_connections=None,
                       max_stub_length=250.0, rule_profile="prototype-unverified",
                       model_context=None, engineering=None, _prepared_geometry=None):
    """Create a single-run test without editing the imported CAD result.

    Inputs must be the full importer output already in millimetres. Source
    units are not converted again. Only the selected solid placements are
    removed from the obstacle set. Neighboring connectors are never discarded.
    """
    core._mapping(cad, "cad")
    if cad.get("units") != "mm":
        raise ValueError("CAD preparation requires importer output already converted to mm. Reimport with the intended source units")
    if cad.get("provenance") != "cad":
        raise ValueError("CAD preparation requires a real CAD importer result")
    if cad.get("preview"):
        raise ValueError("A reduced display preview cannot supply obstacle geometry. Use the full cached CAD import")
    if not isinstance(system_type, str) or system_type not in {"pressurized", "gravity"}:
        raise ValueError("system_type must be pressurized or gravity")
    if not isinstance(reverse_flow, bool):
        raise ValueError("reverse_flow must be true or false")
    if endpoint_connections is not None:
        if not isinstance(endpoint_connections, dict) or any(key not in ("start", "end") for key in endpoint_connections):
            raise ValueError("endpoint_connections must map start or end to obstacle ID lists")
        if any(not isinstance(value, list) or not all(isinstance(item, str) for item in value) for value in endpoint_connections.values()):
            raise ValueError("endpoint_connections values must be lists of obstacle IDs")
    max_stub_length = core._number(max_stub_length, "max_stub_length", positive=True)
    if max_stub_length > 1000:
        raise ValueError("max_stub_length must not exceed 1000 mm")
    clearance = core._number(clearance, "clearance")
    padding = core._number(padding, "padding")
    if clearance < 0 or padding < 0:
        raise ValueError("clearance and padding must be nonnegative millimetre values")
    if not isinstance(candidate_id, str) or not candidate_id:
        raise ValueError("Choose a candidate run from the imported drawing")
    candidates = cad.get("route_candidates")
    if not isinstance(candidates, list):
        raise ValueError("The CAD import has no candidate runs. Reimport a supported DXF")
    matching = [item for item in candidates if isinstance(item, dict) and item.get("id") == candidate_id]
    if not matching and candidate_id.startswith("network:"):
        from .cad_network import inspect_network
        network = (_prepared_geometry["network"] if _prepared_geometry is not None else inspect_network(cad))
        matching = [item for item in network["chains"] if item.get("id") == candidate_id]
    if len(matching) != 1:
        raise ValueError(f"Candidate {candidate_id!r} is missing or ambiguous. Choose one of this drawing's candidate runs")
    candidate = matching[0]
    raw_points = candidate.get("points")
    if not isinstance(raw_points, (list, tuple)) or not 2 <= len(raw_points) <= core.MAX_ROUTE_POINTS:
        raise ValueError("Selected candidate needs at least two valid points within the route size limit")
    points = [list(core._point(point, f"candidate point {i}")) for i, point in enumerate(raw_points)]
    if reverse_flow:
        points.reverse()
    if points[0] == points[-1]:
        raise ValueError("The selected candidate is closed. Choose an open run with different endpoints")
    diameter = core._number(candidate.get("geometric_diameter"), "candidate geometric diameter", positive=True)
    instances = candidate.get("source_instances")
    if instances is not None:
        if not isinstance(instances, list) or not instances or not all(isinstance(item, dict) for item in instances):
            raise ValueError("Selected candidate has invalid source_instances")
        selected = {_instance_key(item.get("source_handle"), item.get("instance_path", []), "Selected candidate") for item in instances}
        handles = sorted({key[0] for key in selected})
    else:
        handles = candidate.get("source_handles")
        if not isinstance(handles, list) or not handles:
            raise ValueError("Selected candidate has no traceable source handles. Reimport the full DXF")
        selected = {_instance_key(handle, candidate.get("instance_path", []), "Selected candidate") for handle in handles}
    envelope = diameter / 2 + clearance
    expansion = padding + envelope
    bounds = {
        "min": [min(point[axis] for point in points) - expansion for axis in range(3)],
        "max": [max(point[axis] for point in points) + expansion for axis in range(3)],
    }
    # An in-process drawing review builds the same immutable geometry once.
    # This cache is never accepted from an HTTP request or persisted across drawings.
    all_boxes, extra_counts = (_prepared_geometry["solids"] if _prepared_geometry is not None else _solid_boxes(cad))
    solid_index = _prepared_geometry.get("solid_index") if _prepared_geometry is not None else None
    known_keys = solid_index.by_key.keys() if solid_index is not None else {box["key"] for box in all_boxes}
    if not selected.issubset(known_keys):
        raise ValueError("Selected candidate source geometry is missing from this import. Use the full CAD result before testing")
    if solid_index is not None:
        # This index belongs to the current in-process immutable CAD snapshot;
        # neither it nor prepared geometry is accepted from an HTTP request.
        if solid_index.boxes is not all_boxes:
            raise ValueError("The prepared geometry index does not match this snapshot")
        excluded = [solid_index.by_key[key] for key in sorted(selected)]
        nearby = [box for box in solid_index.query(bounds) if box["key"] not in selected]
    else:
        excluded = [box for box in all_boxes if box["key"] in selected]
        nearby = [box for box in all_boxes if box["key"] not in selected and _overlaps(box, bounds)]
    allow_dense_screening = _prepared_geometry is not None and _prepared_geometry.get("screening_batches") is True
    if len(nearby) > core.MAX_OBSTACLES and not allow_dense_screening:
        raise ValueError(
            f"This local region contains {len(nearby)} obstacle solids. The tester supports at most "
            f"{core.MAX_OBSTACLES}. Reduce search padding or choose a shorter candidate run. No obstacles were dropped."
        )
    obstacles = [{"id": box["id"], "min": list(box["min"]), "max": list(box["max"]),
                  **({"capsules": deepcopy(box["capsules"])} if "capsules" in box else {})} for box in nearby]
    contacts = _endpoint_contacts(points, nearby, envelope)
    declared_solids = cad.get("counts", {}).get("solid_instances")
    missing = max(0, declared_solids - len(all_boxes)) if isinstance(declared_solids, int) else None
    warnings = [
        "Selected candidate identity, nominal size and equipment ports remain unverified. A candidate may be a fitting or fastener.",
        "The rectangular search region is an assumed local test box. It is not the ship boundary or an approved routing space.",
        "Obstacle boxes conservatively enclose extracted samples and mesh triangles. They can block empty space inside curved components.",
        "Fully supported surfaces use conservative pipe-shaped envelopes. Ambiguous torus bends use the whole ring. Unknown or partial solids keep their sample boxes.",
        "Unsupported and missing source geometry remains unchecked. These boxes do not establish complete solid geometry or ship clearance.",
        "Neighboring components and connectors remain obstacles. Endpoint contacts need designer review rather than automatic removal.",
        "The inferred diameter and selected rule values are provisional. No fitting catalog or engineering standard is verified.",
    ]
    warnings.extend(item for item in candidate.get("warnings", []) if isinstance(item, str))
    if contacts:
        warnings.append(f"{len(contacts)} endpoint-to-obstacle contacts were found under the selected radius and clearance. A replacement route may be blocked at its fixed endpoints.")
    rules = {"clearance": clearance, "grid_step": 250.0, "bend_penalty": 300.0}
    if system_type == "gravity":
        rules["min_slope"] = 0.01
        warnings.append("Gravity direction follows the selected start and end. Z-up and the 0.01 minimum slope are prototype assumptions.")
    assumptions = [
        "Real drawing coordinates were extracted by the CAD preview importer. The candidate and surrounding geometry require source-CAD review.",
        "Imported coordinates are already in millimetres. The original source unit choice remains provisional where the importer marks an assumption.",
        f"Clearance of {clearance:g} mm and local search padding of {padding:g} mm are user-selected prototype values.",
        "Grid spacing of 250 mm and bend penalty of 300 mm are prototype search settings.",
        "The selected geometric envelope diameter is treated as pipe outer diameter for this test only.",
        *warnings,
    ]
    counts = {
        "all_source_objects": len(cad["objects"]),
        "source_declared_solid_instances": declared_solids,
        "solid_instances_with_extracted_geometry": len(all_boxes),
        "solid_instances_without_extracted_geometry": missing,
        "selected_source_instances": len(excluded),
        "excluded_selected_source_objects": sum(box["source_object_count"] for box in excluded),
        "nearby_obstacles": len(nearby), "checked_obstacles": len(obstacles),
        "pipe_shaped_obstacles": sum("capsules" in box for box in nearby),
        "fallback_box_obstacles": sum("capsules" not in box for box in nearby),
        "outside_local_bounds": len(all_boxes) - len(excluded) - len(nearby),
        "endpoint_contacts": len(contacts), **extra_counts,
    }
    context = {
        "candidate_id": candidate_id, "requires_review": True,
        "name": cad.get("name"), "source_name": cad.get("name"), "source": cad.get("source"),
        "source_handles": list(handles), "instance_path": list(candidate.get("instance_path", [])),
        "obstacle_count": len(obstacles), "source_counts": deepcopy(cad.get("counts", {})),
        "source_sha256": cad.get("source_sha256"), "source_units": cad.get("source_units"),
        "source_unit_assumption": cad.get("unit_assumption"),
        "geometry_origin": "full_extracted_geometry",
        "obstacle_method": "supported_surface_capsules_with_box_fallback" if any("capsules" in box for box in nearby) else "aabb_of_extracted_samples",
        "bounds_scope": "assumed_local_search_box", "ship_boundary_verified": False,
        "complete_ship_geometry": False, "semantic_pipe_size_verified": False,
        "connectivity_verified": False, "source_geometry_edited": False,
        "selected_source_instances": [_instance_record(key) for key in sorted(selected)],
        "source_instances": [_instance_record(key) for key in sorted(selected)],
        "network_member_ids": deepcopy(candidate.get("member_ids", [])),
        "network_join_records": deepcopy(candidate.get("join_records", [])),
        "network_confidence": candidate.get("confidence"),
        "source_records": deepcopy(candidate.get("source_records", [])),
        "candidate_contains_inferred_bend": bool(candidate.get("contains_inferred_bend", False)),
        "reverse_flow": reverse_flow, "padding_mm": padding, "pipe_envelope_mm": envelope,
        "counts": counts,
        "obstacle_sources": [{key: deepcopy(box[key]) for key in (
            "id", "source_handle", "instance_path", "source_object_count", "source_point_count", "thin_axes_padded",
            "obstacle_method", "geometry_note",
        )} for box in nearby],
        "endpoint_contacts": contacts, "unsupported_geometry": deepcopy(cad.get("unsupported_geometry", {})),
        "source_warnings": list(cad.get("warnings", [])), "warnings": warnings,
        "limitations": list(warnings),
    }
    result = {
        "name": f"{cad.get('name', 'Imported drawing')} candidate test",
        "units": "mm", "provenance": "cad-derived", "system_type": system_type,
        "bounds": bounds, "obstacles": obstacles,
        "pipe": {"start": list(points[0]), "end": list(points[-1]),
                 "diameter": diameter, "original_route": points},
        "rules": rules, "assumptions": assumptions,
        "source": {"file": cad.get("source"), "sha256": cad.get("source_sha256"),
                   "method": "Imported analytic surface axes with unverified within-solid connectivity"},
        "cad_context": context,
    }
    result["rule_profile"] = rule_profile
    if engineering is not None:
        result["engineering"] = deepcopy(engineering)
    if model_context is not None:
        from .model_context import apply_model_context
        result = apply_model_context(result, model_context, cad.get("source_sha256"), _screening_batches=allow_dense_screening)
        # Context geometry participates in contact discovery before connector
        # exemptions are offered. All other obstacles remain active.
        contacts = []
        for endpoint, point in (("start", points[0]), ("end", points[-1])):
            for obstacle in result["obstacles"]:
                shape = core._prepare_obstacle(obstacle, "context contact", envelope)
                if core._segment_intersects_box(tuple(point), tuple(point), shape):
                    source = next((r for r in result["cad_context"]["obstacle_sources"] if r["id"] == obstacle["id"]), {})
                    contacts.append({"endpoint": endpoint, "obstacle_id": obstacle["id"],
                                     "source_handle": source.get("source_handle", obstacle["id"])})
        result["cad_context"]["endpoint_contacts"] = contacts
        result["cad_context"]["counts"].update({"checked_obstacles": len(result["obstacles"]),
            "pipe_shaped_obstacles": sum("capsules" in o for o in result["obstacles"]),
            "fallback_box_obstacles": sum("capsules" not in o for o in result["obstacles"]), "endpoint_contacts": len(contacts)})
    if rule_profile == "fmg-v3" and clearance < 50.8 - core.EPSILON:
        raise ValueError("The V3 challenge profile requires at least 50.8 mm shock clearance")
    if endpoint_connections:
        from .workflow import with_endpoint_connections
        result = with_endpoint_connections(result, endpoint_connections, max_stub_length)
    return result
