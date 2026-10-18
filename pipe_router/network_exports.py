"""Revision-bound designer bundles with independently checked coordinates.

These files are implementation instructions and previews. Writing a bundle never
changes a CAD drawing and never grants approval to replace native solids.
"""
from __future__ import annotations

from copy import deepcopy
import csv
import json
import math
from pathlib import Path
import tempfile
from typing import Any

from .network_manufacturing import manufacture_network

_EXPORT_FILES = {
    "instructions": "instructions.json", "instructions_markdown": "instructions.md",
    "xlsx": "components.xlsx", "csv": "components.csv",
    "dxf": "network-preview.dxf", "result": "network-result.json",
}
_COMPONENT_COLUMNS = ["Component ID", "Original CAD ID", "Piping system", "Component type",
    "Nominal diameter mm", "Fitting specification", "Quantity", "Global X", "Global Y", "Global Z",
    "Units", "Installation notes", "Verification status"]
_COORDINATE_COLUMNS = ["Route state", "Component ID", "Original CAD ID", "Piping system", "Point index",
                       "Global X", "Global Y", "Global Z", "Units", "Drawing revision"]


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, indent=2)


def _display(value):
    return "MISSING — UNVERIFIED" if value is None or value == "" else value


def _safe_csv(value):
    # Spreadsheet programs interpret these leading characters as formulas.
    # JSON instructions retain exact identifiers; CSV protects on-open execution.
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'"+value
    return value


def _coordinates(original, candidate):
    records = []
    for state, network in (("original", original), ("proposed", candidate)):
        revision = network.get("drawing", {}).get("revision")
        for span in network.get("spans", []):
            for index, point in enumerate(span["points"]):
                records.append([state, span["id"], _display(span.get("cad_handle")), span.get("system_id"), index,
                                *point, "mm", revision])
    return records


def _components(network, manufacture):
    rows = []
    for piece in manufacture["pieces"]:
        point = piece["points"][0] if piece["points"] else [None]*3
        rows.append([piece["id"], _display(piece.get("cad_handle")), piece.get("system_id"), "manufacturing piece",
                     _display(piece.get("nominal_diameter_mm")), "See physical fitting schedule; fabrication method requires confirmation", 1,
                     *point, "mm", "; ".join(piece.get("notes", []))+f" Developed length {piece['length_mm']:.9g} mm; bends {piece['bend_count']}", piece["verification_status"]])
    for fitting in manufacture["fittings"]:
        point = fitting.get("intersection", fitting.get("center", [None]*3))
        spec = f"Elbow {fitting.get('angle_deg', 'MISSING')} degrees; centerline radius {fitting.get('radius_mm', 'MISSING')} mm; source {_display(fitting.get('source'))}"
        rows.append([fitting["id"], _display(fitting.get("cad_handle")), fitting.get("system_id"), "elbow requirement",
                     _display(fitting.get("nominal_diameter_mm")), spec, 1, *point, "mm",
                     "Location is centerline tangent intersection; tangent points and arc center are in instructions.json. Catalog selection is not a purchase approval.", fitting["verification_status"]])
    for node in network.get("nodes", []):
        rows.append([node["id"], _display(node.get("cad_handle")), node.get("system_id"), node.get("type"),
                     _display(node.get("nominal_diameter_mm")), _display((node.get("dimensions") or {}).get("specification")), 1,
                     *node["position"], "mm", "Fixed connection: preserve global coordinates" if node.get("fixed") else "Movable only within declared permitted region", "UNVERIFIED"])
    return rows


def _write_xlsx(path, components, coordinates, manufacture, validation, metadata):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
    workbook = Workbook()
    workbook.remove(workbook.active)
    def sheet(name, headings, rows):
        worksheet = workbook.create_sheet(name)
        worksheet.append(headings)
        for row in rows:
            worksheet.append(row)
            # Preserve exact CAD/engineering IDs as text, including '=' prefixes.
            for cell in worksheet[worksheet.max_row]:
                if isinstance(cell.value, str):
                    cell.data_type = "s"
        worksheet.freeze_panes = "A2"
        worksheet.auto_filter.ref = worksheet.dimensions
        for cell in worksheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="193D54")
        for index, heading in enumerate(headings, 1):
            worksheet.column_dimensions[get_column_letter(index)].width = min(52, max(16, len(heading)+3))
        return worksheet
    sheet("Components", _COMPONENT_COLUMNS, components)
    sheet("Coordinates", _COORDINATE_COLUMNS, coordinates)
    sheet("Manufacturing", ["Piece ID", "Span ID", "Status", "Developed length mm", "Bend count", "Start station mm", "End station mm", "Verification", "Notes"],
          [[piece["id"], piece["span_id"], piece["status"], piece["length_mm"], piece["bend_count"], piece.get("start_station_mm"), piece.get("end_station_mm"), piece["verification_status"], "; ".join(piece.get("notes", []))] for piece in manufacture["pieces"]])
    sheet("Checks", ["Check ID", "Status", "Severity", "Components", "Message", "Numerical evidence"],
          [[check["id"], check["status"], check["severity"], ", ".join(check.get("component_ids", [])), check["message"], _json(check.get("evidence", {}))] for check in validation["checks"]])
    sheet("Metadata", ["Field", "Value"], [[key, _json(value) if isinstance(value, (dict, list)) else value] for key, value in metadata.items()])
    workbook.save(path)


def _write_dxf(path, original, proposed, manufacture):
    import ezdxf
    doc = ezdxf.new("R2013")
    doc.units = 4  # INSUNITS millimetres; WCS coordinates are never translated.
    doc.appids.new("PIPEFORGE4")
    for name, color in (("PF4_ORIGINAL", 30), ("PF4_PROPOSED", 3), ("PF4_FITTINGS", 4), ("PF4_CONTEXT", 8), ("PF4_PORTS", 2), ("PF4_DIMENSIONS", 7)):
        doc.layers.new(name, dxfattribs={"color": color})
    model = doc.modelspace()
    for state, network in (("original", original), ("proposed", proposed)):
        for span in network.get("spans", []):
            entity = model.add_polyline3d(span["points"], dxfattribs={"layer": "PF4_"+state.upper()})
            entity.set_xdata("PIPEFORGE4", [(1000, state)]+[(1000, span["id"][index:index+255]) for index in range(0, len(span["id"]), 255)])
            for index, (a,b) in enumerate(zip(span["points"], span["points"][1:])):
                mid = tuple((x+y)/2 for x,y in zip(a,b))
                model.add_text(f"{span['id']}:{index} {math.dist(a,b):.3f} mm", dxfattribs={"height": 25, "insert": mid, "layer":"PF4_DIMENSIONS"})
    for fitting in manufacture["fittings"]:
        capsules = fitting.get("capsules", [])
        if capsules:
            points = [capsules[0]["start"]]+[capsule["end"] for capsule in capsules]
            model.add_polyline3d(points, dxfattribs={"layer":"PF4_FITTINGS"})
    for node in proposed.get("nodes", []):
        model.add_point(node["position"], dxfattribs={"layer":"PF4_PORTS"})
        model.add_text(node["id"], dxfattribs={"height": 25, "insert": node["position"], "layer":"PF4_PORTS"})
    for obstacle in proposed.get("obstacles", []):
        low, high = obstacle["min"], obstacle["max"]
        corners = [[high[0] if mask&1 else low[0], high[1] if mask&2 else low[1], high[2] if mask&4 else low[2]] for mask in range(8)]
        for index in range(8):
            for axis in (1,2,4):
                if not index&axis:
                    model.add_line(corners[index], corners[index|axis], dxfattribs={"layer":"PF4_CONTEXT"})
    doc.saveas(path)


def _verify_serialized_coordinates(directory, original, proposed, coordinates, components):
    """Read all delivered coordinate formats back from disk independently."""
    import ezdxf
    from openpyxl import load_workbook
    document = json.loads((directory/_EXPORT_FILES["instructions"]).read_text(encoding="utf-8"))
    original_by_id = {span["id"]:span for span in original["spans"]}
    proposed_by_id = {span["id"]:span for span in proposed["spans"]}
    for change in document["spans"]:
        if change["original_centerline_mm"] != original_by_id[change["id"]]["points"] or change["proposed_centerline_mm"] != proposed_by_id[change["id"]]["points"]:
            raise ValueError("Export JSON coordinate consistency failed")
    workbook = load_workbook(directory/_EXPORT_FILES["xlsx"], read_only=True, data_only=False)
    rows = list(workbook["Coordinates"].iter_rows(min_row=2, values_only=True))
    xlsx_components = list(workbook["Components"].iter_rows(min_row=2, values_only=True))
    workbook.close()
    if len(rows) != len(coordinates):
        raise ValueError("Export XLSX coordinate count failed")
    max_error = 0.0
    for actual, expected in zip(rows, coordinates):
        if actual[:5] != tuple(expected[:5]):
            raise ValueError("Export XLSX CAD identity consistency failed")
        for index in (5,6,7):
            max_error = max(max_error, abs(actual[index]-expected[index]))
    with (directory/_EXPORT_FILES["csv"]).open("r", encoding="utf-8-sig", newline="") as stream:
        component_rows = list(csv.reader(stream))[1:]
    if len(component_rows) != len(components) or len(xlsx_components) != len(components):
        raise ValueError("Export component schedule row count failed")
    for csv_row,xlsx_row,expected in zip(component_rows,xlsx_components,components):
        if csv_row[:4] != [str(_safe_csv(value)) for value in expected[:4]] or xlsx_row[:4] != tuple(expected[:4]):
            raise ValueError("Export component schedule identity failed")
        for index in (7,8,9):
            if expected[index] is None:
                if csv_row[index] or xlsx_row[index] is not None:
                    raise ValueError("Export invented an unknown component coordinate")
            else:
                values = (float(csv_row[index]),xlsx_row[index])
                if not all(math.isfinite(value) for value in values):
                    raise ValueError("Export has nonfinite component coordinates")
                max_error = max(max_error,*(abs(value-expected[index]) for value in values))
    dxf = ezdxf.readfile(directory/_EXPORT_FILES["dxf"])
    polylines = [entity for entity in dxf.modelspace().query("POLYLINE") if entity.dxf.layer in ("PF4_ORIGINAL", "PF4_PROPOSED")]
    expected_lines = [(state, span) for state, network in (("original",original),("proposed",proposed)) for span in network["spans"]]
    if len(polylines) != len(expected_lines) or dxf.units != 4:
        raise ValueError("Export DXF route count or units failed")
    for entity, (state, span) in zip(polylines, expected_lines):
        values = list(entity.points())
        if len(values) != len(span["points"]):
            raise ValueError("Export DXF point count failed")
        xdata = entity.get_xdata("PIPEFORGE4")
        if xdata[0].value != state or "".join(tag.value for tag in xdata[1:]) != span["id"]:
            raise ValueError("Export DXF CAD identity failed")
        for actual, expected in zip(values, span["points"]):
            max_error = max(max_error, math.dist(tuple(actual), expected))
    if max_error > 1e-6:
        raise ValueError("Export coordinate error exceeds 0.000001 mm")
    return {"id":"export.serialized_coordinate_roundtrip", "status":"PASS", "severity":"hard",
            "message":"JSON, XLSX and DXF were read back and compared with exact global-WCS routes",
            "evidence":{"max_error_mm":max_error,"tolerance_mm":1e-6,"coordinate_rows":len(rows),"dxf_units":"mm","coordinate_frame":"WCS"},
            "component_ids":list(proposed_by_id)}


def _markdown(instructions):
    lines = ["# PipeForge connected-routing implementation instructions", "", f"Status: {instructions['status']}",
             f"Source drawing: {instructions['drawing'].get('id')} / revision {instructions['source_revision']}",
             "", "Coordinates are global WCS, in millimetres. Do not move fixed ports. A bundle is not approval to replace native CAD solids.",
             "Verify the drawing revision and object handles before implementation. Review every FAIL and UNVERIFIED item before fabrication.",
             "", "Original routes: PF4_ORIGINAL; proposed routes: PF4_PROPOSED; physical bend centerlines: PF4_FITTINGS; dimensions are labelled in mm.", ""]
    for span in instructions["spans"]:
        lines.extend([f"## {span['id']} (CAD {_display(span.get('cad_handle'))})", f"System: {span['system_id']}; nominal diameter: {_display(span.get('nominal_diameter_mm'))} mm.",
                      f"Change: {'replace centerline according to coordinates after designer approval' if span['changed'] else 'preserve existing route'}.", "", "Original XYZ mm:", "```json", _json(span["original_centerline_mm"]), "```", "Proposed XYZ mm:", "```json", _json(span["proposed_centerline_mm"]), "```", ""])
    lines.extend(["## Remaining engineering decisions", *[f"- {issue}" for issue in instructions["unresolved_issues"]], "", "See components.xlsx for components, every route coordinate, per-piece takeoff, checks and source metadata."])
    return "\n".join(lines)+"\n"


def export_network(network: dict[str, Any], result: dict[str, Any], output_dir) -> dict[str, str]:
    """Write a fresh, independently validated instruction bundle; never apply CAD.

    The source digest and revision are mandatory. Supplied validation and metric
    claims are not accepted as evidence. A hard-invalid candidate is rejected.
    No-improvement/blocked reports can be exported, labelled for diagnosis only.
    """
    from .connected import network_digest, metrics
    from .network_model import normalize_network
    from .network_validation import validate_network
    source = normalize_network(network)
    source_digest = network_digest(source)
    revision = source.get("drawing", {}).get("revision")
    if not revision or result.get("source_revision") != revision:
        raise ValueError("Stale result: source drawing revision changed or is missing")
    if result.get("source_digest") != source_digest:
        raise ValueError("Stale result: source network digest changed")
    original_in_result = result.get("original_network")
    if original_in_result is not None and network_digest(normalize_network(original_in_result)) != source_digest:
        raise ValueError("Result original network does not match the current drawing snapshot")
    candidate_raw = result.get("proposed_network")
    candidate = normalize_network(candidate_raw) if candidate_raw is not None else deepcopy(source)
    if candidate.get("drawing") != source.get("drawing") or candidate.get("coordinate_system") != source.get("coordinate_system"):
        raise ValueError("Candidate drawing identity or coordinate frame changed")
    if {span["id"] for span in candidate["spans"]} != {span["id"] for span in source["spans"]}:
        raise ValueError("Candidate component identities changed")
    validation = validate_network(candidate, reference=source)
    if candidate_raw is not None and validation["hard_failures"]:
        raise ValueError("Candidate failed independent engineering validation; export denied")
    manufacture = manufacture_network(candidate)
    original_by_id = {span["id"]:span for span in source["spans"]}
    nodes_by_id = {node["id"]:node for node in candidate["nodes"]}
    unresolved = list(result.get("unresolved_issues", []))
    for check in validation["checks"]:
        if check["status"] in ("FAIL", "UNVERIFIED"):
            message = f"{check['status']}: {check['id']}: {check['message']}"
            if message not in unresolved:
                unresolved.append(message)
    spans = []
    for span in candidate["spans"]:
        original = original_by_id[span["id"]]
        spans.append({"id":span["id"], "cad_handle":span.get("cad_handle"), "instance_path":span.get("instance_path", []),
                      "system_id":span["system_id"], "changed":span["points"] != original["points"],
                      "original_centerline_mm":original["points"], "proposed_centerline_mm":span["points"],
                      "from_node":span["from_node"], "to_node":span["to_node"],
                      "fixed_connections":[{"id":identifier,"position":nodes_by_id[identifier]["position"],"cad_handle":nodes_by_id[identifier].get("cad_handle")} for identifier in (span["from_node"],span["to_node"]) if nodes_by_id[identifier].get("fixed")],
                      "nominal_diameter_mm":span.get("nominal_diameter_mm"), "outer_diameter_mm":span.get("outer_diameter_mm"),
                      "dimensions":span.get("dimensions", {}), "constraints":span.get("constraints", {}),
                      "fittings":[f for f in manufacture["fittings"] if f["span_id"] == span["id"]],
                      "manufacturing_pieces":[p for p in manufacture["pieces"] if p["span_id"] == span["id"]]})
    instructions = {"schema_version":"pipeforge.instructions/4.0", "status":result["status"], "proposal_id":result.get("proposal_id"),
                    "drawing":source.get("drawing", {}), "source_revision":revision, "source_digest":source_digest,
                    "units":"mm", "coordinate_system":source["coordinate_system"], "standards_profile":source.get("standards_profile"),
                    "spans":spans, "before_metrics":metrics(source), "after_metrics":metrics(candidate, reference=source),
                    "validation":validation, "unresolved_issues":unresolved, "native_apply_authorized":False, "source_input_units":source.get("input_units","mm"),
                    "implementation_state":"designer_review_required" if candidate_raw is not None else "diagnostic_or_preserve_existing"}
    metadata = {key:instructions[key] for key in ("schema_version","status","proposal_id","drawing","source_revision","source_digest","units","coordinate_system","standards_profile","implementation_state")}
    metadata["source_authority"] = "User-supplied Fincantieri challenge transcription; original authoritative PDFs and commercial approval have not been independently verified."
    components = _components(candidate, manufacture)
    coordinates = _coordinates(source, candidate)
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".pipeforge-export-", dir=target) as temporary:
        directory = Path(temporary)
        (directory/_EXPORT_FILES["instructions"]).write_text(_json(instructions)+"\n", encoding="utf-8")
        _write_xlsx(directory/_EXPORT_FILES["xlsx"], components, coordinates, manufacture, validation, metadata)
        with (directory/_EXPORT_FILES["csv"]).open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(_COMPONENT_COLUMNS)
            writer.writerows([[_safe_csv(value) for value in row] for row in components])
        _write_dxf(directory/_EXPORT_FILES["dxf"], source, candidate, manufacture)
        coordinate_check = _verify_serialized_coordinates(directory, source, candidate, coordinates, components)
        # Serialization evidence does not establish the source frame's
        # provenance. Preserve the independent WCS/units check and its unknowns.
        validation["checks"].append(coordinate_check)
        instructions["validation"] = validation
        _write_xlsx(directory/_EXPORT_FILES["xlsx"], components, coordinates, manufacture, validation, metadata)
        _verify_serialized_coordinates(directory, source, candidate, coordinates, components)
        (directory/_EXPORT_FILES["instructions"]).write_text(_json(instructions)+"\n", encoding="utf-8")
        (directory/_EXPORT_FILES["instructions_markdown"]).write_text(_markdown(instructions), encoding="utf-8")
        exported_result = deepcopy(result)
        exported_result.update({"validation":validation,"manufacturing":manufacture,"export_coordinate_check":coordinate_check,
                                "before":instructions["before_metrics"],"after":instructions["after_metrics"],
                                "changed_span_ids":[span["id"] for span in spans if span["changed"]]})
        (directory/_EXPORT_FILES["result"]).write_text(_json(exported_result)+"\n", encoding="utf-8")
        # All files are checked before exposing any of them to the caller.
        for filename in _EXPORT_FILES.values():
            (directory/filename).replace(target/filename)
    return deepcopy(_EXPORT_FILES)
