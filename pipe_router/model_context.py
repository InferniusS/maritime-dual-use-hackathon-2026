"""Explicit model context for a local CAD routing check.

Context is supplied by a designer. This module neither aligns drawings nor
infers the hull from a local search box. Geometry is converted once to mm.
Allowed-space checks conservatively cover each entire centerline segment by
the union of individually inset allowed boxes. Adjacent boxes may therefore
need overlap to admit a finite-width pipe through their shared boundary.
"""
from __future__ import annotations

from copy import deepcopy
import math

from . import core
from .collision import segment_box_interval

UNIT_SCALE = {"mm": 1.0, "m": 1000.0, "in": 25.4}
COVERAGE_CATEGORIES = ("structure", "equipment", "piping", "ventilation", "wireways")
MAX_SPACES = 128
MAX_CONTEXT_OBSTACLES = 4096
MAX_ADDITIONAL_DRAWINGS = 32
AXES = {sign + axis for sign in ("+", "-") for axis in "XYZ"}


def _finding(rule_id, status, message, **details):
    return {"rule_id": rule_id, "status": status, "message": message, **details}


def _text(value, name):
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        raise ValueError(f"{name} must be a nonempty string of at most 2048 characters")
    return value


def _items(value, name, maximum):
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{name} must be a list with at most {maximum} entries. No geometry was dropped")
    return value


def _point(value, name, scale):
    return [core._number(v * scale, name) for v in core._point(value, name)]


def _box(item, name, scale):
    item = core._mapping(item, name)
    result = deepcopy(item)
    result["id"] = _text(item.get("id"), f"{name}.id")
    for key in ("min", "max"):
        result[key] = _point(item.get(key), f"{name}.{key}", scale)
    core._box(result, name, result["id"])
    return result


def _refs(value, name):
    return [_text(ref, name) for ref in _items(value, name, 128)]


def _identity(layout, drawing_identity=None):
    if drawing_identity is None:
        cad = layout.get("cad_context", {})
        source = layout.get("source", {})
        return (cad.get("source_sha256") if isinstance(cad, dict) else None) or (source.get("sha256") if isinstance(source, dict) else None)
    if isinstance(drawing_identity, dict):
        drawing_identity = drawing_identity.get("source_sha256") or drawing_identity.get("sha256")
    return _text(drawing_identity, "drawing_identity")


def _selected(layout):
    records = layout.get("cad_context", {}).get("selected_source_instances", [])
    return {(r["source_handle"], tuple(r.get("instance_path", []))) for r in records}


def _same_selected_source(record, layout, drawing_identity):
    source = record.get("source_identity", {})
    if not isinstance(source, dict):
        raise ValueError("Context obstacle source_identity must be an object")
    if source:
        for key in ("drawing_sha256", "source_handle"):
            _text(source.get(key), f"source_identity.{key}")
        path = source.get("instance_path", [])
        if not isinstance(path, list) or not all(isinstance(value, str) for value in path):
            raise ValueError("source_identity.instance_path must be a list of strings")
    return bool(drawing_identity and source.get("drawing_sha256") == drawing_identity and
                (source.get("source_handle"), tuple(source.get("instance_path", []))) in _selected(layout))


def obstacles_from_import(cad, *, drawing_id, category, coordinate_frame_id,
                          shared_coordinates_confirmed=False):
    """Produce a compact aligned-drawing record for context JSON.

    The caller must explicitly acknowledge alignment. Importer coordinates must
    already be mm. All extracted solids are retained here. Applying context
    filters only solids outside the unchanged local search bounds.
    """
    from .cad_layout import _solid_boxes

    _text(drawing_id, "drawing_id")
    _text(coordinate_frame_id, "coordinate_frame_id")
    if category not in COVERAGE_CATEGORIES:
        raise ValueError(f"category must be one of {', '.join(COVERAGE_CATEGORIES)}")
    if shared_coordinates_confirmed is not True:
        raise ValueError("Supplemental drawings require explicit shared coordinate confirmation. No alignment is inferred")
    core._mapping(cad, "imported drawing")
    if cad.get("units") != "mm" or cad.get("provenance") != "cad" or cad.get("preview"):
        raise ValueError("Supplemental drawings require full CAD importer output already in mm, not a display preview")
    sha = _text(cad.get("source_sha256"), "imported drawing source_sha256")
    boxes, extra_counts = _solid_boxes(cad)
    if len(boxes) > MAX_CONTEXT_OBSTACLES:
        raise ValueError(f"Supplemental drawing has more than {MAX_CONTEXT_OBSTACLES} solids. No geometry was dropped")
    obstacles = []
    for box in boxes:
        obstacles.append({
            "id": f"{drawing_id}:{box['id']}", "min": box["min"], "max": box["max"],
            **({"capsules": deepcopy(box["capsules"])} if "capsules" in box else {}),
            "source": cad.get("source", drawing_id), "category": category,
            "source_identity": {"drawing_sha256": sha, "source_handle": box["source_handle"],
                                "instance_path": list(box["instance_path"])},
            "obstacle_method": box["obstacle_method"],
        })
    declared = cad.get("counts", {}).get("solid_instances")
    missing = max(0, declared - len(boxes)) if isinstance(declared, int) else None
    return {"id": drawing_id, "units": "mm", "coordinate_frame_id": coordinate_frame_id,
            "shared_coordinates_confirmed": True, "category": category,
            "source": cad.get("source"), "source_sha256": sha,
            "source_units": cad.get("source_units"), "source_unit_assumption": cad.get("unit_assumption"),
            "obstacles": obstacles,
            "coverage": {"extracted_solids": len(boxes), "declared_solids": declared,
                         "unread_solids": missing, **extra_counts},
            "unsupported_geometry": deepcopy(cad.get("unsupported_geometry", {})),
            "warnings": list(cad.get("warnings", []))}


def apply_model_context(layout, context, drawing_identity=None):
    """Apply explicit surroundings and allowed routing spaces to a copied layout.

    Context keys: units, coordinate_frame, ship_axes, coverage, allowed_spaces,
    required_space_ids, boundary_clearance_mm, keep_outs, additional_drawings.
    An additional drawing is the compact output of obstacles_from_import or a
    record containing ``import`` plus its drawing ID, category and frame.
    Reapplying the returned model_context is idempotent. Existing geometry is
    accepted as already applied only when IDs, shapes and provenance match.
    """
    from .workflow import normalize_layout

    core._mapping(context, "model context")
    if context.get("schema_version", 1) != 1:
        raise ValueError("Only model context schema_version 1 is supported")
    units = context.get("units")
    if units not in UNIT_SCALE:
        raise ValueError("Model context needs explicit units: mm, m, or in")
    scale = UNIT_SCALE[units]
    frame = core._mapping(context.get("coordinate_frame"), "coordinate_frame")
    frame_id = _text(frame.get("id"), "coordinate_frame.id")
    if frame.get("shared_coordinates_confirmed") is not True:
        raise ValueError("Model context requires shared_coordinates_confirmed=true. No drawing alignment is inferred")
    result = normalize_layout(layout)
    previous = result.get("model_context", {})
    if previous and previous.get("coordinate_frame", {}).get("id") != frame_id:
        raise ValueError("Cannot replace an applied context with a different coordinate frame. Start from the original layout")
    identity = _identity(result, drawing_identity)
    findings = []
    axes = deepcopy(context.get("ship_axes"))
    if axes is not None:
        core._mapping(axes, "ship_axes")
        names = ("longitudinal", "transverse", "up")
        if "confirmed" in axes and not isinstance(axes["confirmed"], bool):
            raise ValueError("ship_axes.confirmed must be true or false")
        if all(axes.get(name) is None for name in names) and axes.get("confirmed") is not True:
            axes = None
        elif any(axes.get(name) not in AXES for name in names) or len({axes[name][-1] for name in names}) != 3:
            raise ValueError("ship_axes must name three different signed axes, for example +X, +Y, +Z")
    if not axes or axes.get("confirmed") is not True:
        findings.append(_finding("ship_axes", "needs_review", "Confirm the ship longitudinal, transverse and up directions before directional slope checks"))

    coverage = deepcopy(context.get("coverage", {}))
    core._mapping(coverage, "coverage")
    if set(coverage) - set(COVERAGE_CATEGORIES):
        raise ValueError("Unknown model coverage category")
    declared_complete = True
    for category in COVERAGE_CATEGORIES:
        record = core._mapping(coverage.get(category, {}), f"coverage.{category}")
        for key in ("included", "reviewed"):
            if key in record and not isinstance(record[key], bool):
                raise ValueError(f"coverage.{category}.{key} must be true or false")
        refs = _refs(record.get("source_refs", []), f"coverage.{category}.source_refs")
        positive = record.get("included") is True and record.get("reviewed") is True and bool(refs)
        declared_complete &= positive
        if not positive:
            findings.append(_finding(f"coverage_{category}", "needs_review",
                                     f"{category.capitalize()} coverage needs an explicit included/reviewed declaration and source references",
                                     source_refs=refs))
    spaces = [_box(item, f"allowed_spaces[{i}]", scale) for i, item in enumerate(
        _items(context.get("allowed_spaces", []), "allowed_spaces", MAX_SPACES))]
    space_ids = [space["id"] for space in spaces]
    if len(set(space_ids)) != len(space_ids):
        raise ValueError("Allowed spaces must have unique IDs")
    required = _refs(context.get("required_space_ids", []), "required_space_ids")
    if len(set(required)) != len(required) or set(required) - set(space_ids):
        raise ValueError("required_space_ids must uniquely name supplied allowed spaces")
    boundary_clearance = core._number(context.get("boundary_clearance_mm", 0), "boundary_clearance_mm")
    if boundary_clearance < 0:
        raise ValueError("boundary_clearance_mm must be nonnegative")
    # This field is deliberately mm regardless of the context coordinate unit.
    margin = result["pipe"]["diameter"] / 2 + boundary_clearance
    for space in spaces:
        if not isinstance(space.get("source"), str) or not space["source"].strip():
            findings.append(_finding("allowed_space_source", "needs_review",
                                     f"Allowed space {space['id']} needs a source reference and designer confirmation",
                                     space_id=space["id"]))
        if any(hi - lo < 2 * margin for lo, hi in zip(space["min"], space["max"])):
            findings.append(_finding("allowed_space_size", "needs_review",
                                     f"Allowed space {space['id']} cannot contain this pipe envelope on every axis",
                                     space_id=space["id"]))
    if not spaces:
        findings.append(_finding("allowed_spaces", "needs_review", "No approved routing spaces were supplied. The local search box does not establish the ship boundary"))

    keepouts = []
    for i, item in enumerate(_items(context.get("keep_outs", []), "keep_outs", MAX_CONTEXT_OBSTACLES)):
        box = _box(item, f"keep_outs[{i}]", scale)
        if box.get("kind") not in {"walkway", "duct_interior", "access", "other"}:
            raise ValueError("keep_outs.kind must be walkway, duct_interior, access, or other")
        if "capsules" in box or "source_identity" in box:
            raise ValueError("Keep-outs must be complete exclusion boxes, not individual CAD component envelopes")
        box["source"] = _text(box.get("source"), "keep_out source")
        keepouts.append(box)
    if len({item["id"] for item in keepouts}) != len(keepouts):
        raise ValueError("Keep-out IDs must be unique")

    drawings = []
    for raw in _items(context.get("additional_drawings", []), "additional_drawings", MAX_ADDITIONAL_DRAWINGS):
        raw = core._mapping(raw, "additional drawing")
        if "import" in raw:
            raw = obstacles_from_import(raw["import"], drawing_id=raw.get("id"), category=raw.get("category"),
                                        coordinate_frame_id=raw.get("coordinate_frame_id"),
                                        shared_coordinates_confirmed=raw.get("shared_coordinates_confirmed"))
        drawing = deepcopy(raw)
        _text(drawing.get("id"), "additional drawing id")
        if drawing.get("category") not in COVERAGE_CATEGORIES:
            raise ValueError("Additional drawing category must name a coverage category")
        if drawing.get("coordinate_frame_id") != frame_id or drawing.get("shared_coordinates_confirmed") is not True:
            raise ValueError("Every additional drawing must explicitly share the context coordinate frame")
        drawing_units = drawing.get("units")
        if drawing_units not in UNIT_SCALE:
            raise ValueError("Every additional drawing needs explicit units")
        drawing_scale = UNIT_SCALE[drawing_units]
        shapes = []
        for i, raw_shape in enumerate(_items(drawing.get("obstacles"), "additional drawing obstacles", MAX_CONTEXT_OBSTACLES)):
            shape = _box(raw_shape, f"additional drawing obstacle {i}", drawing_scale)
            if "capsules" in shape:
                for capsule in _items(shape["capsules"], "capsules", 256):
                    core._mapping(capsule, "capsule")
                    for key in ("start", "end", "clip_min", "clip_max"):
                        if key in capsule:
                            capsule[key] = _point(capsule[key], key, drawing_scale)
                    capsule["radius"] = core._number(capsule.get("radius"), "capsule.radius", positive=True) * drawing_scale
            core._prepare_obstacle(shape, "additional drawing obstacle", 0)
            shapes.append(shape)
        if len({shape["id"] for shape in shapes}) != len(shapes):
            raise ValueError("Additional drawing obstacle IDs must be unique")
        drawing.update(units="mm", obstacles=shapes)
        drawings.append(drawing)
    if len({drawing["id"] for drawing in drawings}) != len(drawings):
        raise ValueError("Additional drawings must have unique IDs")
    if sum(len(drawing["obstacles"]) for drawing in drawings) + len(keepouts) > MAX_CONTEXT_OBSTACLES:
        raise ValueError(f"Context exceeds {MAX_CONTEXT_OBSTACLES} obstacle solids. No geometry was dropped")

    # Store every supplied context shape for reproducible application, but only
    # insert nearby shapes into this local search. An obstacle outside its bounds
    # cannot be reached by a valid pipe envelope within the same bounds.
    bounds = result["bounds"]
    obstacles = result["obstacles"]
    lookup = {item["id"]: item for item in obstacles}
    sources = [deepcopy(source) for source in previous.get("obstacle_sources", []) if source.get("id") in lookup]
    source_lookup = {item["id"]: item for item in sources}
    excluded = []
    for namespace, raw_shapes in [("keepout", keepouts), *[(f"drawing:{d['id']}", d["obstacles"]) for d in drawings]]:
        for shape in raw_shapes:
            if namespace != "keepout" and _same_selected_source(shape, result, identity):
                excluded.append(deepcopy(shape.get("source_identity")))
                continue
            if not all(shape["min"][axis] <= bounds["max"][axis] and shape["max"][axis] >= bounds["min"][axis] for axis in range(3)):
                continue
            identifier = f"context:{namespace}:{shape['id']}"
            obstacle = {"id": identifier, "min": shape["min"], "max": shape["max"],
                        **({"capsules": shape["capsules"]} if "capsules" in shape else {})}
            source = {"id": identifier, "model_context": True, "context_id": shape["id"],
                      "source": shape.get("source"), "source_identity": deepcopy(shape.get("source_identity")),
                      "category": shape.get("category", shape.get("kind")),
                      "obstacle_method": shape.get("obstacle_method", "explicit_context_box")}
            if shape.get("source_identity"):
                source.update(source_handle=shape["source_identity"]["source_handle"],
                              instance_path=list(shape["source_identity"].get("instance_path", [])),
                              drawing_sha256=shape["source_identity"]["drawing_sha256"])
            if identifier in lookup:
                if lookup[identifier] != obstacle or source_lookup.get(identifier) != source:
                    raise ValueError("Applied model context obstacle conflicts with an existing ID or provenance. Start from the original layout")
                continue
            obstacles.append(deepcopy(obstacle))
            lookup[identifier] = obstacles[-1]
            sources.append(source)
            source_lookup[identifier] = source
    if len(obstacles) > core.MAX_OBSTACLES:
        raise ValueError(f"Local model context contains {len(obstacles)} obstacles, exceeding {core.MAX_OBSTACLES}. Reduce the local region. No geometry was dropped")
    if sum(len(item.get("capsules", [])) for item in obstacles) > core.MAX_TOTAL_CAPSULES:
        raise ValueError("Local model context exceeds the total capsule limit. No geometry was dropped")
    # Even positive declarations are user evidence, not automatic proof that
    # unknown geometry, hidden references, pipe semantics or ports were decoded.
    findings.append(_finding("context_geometry", "needs_review",
                             "Model context uses supplied boxes and extracted envelopes. Review source completeness, geometry fidelity and actual ship boundaries"))
    canonical = {
        "schema_version": 1, "units": "mm", "applied": True,
        "coordinate_frame": {"id": frame_id, "shared_coordinates_confirmed": True},
        "ship_axes": axes, "coverage": coverage, "coverage_declared_complete": bool(declared_complete),
        "allowed_spaces": spaces, "required_space_ids": required,
        "boundary_clearance_mm": boundary_clearance, "keep_outs": keepouts,
        "additional_drawings": drawings, "obstacle_sources": sources,
        "excluded_selected_source_instances": excluded,
        "findings": findings, "requires_review": True,
        "containment_method": "continuous_union_of_individually_inset_boxes",
    }
    if previous.get("applied") is True:
        for key in ("coordinate_frame", "allowed_spaces", "required_space_ids", "boundary_clearance_mm",
                    "keep_outs", "additional_drawings"):
            if previous.get(key) != canonical[key]:
                raise ValueError("Applied model context geometry changed. Start from the original layout before changing its context")
    result["model_context"] = canonical
    cad = result.get("cad_context")
    if isinstance(cad, dict):
        existing_sources = {s["id"]: s for s in cad.get("obstacle_sources", [])}
        existing_sources.update({s["id"]: deepcopy(s) for s in sources})
        cad["obstacle_sources"] = list(existing_sources.values())
        cad["obstacle_count"] = len(obstacles)
        cad.setdefault("counts", {})["checked_obstacles"] = len(obstacles)
        cad["counts"]["model_context_obstacles"] = len(sources)
    # Reuse the established geometry and protected-stub validations after merge.
    # This deliberately rejects formerly exempt endpoint contacts that changed.
    return normalize_layout(result)


def _inset_spaces(layout):
    context = layout.get("model_context", {})
    if not context:
        return []
    if context.get("units") != "mm":
        raise ValueError("Apply model context before checking its allowed spaces")
    scale = UNIT_SCALE.get(layout.get("units"))
    if scale is None:
        raise ValueError("Layout must have explicit supported units")
    margin = core._number(layout.get("pipe", {}).get("diameter"), "pipe.diameter", positive=True) * scale / 2
    clearance = core._number(context.get("boundary_clearance_mm", 0), "boundary_clearance_mm")
    if clearance < 0:
        raise ValueError("boundary_clearance_mm must be nonnegative")
    margin += clearance
    result = []
    for box in context.get("allowed_spaces", []):
        minimum = tuple(x + margin for x in core._point(box.get("min"), "allowed space min"))
        maximum = tuple(x - margin for x in core._point(box.get("max"), "allowed space max"))
        if all(a <= b for a, b in zip(minimum, maximum)):
            result.append((box["id"], minimum, maximum))
    return result


def segment_allowed(layout, start, end):
    """Search predicate, with points in layout units. Missing spaces are unknown.

    Missing space evidence does not disable exploratory routing, but
    validate_allowed_spaces always reports needs_review rather than pass.
    """
    return prepare_allowed_space_predicate(layout)(start, end)


def prepare_allowed_space_predicate(layout):
    """Precompute allowed-box insets for repeated search edges without mutation."""
    context = layout.get("model_context", {})
    if not context.get("allowed_spaces"):
        return lambda start, end: True
    scale = UNIT_SCALE.get(layout.get("units"))
    if scale is None:
        raise ValueError("Layout must have explicit supported units")
    boxes = tuple(_inset_spaces(layout))

    def check(start, end):
        a, b = tuple(_point(start, "route start", scale)), tuple(_point(end, "route end", scale))
        intervals = []
        for _, low, high in boxes:
            interval = segment_box_interval(a, b, low, high, tolerance=0)
            if interval is not None:
                intervals.append(interval)
        if not intervals:
            return False
        # Zero tolerance prevents a tiny real gap from becoming a false pass.
        covered = 0.0
        for low, high in sorted(intervals):
            if low > covered:
                return False
            covered = max(covered, high)
            if covered >= 1.0:
                return True
        return False

    return check


def validate_allowed_spaces(layout, points):
    """Return explicit pass/fail/review findings for entire segments and spaces."""
    context = layout.get("model_context", {})
    if not context.get("allowed_spaces"):
        return [_finding("allowed_spaces", "needs_review", "No approved routing spaces were supplied. Ship containment is unverified")]
    points = _items(points, "route points", core.MAX_ROUTE_POINTS)
    if len(points) < 2:
        raise ValueError("Allowed-space checking requires at least two route points")
    findings = []
    allowed = prepare_allowed_space_predicate(layout)
    for index, (a, b) in enumerate(zip(points, points[1:])):
        if not allowed(a, b):
            findings.append(_finding("allowed_spaces", "fail",
                                     f"Route segment {index + 1} is not fully contained by the supplied allowed-space envelopes",
                                     segment_index=index, method=context.get("containment_method")))
    if not findings:
        findings.append(_finding("allowed_spaces", "pass", "Every route segment fits the union of the supplied inset allowed-space boxes. This is not a verified hull-mesh check"))
    scale = UNIT_SCALE[layout["units"]]
    mm_points = [_point(point, "route point", scale) for point in points]
    lookup = {identifier: (low, high) for identifier, low, high in _inset_spaces(layout)}
    for identifier in context.get("required_space_ids", []):
        bounds = lookup.get(identifier)
        visited = False
        if bounds is not None:
            for a, b in zip(mm_points, mm_points[1:]):
                interval = segment_box_interval(a, b, *bounds, tolerance=0)
                if interval is not None and (interval[1] - interval[0]) * math.dist(a, b) > core.EPSILON:
                    visited = True
                    break
        findings.append(_finding("required_space", "pass" if visited else "fail",
                                 f"Route {'visits' if visited else 'does not visit'} required space {identifier}", space_id=identifier))
    return findings
