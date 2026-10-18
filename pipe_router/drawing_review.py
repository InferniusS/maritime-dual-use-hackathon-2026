"""Bounded, read-only screening of inferred routes in one immutable CAD snapshot.

Screening never waives contacts, searches a replacement, or certifies optimality.
The caller must still review a selected route and its connections before routing.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import html
import json
import math
import time

from . import core
from .autocad import API_VERSION, _candidate_sources
from .cad_import import inspect_cad
from .cad_layout import _solid_boxes, prepare_cad_layout
from .cad_network import inspect_network
from .challenge_rules import assess_layout
from .workflow import _validate_normalized, normalize_layout, validate_layout_route

MAX_REVIEW_ROUTES = 50
MAX_REVIEW_SECONDS = 30.0
MAX_REVIEW_SEGMENT_OBSTACLE_PAIRS = 100_000
STATUS_LABELS = {
    "attention": "Check flagged geometry / rules",
    "opportunity": "Potential routing improvement",
    "no_obvious_detour": "No obvious detour",
    "needs_review": "Designer review needed",
    "not_reviewed": "Not screened",
    "not_processed": "Not processed",
}


def _measures(candidate):
    points = candidate["points"]
    length = sum(math.dist(a, b) for a, b in zip(points, points[1:]))
    variation = [sum(abs(b[i] - a[i]) for a, b in zip(points, points[1:])) for i in range(3)]
    backtrack = sum(max(0, variation[i] - abs(points[-1][i] - points[0][i])) for i in range(3))
    return length, backtrack


def _screen_settings(*, system_type, clearance, padding, rule_profile, reverse_flow, min_slope):
    if system_type not in ("pressurized", "gravity"):
        raise ValueError("system_type must be pressurized or gravity")
    if rule_profile not in ("fmg-v3", "prototype-unverified"):
        raise ValueError("Choose a supported rule profile")
    if not isinstance(reverse_flow, bool):
        raise ValueError("reverse_flow must be true or false")
    min_slope = core._number(min_slope, "min_slope", positive=True)
    clearance = core._number(clearance, "clearance")
    padding = core._number(padding, "padding")
    if clearance < 0 or padding < 0:
        raise ValueError("Clearance and padding must be nonnegative")
    if rule_profile == "fmg-v3" and clearance < 50.8 - core.EPSILON:
        raise ValueError("The V3 challenge profile requires at least 50.8 mm shock clearance")
    return {"system_type": system_type, "clearance": clearance, "padding": padding,
            "rule_profile": rule_profile, "reverse_flow": reverse_flow, "min_slope": min_slope}


def _review_inventory(cad):
    network = inspect_network(cad)
    members = {identity for chain in network["chains"] for identity in chain["member_ids"]}
    omitted = {record.get("candidate_id") for record in network["invalid_candidates"] + network["duplicate_candidates"]}
    candidates = [("chain", item) for item in network["chains"]]
    candidates += [("run", item) for item in cad["route_candidates"] if isinstance(item, dict) and item.get("id") not in members | omitted]
    # Long connected runs are useful review units. Very short cylinders may be
    # fittings or fasteners; their semantic identity is never guessed here.
    candidates.sort(key=lambda pair: (
        _measures(pair[1])[0] < 2 * pair[1]["geometric_diameter"],
        -pair[1].get("member_count", 1), -_measures(pair[1])[1], -_measures(pair[1])[0], pair[1]["id"]))
    return network, candidates


def _screening_row(kind, candidate):
    length, backtrack = _measures(candidate)
    return {
        "id": candidate["id"], "label": candidate.get("name", candidate["id"]),
        "kind": kind, "member_count": candidate.get("member_count", 1),
        "source_handles": sorted({item["source_handle"] for item in _candidate_sources(candidate)}),
        "diameter_mm": candidate["geometric_diameter"], "length_mm": length,
        "axis_backtracking_mm": backtrack, "start": candidate["points"][0], "end": candidate["points"][-1],
        "review_status": "not_reviewed", "review_message": "Screening time budget reached; select to inspect this route individually.",
        "issue_count": 0, "endpoint_contact_count": 0, "geometry_checked": False,
        "findings": [], "full_compliance_verified": False,
    }


def _screen_layout_batches(layout, *, rule_profile):
    """Check all local obstacles in bounded groups; never change routing limits."""
    batches, current, capsules = [], [], 0
    for obstacle in layout["obstacles"]:
        count = len(obstacle.get("capsules", []))
        if current and (len(current) >= core.MAX_OBSTACLES or capsules + count > core.MAX_TOTAL_CAPSULES):
            batches.append(current)
            current, capsules = [], 0
        current.append(obstacle)
        capsules += count
    batches.append(current)
    combined, assessment, seen = None, None, set()
    shock_hits, shock_refs = [], []
    obstacle_count = 0
    for obstacles in batches:
        chunk = {**layout, "obstacles": obstacles}
        # Context was applied once while preparing this whole section. Reapplying
        # it here would append all context obstacles again and defeat batching.
        normalized = normalize_layout(chunk)
        checked = _validate_normalized(normalized, normalized["pipe"]["original_route"],
                                       allow_pressure_polyline=True)
        if combined is None:
            combined = {**checked, "issues": []}
        for issue in checked["issues"]:
            fingerprint = json.dumps(issue, sort_keys=True)
            if fingerprint not in seen:
                seen.add(fingerprint)
                combined["issues"].append(issue)
        if rule_profile == "fmg-v3":
            chunk_assessment = assess_layout(chunk)
            if assessment is None:
                assessment = chunk_assessment
            shock = next(item for item in chunk_assessment["findings"] if item["id"] == "shock_clearance")
            shock_hits.extend(shock["measured"]["collisions"])
            shock_refs.extend(shock.get("source_refs", []))
            obstacle_count += shock["measured"]["obstacle_count"]
    combined["valid"] = not combined["issues"]
    if assessment:
        shock = next(item for item in assessment["findings"] if item["id"] == "shock_clearance")
        shock["status"] = "fail" if shock_hits else "pass" if obstacle_count else "needs_review"
        shock["measured"].update(obstacle_count=obstacle_count, collisions=shock_hits)
        shock["source_refs"] = list(dict.fromkeys(shock_refs))
        assessment["counts"] = {status: sum(item["status"] == status for item in assessment["findings"])
            for status in ("pass", "fail", "needs_review", "not_applicable")}
        assessment["hard_failures"] = [item["id"] for item in assessment["findings"] if item["status"] == "fail" and item["severity"] == "error"]
        assessment["complete"] = not assessment["counts"]["needs_review"] and not assessment["counts"]["fail"]
        if "evidence_status_counts" in assessment:
            assessment["evidence_status_counts"] = {kind: {status: sum(item.get("evidence_kind") == kind and item["status"] == status
                for item in assessment["findings"]) for status in ("pass", "fail", "needs_review", "not_applicable")}
                for kind in assessment["evidence_status_counts"]}
    return combined, assessment, len(batches)


def _screen_candidate(cad, candidate, row, prepared, *, system_type, clearance, padding,
                      rule_profile, model_context, engineering, reverse_flow, min_slope):
    length, backtrack = row["length_mm"], row["axis_backtracking_mm"]
    try:
        layout = prepare_cad_layout(cad, candidate["id"], system_type=system_type, clearance=clearance,
            padding=padding, rule_profile=rule_profile, model_context=model_context, engineering=engineering,
            reverse_flow=reverse_flow, _prepared_geometry=prepared)
        row["start"], row["end"] = layout["pipe"]["start"], layout["pipe"]["end"]
        if system_type == "gravity":
            layout["rules"]["min_slope"] = min_slope
        row["endpoint_contact_count"] = len(layout["cad_context"]["endpoint_contacts"])
        row["obstacle_count"] = len(layout["obstacles"])
        row["fallback_box_count"] = layout["cad_context"]["counts"]["fallback_box_obstacles"]
        batching = prepared.get("screening_batches") is True
        shape_count = sum(max(1, len(obstacle.get("capsules", []))) for obstacle in layout["obstacles"]) if batching else len(layout["obstacles"])
        if (len(candidate["points"]) - 1) * shape_count > MAX_REVIEW_SEGMENT_OBSTACLE_PAIRS:
            row["review_message"] = "This route exceeds the drawing screening work limit. Select it for an individual check."
            return row
        if batching:
            checked, assessment, batch_count = _screen_layout_batches(layout, rule_profile=rule_profile)
            row["obstacle_batches_checked"] = batch_count
        else:
            checked = validate_layout_route(layout, layout["pipe"]["original_route"])
            assessment = assess_layout(layout) if rule_profile == "fmg-v3" else None
        row["geometry_checked"] = True
        row["findings"] = []
        route_points = layout["pipe"]["original_route"]
        obstacle_sources = {item["id"]: item for item in layout["cad_context"]["obstacle_sources"]}
        for issue in checked["issues"]:
            finding = {**issue, "evidence_kind": "computed_geometry", "evidence_completeness": "extracted_envelopes",
                "evidence_note": "Computed from the selected inferred centerline and supplied geometry. Complete source coverage and physical component identity remain unverified."}
            segment = finding.get("segment")
            if isinstance(segment, int) and not isinstance(segment, bool) and 0 <= segment < len(route_points) - 1:
                finding.update(segment_start=list(route_points[segment]), segment_end=list(route_points[segment + 1]))
            source = obstacle_sources.get(finding.get("obstacle"))
            if source:
                finding["obstacle_source"] = {key: deepcopy(source[key]) for key in ("source_handle", "instance_path") if key in source}
            row["findings"].append(finding)
        row["issue_count"] = len(checked["issues"])
        if assessment:
            row["rule_counts"] = assessment["counts"]
            row["rule_findings"] = assessment["findings"]
        failures = [item for item in (assessment or {}).get("findings", []) if item["status"] == "fail"]
        if checked["issues"] or failures:
            row["review_status"] = "attention"
            row["review_message"] = (f"{len(checked['issues'])} geometry flags; {len(failures)} failed rule screens. "
                f"{row['endpoint_contact_count']} endpoint contacts need connection review. Conservative geometry may flag intended contacts.")
        elif length < 2 * row["diameter_mm"]:
            row["review_status"] = "needs_review"
            row["review_message"] = "Short cylindrical component: confirm this is a reroutable pipe, not a required fitting or fastener."
        elif backtrack > 1.0:
            row["review_status"] = "opportunity"
            row["review_message"] = "Coordinate backtracking suggests a possible detour. Search must prove a feasible improvement within the approved spaces."
        else:
            row["review_status"] = "no_obvious_detour"
            row["review_message"] = "No coordinate backtracking or screened geometry issue. This does not establish an optimal route or full compliance."
    except (ValueError, KeyError) as error:
        row["review_status"] = "needs_review"
        row["review_message"] = str(error)
    return row


def review_drawing(cad, *, system_type="pressurized", clearance=50.8, padding=500,
                   rule_profile="fmg-v3", model_context=None, engineering=None,
                   reverse_flow=False, min_slope=0.01):
    """Return screening rows and explicit coverage; do not mutate imported CAD."""
    started = time.perf_counter()
    settings = _screen_settings(system_type=system_type, clearance=clearance, padding=padding,
        rule_profile=rule_profile, reverse_flow=reverse_flow, min_slope=min_slope)
    network, candidates = _review_inventory(cad)
    prepared = {"network": network, "solids": _solid_boxes(cad)}
    rows = []
    for kind, candidate in candidates[:MAX_REVIEW_ROUTES]:
        row = _screening_row(kind, candidate)
        rows.append(row)
        if time.perf_counter() - started > MAX_REVIEW_SECONDS:
            continue
        _screen_candidate(cad, candidate, row, prepared, model_context=model_context,
                          engineering=engineering, **settings)
    priority = {"attention": 0, "opportunity": 1, "no_obvious_detour": 2, "needs_review": 3, "not_reviewed": 4}
    rows.sort(key=lambda row: (priority[row["review_status"]], -row["member_count"], -row["axis_backtracking_mm"], -row["length_mm"], row["id"]))
    reviewed = sum(row["geometry_checked"] for row in rows)
    all_boxes, unread = prepared["solids"]
    limits = [
        "Screening covers inferred pipe geometry, not all ship systems or verified component identities.",
        "Intended endpoint connections are not automatically waived. Inspect the selected route and confirm its fixed connections.",
        "No obvious detour means no coordinate backtracking, not a proof of optimality. An opportunity is not a proven saving.",
        "Missing specifications, nominal sizes, required spaces and source evidence remain designer review items.",
        "This screen does not optimize routes. MARITIMEREVIEW selects one section; MARITIMEOPTIMIZE coordinates up to twelve explicitly selected sections. Neither certifies the entire system.",
        "Local search padding is not an approved ship space. Supply aligned model context to check permitted spaces.",
        "The screening time budget is checked between sections, not a hard deadline for drawing extraction or one complex section.",
    ]
    review = {
        "coverage": "all_supported_routes_screened" if reviewed == len(candidates) else "partial_supported_route_screening",
        "total_supported_routes": len(candidates), "listed_routes": len(rows), "reviewed_routes": reviewed,
        "unreviewed_routes": len(candidates) - reviewed, "omitted_from_list": max(0, len(candidates) - len(rows)),
        "counts": dict(Counter(row["review_status"] for row in rows)),
        "runtime_seconds": time.perf_counter() - started,
        "limits": {"maximum_listed_routes": MAX_REVIEW_ROUTES, "screening_budget_seconds": MAX_REVIEW_SECONDS,
                   "maximum_segment_obstacle_pairs_per_route": MAX_REVIEW_SEGMENT_OBSTACLE_PAIRS},
        "geometry_counts": {"source_solids_with_geometry": len(all_boxes), **unread},
        "complete_model_verified": False, "limitations": limits,
    }
    return {"candidates": rows, "review": review, "network_counts": network["counts"],
            "warnings": list(dict.fromkeys([*cad.get("warnings", []), *network["warnings"], *limits]))}


def inspect_drawing_snapshot(source, *, source_units, document, **settings):
    started = time.perf_counter()
    cad = inspect_cad(source, source_units=source_units)
    cad["autocad_context"] = {"api_version": API_VERSION, "document": deepcopy(document),
        "selected_handles": [], "source_snapshot_sha256": cad["source_sha256"], "scope": "drawing_review"}
    screened = review_drawing(cad, **settings)
    screened["review"]["total_runtime_seconds"] = time.perf_counter() - started
    return cad, {"api_version": API_VERSION, "source_sha256": cad["source_sha256"],
        "source_units": source_units, "units": "mm", **screened,
        "counts": {"drawing": cad.get("counts", {}), "network": screened["network_counts"],
                   "matched_candidates": len(screened["candidates"])}}


def write_drawing_review(summary, folder):
    """HTML and literal-cell XLSX share the same numbered route choices."""
    from .review_export import write_workbook
    review = summary["review"]
    rows = [["Number", "Source section", "Screen", "Finding", "Length mm", "Inferred OD mm", "Geometry flags", "Endpoint contacts", "Source handles", "Start XYZ mm", "End XYZ mm"]]
    for index, item in enumerate(summary["candidates"], 1):
        rows.append([index, item["label"], STATUS_LABELS[item["review_status"]], item["review_message"],
            item["length_mm"], item["diameter_mm"], item["issue_count"], item["endpoint_contact_count"],
            item["source_handles"], item["start"], item["end"]])
    findings = [["Route number", "Rule / geometry code", "Result", "Finding", "Source / measurement"]]
    for index, item in enumerate(summary["candidates"], 1):
        for finding in item.get("findings", []) + item.get("rule_findings", []):
            findings.append([index, finding.get("id", finding.get("code", "")), finding.get("status", "flag"),
                finding.get("message", ""), finding.get("measured", finding.get("obstacle", ""))])
    write_workbook([("Drawing review", rows), ("Findings", findings), ("Coverage", [["Item", "Value"],
        ["Source hash", summary["source_sha256"]], *[[key, value] for key, value in review.items()]])], folder / "route-review.xlsx")
    e = lambda value: html.escape(str(value))
    def display(value):
        if isinstance(value, float):
            return f"{value:,.1f}"
        if isinstance(value, list):
            return ', '.join(str(item) for item in value)
        return value
    table = ''.join('<tr>' + ''.join('<td>' + e(display(value)) + '</td>' for value in row[:9]) + '</tr>' for row in rows[1:])
    print_cards = ''.join('<article><h3>Route ' + str(index) + ' · ' + e(item['label']) + '</h3><p><strong>Source handles: '
        + e(display(item['source_handles'])) + '</strong></p><p>' + e(STATUS_LABELS[item['review_status']]) + ' · '
        + e(item['review_message']) + '</p><p>Length ' + e(display(item['length_mm'])) + ' mm · OD '
        + e(display(item['diameter_mm'])) + ' mm · Endpoint contacts ' + str(item['endpoint_contact_count'])
        + '</p><p>Start XYZ mm: ' + e(display(item['start'])) + '<br>End XYZ mm: ' + e(display(item['end'])) + '</p></article>'
        for index, item in enumerate(summary['candidates'], 1))
    (folder / "report.html").write_text('''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Drawing routing review</title><style>body{font:16px system-ui;margin:0;background:#eef3f7;color:#122b42}main{max-width:1280px;margin:auto;padding:32px}h1{font-size:36px}section{background:white;padding:24px;border-radius:12px;margin:20px 0}table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;vertical-align:top;border-bottom:1px solid #d3dde5;padding:12px}th{background:#143852;color:white}a{color:#006b79}.scroll{overflow:auto}.note{color:#455b6b}.print-cards{display:none}@media print{main{padding:0}body{background:white}section{padding:8px}.scroll{display:none}.print-cards{display:block}article{break-inside:avoid;border-bottom:1px solid #888;padding:8px 0}}</style>
<main><p>MARITIME / DRAWING REVIEW</p><h1>Choose the next pipe section to improve</h1><section><h2>Screening coverage</h2><p>'''
        + f"{review['reviewed_routes']} of {review['total_supported_routes']} supported inferred sections screened. {review['unreviewed_routes']} remain unreviewed."
        + '</p><p>Geometry flags include possible intended connections and conservative obstacle approximations. Review their source before treating them as violations.</p>'
        + '<p><a href="route-review.xlsx">Excel review</a> · <a href="result.json">Full screening result</a></p></section>'
        + '<section><h2>Source sections</h2><p>Use the same numbers in AutoCAD MARITIMEREVIEW or MARITIMEOPTIMIZE. Selected sections are previewed before route search.</p><div class="scroll"><table><thead><tr>'
        + ''.join('<th>' + e(value) + '</th>' for value in rows[0][:9]) + '</tr></thead><tbody>' + table + '</tbody></table></div><div class="print-cards">' + print_cards + '</div></section>'
        + '<section><h2>Designer attention</h2><ul>' + ''.join('<li>' + e(item) + '</li>' for item in summary["warnings"])
        + '</ul><p class="note">Snapshot: ' + e(summary["source_sha256"]) + '</p></section></main></html>', encoding="utf-8")
