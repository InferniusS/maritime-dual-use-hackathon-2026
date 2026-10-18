"""Build compact, explicitly aligned model context from a local JSON specification.

Usage: python scripts/build_model_context.py --spec local/context-spec.json
       --output data/generated/model-context.json

This reads DXFs without changing them. No alignment, coverage approval or
engineering evidence is inferred. Keep the specification and output local.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipe_router import core
from pipe_router.cad_import import inspect_cad
from pipe_router.model_context import (
    AXES, COVERAGE_CATEGORIES, MAX_ADDITIONAL_DRAWINGS, MAX_CONTEXT_OBSTACLES, MAX_SPACES,
    UNIT_SCALE, _box, _items, _refs, _text, obstacles_from_import,
)


def build_context(spec_path, output_path):
    """Read local sources and create a new JSON file, never overwrite any input."""
    spec_path = Path(spec_path).resolve()
    output_path = Path(output_path).resolve()
    if output_path == spec_path:
        raise ValueError("Output must be a new JSON file, not the specification")
    if output_path.exists():
        raise ValueError("Output already exists. Choose a new output filename so no inputs or prior results are overwritten")
    raw = json.loads(spec_path.read_text(encoding="utf-8"))
    core._mapping(raw, "specification")
    context = deepcopy(raw.get("model_context", raw))
    core._mapping(context, "model_context")
    context.pop("engineering", None)
    if context.get("schema_version", 1) != 1:
        raise ValueError("Only model context schema_version 1 is supported")
    units = context.get("units")
    if units not in UNIT_SCALE:
        raise ValueError("Model context requires explicit units: mm, m, or in")
    frame = core._mapping(context.get("coordinate_frame"), "coordinate_frame")
    frame_id = _text(frame.get("id"), "coordinate_frame.id")
    if frame.get("shared_coordinates_confirmed") is not True:
        raise ValueError("Confirm the common coordinate frame explicitly before building context. This tool does not align drawings")
    axes = context.get("ship_axes")
    if axes is not None:
        core._mapping(axes, "ship_axes")
        if axes.get("confirmed") is not None and not isinstance(axes["confirmed"], bool):
            raise ValueError("ship_axes.confirmed must be true or false")
        # An all-null, unconfirmed template is allowed and remains unknown.
        names = ("longitudinal", "transverse", "up")
        if any(axes.get(name) is not None for name in names):
            if any(axes.get(name) not in AXES for name in names) or len({axes[name][-1] for name in names}) != 3:
                raise ValueError("ship_axes must use three different signed axes such as +X, +Y, +Z")
        elif axes.get("confirmed") is True:
            raise ValueError("Confirmed ship axes must provide actual directions")
        else:
            context["ship_axes"] = None
    coverage = core._mapping(context.get("coverage", {}), "coverage")
    if set(coverage) - set(COVERAGE_CATEGORIES):
        raise ValueError("Unknown model coverage category")
    for category, record in coverage.items():
        core._mapping(record, f"coverage.{category}")
        for key in ("included", "reviewed"):
            if key in record and not isinstance(record[key], bool):
                raise ValueError(f"coverage.{category}.{key} must be true or false")
        _refs(record.get("source_refs", []), "source_refs")
    factor = UNIT_SCALE[units]
    spaces = [_box(item, "allowed space", factor) for item in _items(context.get("allowed_spaces", []), "allowed_spaces", MAX_SPACES)]
    if len({s["id"] for s in spaces}) != len(spaces):
        raise ValueError("Allowed spaces must have unique IDs")
    required = _refs(context.get("required_space_ids", []), "required_space_ids")
    if len(set(required)) != len(required) or set(required) - {s["id"] for s in spaces}:
        raise ValueError("Required spaces must uniquely name supplied allowed spaces")
    keepouts = [_box(item, "keep-out", factor) for item in _items(context.get("keep_outs", []), "keep_outs", MAX_CONTEXT_OBSTACLES)]
    if len({s["id"] for s in keepouts}) != len(keepouts):
        raise ValueError("Keep-outs must have unique IDs")
    for item in keepouts:
        if item.get("kind") not in {"walkway", "duct_interior", "access", "other"}:
            raise ValueError("Keep-outs need kind walkway, duct_interior, access or other")
        _text(item.get("source"), "keep-out source")
        if "capsules" in item or "source_identity" in item:
            raise ValueError("Keep-outs must be full exclusion boxes. Use supplemental drawing obstacles for capsule geometry")
    clearance = core._number(context.get("boundary_clearance_mm", 0), "boundary_clearance_mm")
    if clearance < 0:
        raise ValueError("boundary_clearance_mm must be nonnegative")
    context.update(schema_version=1, units="mm", allowed_spaces=spaces, keep_outs=keepouts,
                   boundary_clearance_mm=clearance, required_space_ids=required)
    context.pop("applied", None)
    files = context.pop("drawing_files", raw.get("drawing_files", []))
    files = _items(files, "drawing_files", MAX_ADDITIONAL_DRAWINGS)
    if context.get("additional_drawings"):
        raise ValueError("Use drawing_files in the build specification. The builder creates additional_drawings")
    planned = []
    ids = set()
    for entry in files:
        core._mapping(entry, "drawing file")
        identifier = _text(entry.get("id"), "drawing file id")
        if identifier in ids:
            raise ValueError("Drawing IDs must be unique")
        ids.add(identifier)
        if entry.get("category") not in COVERAGE_CATEGORIES:
            raise ValueError("Each drawing file needs a valid coverage category")
        source_units = entry.get("source_units")
        if source_units not in UNIT_SCALE:
            raise ValueError("Each drawing file needs explicit source_units: mm, m, or in")
        if entry.get("shared_coordinates_confirmed") is not True:
            raise ValueError("Every drawing file needs explicit shared_coordinates_confirmed=true")
        if entry.get("coordinate_frame_id", frame_id) != frame_id:
            raise ValueError("Drawing coordinate frame differs from the context frame. No automatic alignment is available")
        path = Path(_text(entry.get("path"), "drawing file path"))
        path = (path if path.is_absolute() else spec_path.parent / path).resolve()
        if output_path == path:
            raise ValueError("Output must not overwrite an input drawing")
        if path.suffix.lower() != ".dxf":
            raise ValueError("Drawing files must be DXF. Export a DXF copy of DWG in CAD first")
        if not path.is_file():
            raise ValueError(f"Drawing file does not exist: {path}")
        planned.append((entry, path))
    drawings = []
    for entry, path in planned:
        cad = inspect_cad(path, source_units=entry["source_units"])
        drawings.append(obstacles_from_import(cad, drawing_id=entry["id"], category=entry["category"],
                                             coordinate_frame_id=frame_id, shared_coordinates_confirmed=True))
    if len(keepouts) + sum(len(item["obstacles"]) for item in drawings) > MAX_CONTEXT_OBSTACLES:
        raise ValueError("Context exceeds the supported obstacle limit. Split the reviewed area. No geometry was dropped")
    context["additional_drawings"] = drawings
    result = {"model_context": context,
              "build_info": {"specification": str(spec_path), "drawings_read": len(drawings),
                             "alignment_inferred": False, "coverage_approved": False,
                             "source_files_modified": False}}
    engineering = raw.get("engineering")
    if engineering is not None:
        core._mapping(engineering, "engineering")
        if engineering.get("units") != "mm":
            raise ValueError("Engineering evidence must explicitly use mm. It is not converted or inferred by this builder")
        result["engineering"] = deepcopy(engineering)
    encoded = json.dumps(result, indent=2, allow_nan=False)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as output:
        output.write(encoded + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True, help="Local context specification JSON")
    parser.add_argument("--output", type=Path, required=True, help="New compact context JSON file")
    args = parser.parse_args()
    try:
        result = build_context(args.spec, args.output)
    except (ValueError, OSError, TypeError, KeyError) as error:
        parser.error(str(error))
    count = sum(len(item["obstacles"]) for item in result["model_context"]["additional_drawings"])
    print(f"Wrote {args.output.resolve()} with {count} extracted obstacle envelopes from {result['build_info']['drawings_read']} drawings.")
    print("Coordinate alignment and coverage remain your declarations. Missing ship geometry and unconfirmed rules still need review.")


if __name__ == "__main__":
    main()
