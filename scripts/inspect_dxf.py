#!/usr/bin/env python3
"""Inventory a DXF without changing it or inferring a pipe network."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

import ezdxf


CANDIDATE_TYPES = ("LINE", "ARC", "POLYLINE", "LWPOLYLINE")
ACIS_TYPES = ("3DSOLID", "BODY", "REGION")


def counts(entities):
    return dict(sorted(Counter(entity.dxftype() for entity in entities).items()))


def selected_counts(entity_counts, types):
    return {name: entity_counts.get(name, 0) for name in types}


def block_references(entities):
    return dict(sorted(Counter(
        entity.dxf.name for entity in entities if entity.dxftype() == "INSERT"
    ).items()))


def audit_entry(issue):
    entity = issue.entity
    return {
        "code": int(issue.code),
        "message": issue.message,
        "entity_type": entity.dxftype() if entity is not None else None,
        "entity_handle": entity.dxf.get("handle") if entity is not None else None,
    }


def inspect_dxf(path):
    document = ezdxf.readfile(path)
    modelspace = list(document.modelspace())
    modelspace_counts = counts(modelspace)
    definitions = []
    definition_counts = Counter()
    for block in document.blocks:
        if block.block_record.is_any_layout:
            continue
        entities = list(block)
        entity_counts = counts(entities)
        definition_counts.update(entity_counts)
        definitions.append({
            "name": block.name,
            "entity_counts": entity_counts,
            "block_references": block_references(entities),
            "acis_entity_counts": selected_counts(entity_counts, ACIS_TYPES),
            "candidate_curve_counts": selected_counts(entity_counts, CANDIDATE_TYPES),
        })

    classes = []
    for item in document.classes:
        cpp_name = item.dxf.get("cpp_class_name", "")
        classes.append({
            "name": item.dxf.name,
            "cpp_class_name": cpp_name,
            "application_name": item.dxf.get("app_name", ""),
            "was_a_proxy": bool(item.dxf.get("was_a_proxy", 0)),
            "is_an_entity": bool(item.dxf.get("is_an_entity", 0)),
            "custom_class_candidate": bool(cpp_name and not cpp_name.startswith("AcDb")),
        })

    all_counts = counts(entity for entity in document.entitydb.values() if entity.is_alive)
    units_code = document.header.get("$INSUNITS")
    try:
        units_name = ezdxf.units.unit_name(units_code) if units_code is not None else "Missing"
    except (ValueError, IndexError, TypeError):
        units_name = "Unknown"
    explicit_units = isinstance(units_code, int) and 1 <= units_code <= 24
    solid_count = modelspace_counts.get("3DSOLID", 0) + definition_counts.get("3DSOLID", 0)
    acis_count = sum(modelspace_counts.get(t, 0) + definition_counts.get(t, 0) for t in ACIS_TYPES)
    candidate_count = sum(modelspace_counts.get(t, 0) + definition_counts.get(t, 0) for t in CANDIDATE_TYPES)
    blockers = []
    if not explicit_units:
        blockers.append("Drawing units are missing or not explicit. Confirm units with the source designer.")
    if acis_count:
        blockers.append("ACIS geometry is present. This inspector does not extract its surfaces or pipe centerlines.")
    if not candidate_count:
        blockers.append("No explicit LINE, ARC or polyline candidates were found in modelspace or block definitions.")
    blockers.append("Pipe identities, endpoints, diameters and connectivity have not been established.")

    # The auditor may repair the in-memory document. Never save it back to disk.
    auditor = document.audit()
    return {
        "file_name": path.name,
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "ezdxf_version": ezdxf.__version__,
        "dxf_version": document.dxfversion,
        "units": {
            "header": "$INSUNITS",
            "code": units_code,
            "name": units_name,
            "explicit": explicit_units,
        },
        "modelspace_entity_counts": modelspace_counts,
        "layer_names": sorted(layer.dxf.name for layer in document.layers),
        "modelspace_block_references": block_references(modelspace),
        "block_definitions": sorted(definitions, key=lambda item: item["name"]),
        "acis_geometry": {
            "modelspace": selected_counts(modelspace_counts, ACIS_TYPES),
            "block_definitions": selected_counts(definition_counts, ACIS_TYPES),
            "stored_3dsolid_count": solid_count,
            "geometry_extracted": False,
        },
        "proxy_and_custom_data": {
            "proxy_entity_count": all_counts.get("ACAD_PROXY_ENTITY", 0),
            "proxy_object_count": all_counts.get("ACAD_PROXY_OBJECT", 0),
            "custom_class_candidates_present": any(item["custom_class_candidate"] for item in classes),
            "classes_with_proxy_history_present": any(item["was_a_proxy"] for item in classes),
            "custom_class_detection": "Candidates have a C++ class name that does not start with AcDb. This is a naming heuristic.",
            "registered_classes": classes,
            "note": "Class registrations or proxy history do not prove that instances or their original data survived conversion.",
        },
        "candidate_explicit_curves": {
            "modelspace": selected_counts(modelspace_counts, CANDIDATE_TYPES),
            "block_definitions": selected_counts(definition_counts, CANDIDATE_TYPES),
            "warning": "These entities are not guaranteed pipe centerlines. No pipe connectivity or diameters are inferred.",
        },
        "structural_audit": {
            "error_count": len(auditor.errors),
            "in_memory_fix_count": len(auditor.fixes),
            "errors": [audit_entry(issue) for issue in auditor.errors],
            "in_memory_fixes": [audit_entry(issue) for issue in auditor.fixes],
            "source_saved": False,
            "scope": "DXF structure only. This does not check design compliance or conversion fidelity.",
        },
        "pipe_graph_ready": False,
        "blockers": blockers,
        "count_scope": "Block counts describe unique stored definition entities. Layout blocks are excluded. Inserts are not expanded into world coordinates or repeated instances.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="DXF drawing to inspect")
    parser.add_argument("--output", "-o", type=Path, help="Optional JSON report path")
    args = parser.parse_args()
    if args.output and args.output.resolve() == args.input.resolve():
        parser.error("The report path must be different from the source drawing")
    try:
        report = inspect_dxf(args.input)
        serialized = json.dumps(report, indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(serialized, encoding="utf-8")
        else:
            sys.stdout.write(serialized)
    except (OSError, ezdxf.DXFError, UnicodeError) as error:
        parser.exit(1, f"Could not inspect drawing: {error}\n")
    if args.output:
        print(f"Report saved to {args.output}")


if __name__ == "__main__":
    main()
