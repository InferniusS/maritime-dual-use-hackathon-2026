"""Screen every supported section in an immutable CAD snapshot.

This visits the complete inferred inventory, not a capped selection of promising
routes. Unsupported geometry and ambiguous topology remain explicit review gaps.
No optimization or connection waivers occur during this operation.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import time

from . import core, drawing_review
from .autocad import _candidate_sources
from .cad_layout import _overlaps, _solid_boxes

MAX_STORED_GEOMETRY_FINDINGS_PER_ROUTE = 20


class _SolidIndex:
    """Read-only bounding-volume tree over exactly the prepared solid records."""

    def __init__(self, boxes):
        self.boxes = boxes
        self.by_key = {box["key"]: box for box in boxes}
        self.root = self._build(list(range(len(boxes)))) if boxes else None

    def _build(self, positions):
        bounds = {"min": [min(self.boxes[p]["min"][a] for p in positions) for a in range(3)],
                  "max": [max(self.boxes[p]["max"][a] for p in positions) for a in range(3)]}
        if len(positions) <= 16:
            return bounds, positions, None
        axis = max(range(3), key=lambda a: bounds["max"][a] - bounds["min"][a])
        positions.sort(key=lambda p: self.boxes[p]["min"][axis] + self.boxes[p]["max"][axis])
        midpoint = len(positions) // 2
        return bounds, self._build(positions[:midpoint]), self._build(positions[midpoint:])

    def query(self, bounds):
        found = []

        def visit(node):
            if node is None or not _overlaps(node[0], bounds):
                return
            if node[2] is None:
                found.extend(p for p in node[1] if _overlaps(self.boxes[p], bounds))
            else:
                visit(node[1])
                visit(node[2])

        visit(self.root)
        # Keep obstacle identity/order identical to ordinary individual prepare.
        return [self.boxes[p] for p in sorted(found)]


def _inventory_geometry(cad, boxes, unread):
    imported = cad.get("counts", {})
    declared = imported.get("solid_instances")
    missing = max(0, declared - len(boxes)) if isinstance(declared, int) else None
    unsupported = deepcopy(cad.get("unsupported_geometry", {}))
    # Face/edge records are not unique source objects; never sum them into an
    # object count. An imported partial solid can have many unsupported faces.
    entity_count = sum(count for kind, count in unsupported.items()
                       if not kind.startswith(("face:", "edge:")) and isinstance(count, int))
    failed = imported.get("failed_solid_instances", 0)
    failed = failed if isinstance(failed, int) else 0
    return {
        "source_declared_solid_instances": declared,
        "source_solids_with_geometry": len(boxes),
        "source_solid_instances_missing_geometry": missing,
        "fallback_geometry_instances": sum("capsules" not in box for box in boxes),
        "unsupported_objects": failed + entity_count,
        "unsupported_object_count_scope": "Failed solid instances plus unsupported non-face/edge entity records; partial solid faces are counted separately.",
        "unsupported_geometry_records": unsupported,
        **unread,
    }


def scan_drawing(cad, *, progress=None, should_cancel=None, system_type="pressurized",
                 clearance=50.8, padding=500, rule_profile="fmg-v3", model_context=None,
                 engineering=None, reverse_flow=False, min_slope=0.01):
    """Return a full numbered inventory, retaining every failure or cancellation.

    ``progress`` receives independent compact dictionaries. ``should_cancel`` is
    checked between sections; cancellation never discards completed rows. There
    is no whole-scan time cap. Per-section geometry limits remain explicit and
    prevent a dense unsupported section from silently passing.
    """
    started = time.perf_counter()
    core._mapping(cad, "cad")
    if cad.get("provenance") != "cad" or cad.get("units") != "mm" or cad.get("preview"):
        raise ValueError("A full CAD import in millimetres is required for a whole-drawing scan")
    settings = drawing_review._screen_settings(system_type=system_type, clearance=clearance,
        padding=padding, rule_profile=rule_profile, reverse_flow=reverse_flow, min_slope=min_slope)
    network, candidates = drawing_review._review_inventory(cad)
    solids = _solid_boxes(cad)
    prepared = {"network": network, "solids": solids, "solid_index": _SolidIndex(solids[0]), "screening_batches": True}
    rows = []
    for number, (kind, candidate) in enumerate(candidates, 1):
        row = drawing_review._screening_row(kind, candidate)
        radius = row["diameter_mm"] / 2
        row.update(number=number, global_number=number,
            source_instances=deepcopy(_candidate_sources(candidate)),
            start=list(row["start"]), end=list(row["end"]),
            bounds={"min": [min(point[a] for point in candidate["points"]) - radius for a in range(3)],
                    "max": [max(point[a] for point in candidate["points"]) + radius for a in range(3)]},
            review_status="not_processed", review_message="This section has not yet been processed.",
            processed=False)
        rows.append(row)
    counts = Counter({"not_processed": len(rows)})
    processed = reviewed = 0
    cancelled = False

    def notify(current=None):
        if progress:
            progress({"phase": "screening", "processed_routes": processed,
                "reviewed_routes": reviewed, "total_supported_routes": len(rows),
                "not_processed_routes": len(rows) - processed,
                "current_number": current["number"] if current else None,
                "current_id": current["id"] if current else None,
                "counts": dict(counts), "elapsed_seconds": time.perf_counter() - started})

    notify()
    for (_, candidate), row in zip(candidates, rows):
        if should_cancel and should_cancel():
            cancelled = True
            break
        try:
            drawing_review._screen_candidate(cad, candidate, row, prepared,
                model_context=model_context, engineering=engineering, **settings)
        except Exception as error:
            # One malformed/unsupported section must not hide later sections.
            # Preserve an explicit diagnostic, never a successful fallback.
            row.update(review_status="needs_review", review_message=f"Section check could not finish ({type(error).__name__}): {error}",
                       geometry_checked=False, full_compliance_verified=False)
        if row["review_status"] in ("not_processed", "not_reviewed"):
            row["review_status"] = "needs_review"
        row["processed"] = True
        row["finding_counts"] = dict(Counter(item.get("code", "geometry") for item in row["findings"]))
        row["omitted_geometry_findings"] = max(0, len(row["findings"]) - MAX_STORED_GEOMETRY_FINDINGS_PER_ROUTE)
        row["findings"] = row["findings"][:MAX_STORED_GEOMETRY_FINDINGS_PER_ROUTE]
        if row["omitted_geometry_findings"]:
            row["review_message"] += f" The first {MAX_STORED_GEOMETRY_FINDINGS_PER_ROUTE} geometry findings are listed; all finding counts are retained. Select this section for the full check."
        for finding in row.get("rule_findings", []):
            if finding.get("id") == "shock_clearance":
                measured = finding.get("measured", {})
                hits = measured.get("collisions", [])
                measured["collision_count"] = len(hits)
                measured["omitted_collisions"] = max(0, len(hits) - MAX_STORED_GEOMETRY_FINDINGS_PER_ROUTE)
                measured["collisions"] = hits[:MAX_STORED_GEOMETRY_FINDINGS_PER_ROUTE]
                finding["source_refs"] = finding.get("source_refs", [])[:MAX_STORED_GEOMETRY_FINDINGS_PER_ROUTE]
        counts["not_processed"] -= 1
        counts[row["review_status"]] += 1
        processed += 1
        reviewed += bool(row["geometry_checked"])
        notify(row)
    if cancelled:
        for row in rows[processed:]:
            row["review_message"] = "Scan cancelled before this section. Run the scan again to check it."
    geometry = _inventory_geometry(cad, solids[0], solids[1])
    limitations = [
        "Every supported inferred section is inventoried. Unsupported geometry and ambiguous branches are reported separately; this is not proof that every physical pipe was recognized.",
        "Each section is checked locally against the extracted surrounding geometry. Other systems, ship spaces, equipment and hidden geometry can be checked only when supplied and supported.",
        "Intended endpoint contacts are never waived automatically. Conservative envelopes can flag valid connections; a designer must verify them.",
        "No obvious detour is not proof of global optimality. Potential improvement requires a separate feasible route search.",
        "Missing nominal sizes, commercial fittings, supports, installation and access evidence remain rule findings requiring designer review.",
        "Completing this scan does not create a replacement model or certify buildability, system functionality, or full specification compliance.",
        "One selected system type and flow direction apply to this scan. Mixed pressure and drain systems need separately verified system assignments.",
        f"The first {MAX_STORED_GEOMETRY_FINDINGS_PER_ROUTE} geometry findings per section are retained with complete counts; detailed individual checking is available for sections exceeding this display limit.",
    ]
    rule_coverage, rule_evidence = {}, {}
    for row in rows:
        for finding in row.get("rule_findings", []):
            aggregate = rule_coverage.setdefault(finding["id"], Counter())
            aggregate[finding["status"]] += 1
            evidence = rule_evidence.setdefault(finding["id"], Counter())
            evidence[finding.get("evidence_kind", "not_classified")] += 1
    review = {
        "mode": "whole_drawing_scan", "status": "cancelled" if cancelled else "completed",
        "coverage": "all_supported_routes_screened" if reviewed == len(rows) else "partial_supported_route_screening",
        "total_inventoried": len(rows), "total_supported_routes": len(rows), "listed_routes": len(rows),
        "processed_routes": processed, "reviewed_routes": reviewed,
        "needs_review_routes": counts["needs_review"], "not_processed_routes": len(rows) - processed,
        "unreviewed_routes": len(rows) - reviewed, "omitted_from_list": 0,
        "unsupported_objects": geometry["unsupported_objects"], "counts": dict(counts),
        "runtime_seconds": time.perf_counter() - started,
        "limits": {"maximum_listed_routes": None, "screening_budget_seconds": None,
            "maximum_segment_obstacle_pairs_per_route": drawing_review.MAX_REVIEW_SEGMENT_OBSTACLE_PAIRS,
            "maximum_stored_geometry_findings_per_route": MAX_STORED_GEOMETRY_FINDINGS_PER_ROUTE},
        "geometry_counts": geometry, "rule_coverage": {key: dict(value) for key, value in rule_coverage.items()},
        "rule_evidence": {key: dict(value) for key, value in rule_evidence.items()},
        "complete_model_verified": False, "limitations": limitations,
    }
    # Common rule definitions are stored once, not thousands of times in the
    # AutoCAD response. Each row retains its independent outcome/measurement.
    catalog = {}
    import hashlib
    import json
    for row in rows:
        for finding in row.get("rule_findings", []):
            definition = {key: finding.pop(key) for key in ("source", "required", "message", "evidence_note") if key in finding}
            signature = hashlib.sha256(json.dumps(definition, sort_keys=True).encode()).hexdigest()[:16]
            reference = finding["id"] + ":" + signature
            catalog.setdefault(reference, definition)
            finding["rule_ref"] = reference
            finding.pop("rule_id", None)
            if finding["status"] == "fail":
                finding["message"] = definition.get("message", "")
    return {"candidates": rows, "review": review, "network_counts": network["counts"], "rule_catalog": catalog,
        "inventory_findings": {"invalid_candidates": deepcopy(network["invalid_candidates"]),
            "duplicate_candidates": deepcopy(network["duplicate_candidates"]),
            "closed_components": deepcopy(network["closed_components"]),
            "branch_or_ambiguous_junctions": deepcopy([junction for junction in network["junctions"]
                if junction["reason"] == "branch_or_ambiguous_cluster"])},
        "warnings": list(dict.fromkeys([*cad.get("warnings", []), *network["warnings"], *limitations]))}


def write_drawing_scan(summary, folder):
    """Write an all-section workbook and searchable report with stable numbers."""
    import html
    import json
    from pathlib import Path
    from .review_export import EVIDENCE_LABELS, evidence_labels, write_workbook

    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "result.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    review = summary["review"]
    rows = [["Global number", "Section ID", "Source section", "Source handles", "Source instances", "Screen", "Finding",
             "Length mm", "Inferred OD mm", "Geometry flags", "Endpoint contacts", "Start XYZ mm", "End XYZ mm", "Bounds mm", "Geometry checked"]]
    findings = [["Global number", "Section ID", "Rule / geometry code", "Result", "Finding", "Measurement / obstacle", "Evidence kind", "Evidence completeness", "Evidence note"]]
    for item in summary["candidates"]:
        number = item["global_number"]
        rows.append([number, item["id"], item["label"], item["source_handles"], item["source_instances"],
            drawing_review.STATUS_LABELS[item["review_status"]], item["review_message"], item["length_mm"],
            item["diameter_mm"], item["issue_count"], item["endpoint_contact_count"], item["start"], item["end"], item["bounds"], item["geometry_checked"]])
        for raw_finding in item.get("findings", []) + item.get("rule_findings", []):
            finding = {**summary.get("rule_catalog", {}).get(raw_finding.get("rule_ref"), {}), **raw_finding}
            findings.append([number, item["id"], finding.get("id", finding.get("code", "")), finding.get("status", "flag"),
                finding.get("message", ""), finding.get("measured", finding.get("obstacle", "")),
                *evidence_labels(finding)])
        if item.get("omitted_geometry_findings"):
            findings.append([number, item["id"], "additional_geometry_findings", "flag",
                f"{item['omitted_geometry_findings']} additional geometry finding details omitted; full counts retained.", item.get("finding_counts", {})])
    rule_rows = [["Rule", "Pass", "Fail", "Needs review", "Not applicable", "Evidence origins"]]
    for rule, counts in review.get("rule_coverage", {}).items():
        origins = ', '.join(f'{EVIDENCE_LABELS.get(kind, "Evidence not classified")}: {count}'
            for kind, count in review.get("rule_evidence", {}).get(rule, {}).items())
        rule_rows.append([rule, *[counts.get(status, 0) for status in ("pass", "fail", "needs_review", "not_applicable")], origins])
    write_workbook([("Whole drawing scan", rows), ("Findings", findings), ("Rule coverage", rule_rows),
        ("Coverage", [["Item", "Value"], ["Source hash", summary.get("source_sha256", "")],
            *[[key, value] for key, value in review.items()]]),
        ("Inventory gaps", [["Type", "Finding"], *[[key, value] for key, values in summary.get("inventory_findings", {}).items() for value in values]])],
        folder / "route-review.xlsx")
    e = lambda value: html.escape(str(value), quote=True)
    def display(value):
        if isinstance(value, float):
            return f"{value:,.1f}"
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)
    table, cards = [], []
    for item in summary["candidates"]:
        status = item["review_status"]
        number = item["global_number"]
        search = " ".join([str(number), item["id"], item["label"], *item["source_handles"], item["review_message"]]).lower()
        cells = [number, item["label"], ', '.join(item["source_handles"]), drawing_review.STATUS_LABELS[status],
                 item["review_message"], item["length_mm"], item["diameter_mm"], item["issue_count"], item["start"], item["end"]]
        attributes = f' data-number="{number}" data-status="{e(status)}" data-search="{e(search)}"'
        table.append('<tr' + attributes + '>' + ''.join('<td>' + e(display(value)) + '</td>' for value in cells) + '</tr>')
        cards.append('<article' + attributes + '><h3>Section ' + str(number) + ' · ' + e(item["label"]) + '</h3><p><strong>Source handles: '
            + e(', '.join(item["source_handles"])) + '</strong></p><p>' + e(drawing_review.STATUS_LABELS[status]) + ' · '
            + e(item["review_message"]) + '</p><p>Length ' + e(display(item["length_mm"])) + ' mm · OD ' + e(display(item["diameter_mm"]))
            + ' mm</p><p>Start XYZ mm: ' + e(display(item["start"])) + '<br>End XYZ mm: ' + e(display(item["end"])) + '</p></article>')
    coverage_table = ''.join('<tr>' + ''.join('<td>' + e(value) + '</td>' for value in row) + '</tr>' for row in rule_rows[1:])
    status_options = ''.join('<option value="' + e(key) + '">' + e(label) + '</option>' for key, label in drawing_review.STATUS_LABELS.items() if key != "not_reviewed")
    unsupported = review.get("geometry_counts", {})
    page = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Whole drawing pipe scan</title><style>
body{font:16px system-ui;margin:0;background:#eef3f7;color:#122b42}main{max-width:1440px;margin:auto;padding:28px}h1{font-size:36px}section{background:white;padding:22px;border-radius:12px;margin:18px 0}table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;vertical-align:top;border-bottom:1px solid #d3dde5;padding:10px}th{background:#143852;color:white;position:sticky;top:0}a{color:#006b79}.scroll{overflow:auto;max-height:68vh}.note{color:#455b6b}.filters{display:flex;gap:18px;flex-wrap:wrap;margin:18px 0}label{display:grid;gap:6px}input,select{font:inherit;padding:8px;border:1px solid #8093a1;border-radius:6px}input{min-width:300px}.metrics{display:flex;gap:28px;flex-wrap:wrap}.metrics strong{font-size:28px;display:block}.print-cards{display:none}[hidden]{display:none!important}details{margin-top:14px}@media print{main{padding:0}body{background:white}section{padding:8px}.scroll,.filters{display:none}.rule-coverage{display:block;max-height:none;overflow:visible}.print-cards{display:block}article{break-inside:avoid;border-bottom:1px solid #888;padding:8px 0}th{position:static}}
</style><main><p>MARITIME / WHOLE DRAWING SCAN</p><h1>Find the sections that need attention</h1><section><h2>Coverage of this snapshot</h2><div class="metrics">'''
    page += ''.join('<div><strong>' + str(value) + '</strong>' + e(label) + '</div>' for value, label in (
        (review['total_inventoried'], 'inferred sections inventoried'), (review['processed_routes'], 'sections processed'),
        (review['reviewed_routes'], 'geometry checks completed'), (review['needs_review_routes'], 'sections with incomplete geometry checks'),
        (review['not_processed_routes'], 'not processed')))
    page += '</div><p>Every inventoried section is listed below. Processing a section does not mean it passed. Geometry flags include possible intended contacts and conservative envelopes.</p>'
    page += '<p>Full model compliance and global optimality are not verified. Missing rule evidence remains visible in Rule coverage.</p><p><a href="route-review.xlsx">Download Excel: all sections and findings</a> · <a href="result.json">Download full scan result</a></p></section>'
    page += '<section><h2>Locate a section in AutoCAD</h2><p>Use <strong>MARITIMEFIND</strong> and the global section number from this scan. Numbers remain attached to their source section when you filter this report. Quick-review numbering is separate.</p>'
    page += '<div class="filters"><label>Search number, source handle or finding<input id="search" type="search" placeholder="Section number or source handle"></label><label>Screen result<select id="status"><option value="">All sections</option>' + status_options + '</select></label></div><p id="visible-count"></p><div class="scroll"><table><thead><tr>'
    page += ''.join('<th>' + e(header) + '</th>' for header in ("Global #", "Source section", "Source handles", "Screen", "Finding", "Length mm", "OD mm", "Geometry flags", "Start XYZ mm", "End XYZ mm"))
    page += '</tr></thead><tbody id="sections">' + ''.join(table) + '</tbody></table></div><div class="print-cards" id="cards">' + ''.join(cards) + '</div></section>'
    page += '<section><h2>Rule coverage</h2><p>Counts cover only completed section assessments. A pass for one supplied measurement does not establish complete system compliance; Excel records the evidence type and completeness for each finding.</p><div class="scroll rule-coverage"><table><thead><tr>'
    page += ''.join('<th>' + e(header) + '</th>' for header in rule_rows[0]) + '</tr></thead><tbody>' + coverage_table + '</tbody></table></div></section>'
    page += '<section><h2>Geometry and designer review gaps</h2><p>' + e(unsupported.get('source_solids_with_geometry', 0)) + ' source solid instances have extracted geometry; ' + e(unsupported.get('fallback_geometry_instances', 0)) + ' use fallback boxes. ' + e(unsupported.get('unsupported_objects', 0)) + ' failed solid or unsupported entity records remain; unsupported faces and edges are recorded separately.</p><details><summary>Detailed geometry inventory</summary><pre>' + e(json.dumps(unsupported, indent=2)) + '</pre></details><ul>' + ''.join('<li>' + e(item) + '</li>' for item in summary.get('warnings', [])) + '</ul><p class="note">Snapshot: ' + e(summary.get('source_sha256', '')) + '</p></section></main>'
    page += r'''<script>
const search=document.getElementById('search'),status=document.getElementById('status');
const rows=[...document.querySelectorAll('#sections tr')],cards=[...document.querySelectorAll('#cards article')];
function filter(){const term=search.value.trim().toLowerCase();let count=0;rows.forEach((row,i)=>{const show=(!status.value||row.dataset.status===status.value)&&(!term||(/^\d+$/.test(term)?row.dataset.number===term:row.dataset.search.includes(term)));row.hidden=!show;cards[i].hidden=!show;if(show)count++;});document.getElementById('visible-count').textContent=count+' of '+rows.length+' sections shown';}
search.addEventListener('input',filter);status.addEventListener('change',filter);filter();
</script></html>'''
    (folder / "report.html").write_text(page, encoding="utf-8")
