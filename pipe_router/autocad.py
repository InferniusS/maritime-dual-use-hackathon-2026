"""Read an AutoCAD DXF snapshot through the shared CAD importer.

Selections identify actual source entities or block placements. Bounding boxes
remain obstacle approximations and are never used to invent pipe centerlines.
"""

from __future__ import annotations

import base64
from copy import deepcopy
import math
from pathlib import Path
import re

from .cad_import import inspect_cad
from .cad_network import inspect_network

API_VERSION = 1
MAX_CAD_BYTES = 48 * 1024 * 1024
MAX_MATCHED_CANDIDATES = 50
MAX_SELECTED_HANDLES = 256


def service_health():
    return {
        "service": "maritime-pipe-router",
        "api_version": API_VERSION,
        "capabilities": ["cad_snapshot", "route_search", "pipe_envelopes", "challenge_rules_v3", "model_context", "review_excel"],
        "units": "mm",
    }


def decode_dxf_upload(payload):
    """Validate the shared browser/plugin upload format before creating files."""
    filename = payload.get("filename", "drawing.dxf")
    if not isinstance(filename, str) or not filename or "\x00" in filename:
        raise ValueError("Provide a DXF filename")
    # AutoCAD sends a Windows filename even when the service runs on a Mac.
    filename = Path(filename.replace("\\", "/")).name
    if Path(filename).suffix.lower() != ".dxf":
        raise ValueError("Choose a DXF file. Convert DWG to DXF with ODA File Converter first.")
    encoded = payload.get("content_base64", "")
    if not isinstance(encoded, str):
        raise ValueError("The uploaded file could not be read")
    if len(encoded) > ((MAX_CAD_BYTES + 2) // 3) * 4:
        raise ValueError("DXF files must be between 1 byte and 48 MB")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as error:
        raise ValueError("The uploaded file could not be read") from error
    if not content or len(content) > MAX_CAD_BYTES:
        raise ValueError("DXF files must be between 1 byte and 48 MB")
    return filename, content


def validate_snapshot_request(payload):
    source_units = payload.get("source_units")
    if source_units not in ("mm", "m", "in"):
        raise ValueError("Choose mm, m or in for source units")
    handles = payload.get("selected_handles")
    if not isinstance(handles, list) or not 1 <= len(handles) <= MAX_SELECTED_HANDLES:
        raise ValueError("Select between 1 and 256 source entities in AutoCAD")
    selected = []
    for handle in handles:
        if not isinstance(handle, str) or re.fullmatch(r"[0-9a-fA-F]{1,16}", handle) is None or int(handle, 16) == 0:
            raise ValueError("Selected entity handles must be nonzero hexadecimal strings")
        normalized = format(int(handle, 16), "X")
        if normalized not in selected:
            selected.append(normalized)
    document = payload.get("document", {})
    if not isinstance(document, dict):
        raise ValueError("Document details must be an object")
    clean_document = {}
    for key in ("name", "fingerprint"):
        value = document.get(key, "")
        if not isinstance(value, str) or len(value) > 1024 or "\x00" in value:
            raise ValueError(f"Document {key} must be a string of at most 1024 characters")
        clean_document[key] = value
    filename, content = decode_dxf_upload(payload)
    return {
        "filename": filename,
        "content": content,
        "source_units": source_units,
        "selected_handles": selected,
        "document": clean_document,
    }


def _candidate_sources(candidate):
    sources = candidate.get("source_instances")
    if sources is not None:
        return sources
    return [{"source_handle": handle, "instance_path": candidate.get("instance_path", [])}
            for handle in candidate.get("source_handles", [])]


def _handle_key(value):
    # The importer records each INSERT array placement as HANDLE[index].
    match = re.fullmatch(r"([0-9a-fA-F]{1,16})(?:\[\d+\])?", value) if isinstance(value, str) else None
    if match:
        return format(int(match.group(1), 16), "X")
    return value


def _matches_selection(candidate, handles):
    return any(
        _handle_key(source.get("source_handle")) in handles
        or any(_handle_key(handle) in handles for handle in source.get("instance_path", []))
        for source in _candidate_sources(candidate)
    )


def inspect_snapshot(source, *, source_units, selected_handles, document):
    """Return full cached CAD plus compact choices tied to the selected handles."""
    cad = inspect_cad(source, source_units=source_units)
    network = inspect_network(cad)
    selected = set(selected_handles)
    candidates = []
    for kind, source_candidates in (("chain", network["chains"]), ("run", cad["route_candidates"])):
        for candidate in source_candidates:
            if not _matches_selection(candidate, selected):
                continue
            length = sum(math.dist(a, b) for a, b in zip(candidate["points"], candidate["points"][1:]))
            candidates.append({
                "id": candidate["id"],
                "label": candidate.get("name", candidate["id"]),
                "kind": kind,
                "member_count": candidate.get("member_count", 1),
                "source_handles": sorted({item["source_handle"] for item in _candidate_sources(candidate)}),
                "diameter_mm": candidate["geometric_diameter"],
                "length_mm": length,
            })
    if not candidates:
        raise ValueError(
            "No pipe route could be read from the selected source objects. "
            "Select a supported pipe solid or its block placement. "
            "Object boxes are not converted into pipe centerlines."
        )
    if len(candidates) > MAX_MATCHED_CANDIDATES:
        raise ValueError(
            f"The selection matches {len(candidates)} possible routes. "
            "Select a more specific pipe object or block with at most 50 matching routes."
        )
    context = {
        "api_version": API_VERSION,
        "document": deepcopy(document),
        "selected_handles": list(selected_handles),
        "source_snapshot_sha256": cad["source_sha256"],
    }
    cad["autocad_context"] = context
    warnings = list(dict.fromkeys([
        *cad.get("warnings", []), *network.get("warnings", []),
        "The snapshot uses the source units supplied by AutoCAD. Confirm the units before applying a result.",
        "Selected routes come from extracted cylinder and bend geometry. Pipe identity and required connections still need review.",
        "Nearby obstacles come from extracted drawing geometry. Missing or unsupported geometry can leave untested areas.",
    ]))
    summary = {
        "api_version": API_VERSION,
        "source_sha256": cad["source_sha256"],
        "source_units": source_units,
        "units": "mm",
        "candidates": candidates,
        "warnings": warnings,
        "counts": {"drawing": cad.get("counts", {}), "network": network["counts"],
                   "matched_candidates": len(candidates)},
    }
    return cad, summary
