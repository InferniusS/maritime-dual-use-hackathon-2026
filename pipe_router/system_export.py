"""Combined, traceable review exports for coordinated CAD sections."""
from __future__ import annotations

import html
import json
import math
from pathlib import Path

import ezdxf
import plotly.graph_objects as go

from .review_export import evidence_labels, write_workbook


def geometry_schedule_issues(row):
    """Check export identities and measurements without reclassifying routing safety."""
    assembly = (row.get("result") or {}).get("assembly") or {}
    parts, meshes = assembly.get("components", []), assembly.get("meshes", [])
    if not parts or not meshes:
        return ["Accepted component or mesh records are missing; regenerate the result."]
    issues = []
    part_ids, mesh_ids = [p.get("id") for p in parts], [m.get("id") for m in meshes]
    if any(not isinstance(identifier, str) or not identifier for identifier in part_ids) or len(set(part_ids)) != len(part_ids):
        issues.append("Component identifiers are missing or repeated.")
    if mesh_ids != part_ids:
        issues.append("Component and mesh identifiers do not match in order.")
    try:
        pipe = row["layout"]["pipe"]
        same_point = lambda a, b: len(a) == len(b) == 3 and all(math.isfinite(float(v)) for v in [*a, *b]) and math.dist(a, b) <= 1e-5
        if not same_point(parts[0]["start"], pipe["start"]) or not same_point(parts[-1]["end"], pipe["end"]):
            issues.append("Component endpoints differ from the selected source endpoints.")
        combined = []
        for previous, part in zip([None, *parts[:-1]], parts):
            if previous and not same_point(previous["end"], part["start"]):
                issues.append("The component schedule contains a disconnected interface.")
            if not math.isclose(part["outer_diameter_mm"], pipe["diameter"], rel_tol=1e-9, abs_tol=1e-5):
                issues.append("A component outer diameter differs from the selected source.")
            points = part["centerline_points"]
            if not points or not same_point(points[0], part["start"]) or not same_point(points[-1], part["end"]):
                issues.append("A component centerline disagrees with its coordinate schedule.")
            expected = (math.dist(part["start"], part["end"]) if part["kind"] == "straight" else
                        part["radius_mm"] * math.radians(part["angle_deg"]))
            if not math.isfinite(expected) or expected < 0 or not math.isclose(part["length_mm"], expected, rel_tol=1e-8, abs_tol=1e-5):
                issues.append("A component length disagrees with its analytic geometry.")
            for point in points:
                if not combined or point != combined[-1]:
                    combined.append(point)
        for part, mesh in zip(parts, meshes):
            # The generated capped tube stores its start/end center as the last
            # two vertices. Bind those drawing coordinates to the schedule too.
            vertices = mesh.get("vertices", [])
            if len(vertices) < 2 or not same_point(vertices[-2], part["start"]) or not same_point(vertices[-1], part["end"]):
                issues.append("A mesh endpoint differs from its component coordinate schedule.")
        actual = assembly["centerline_points"]
        if len(combined) != len(actual) or not all(same_point(a, b) for a, b in zip(combined, actual)):
            issues.append("The exported rounded centerline differs from the component schedule.")
        if not math.isclose(sum(part["length_mm"] for part in parts), assembly["metrics"]["centerline_length_mm"], rel_tol=1e-8, abs_tol=1e-5):
            issues.append("The component length total differs from the reported rounded length.")
    except (KeyError, TypeError, ValueError, OverflowError):
        issues.append("A required coordinate or geometric measurement is missing or invalid.")
    return list(dict.fromkeys(issues))


def _source_number(row):
    # A batch item index is not a scan number. Never reconstruct this from order.
    return row.get("source_scan_number", "")


def _next_action(row, accepted):
    if accepted:
        return ("Retain this geometry; no better accepted route was found in this search. This is not a proof of global optimality."
                if row.get("status") == "unchanged" else
                "Inspect the proposal, connection portions and tight passages; check dimensions and the matching component schedule before saving the review.")
    review = (row.get("result") or {}).get("search_region_review") or {}
    if review.get("next_action"):
        return review["next_action"]
    if row.get("status") == "not_processed":
        return "Run this source section in a smaller batch; it has not been checked."
    if row.get("status") == "needs_review":
        return "Resolve the stated source geometry, system or connection evidence before rerunning this section."
    return "Inspect the recorded findings and endpoint contacts. A bounded search failure does not establish that the physical problem is impossible."


def _search_settings(row):
    layout, result = row.get("layout") or {}, row.get("result") or {}
    review = result.get("search_region_review") or {}
    context, rules = layout.get("cad_context", {}), layout.get("rules", {})
    recorded = lambda *values: next((value for value in values if value is not None), "Not recorded")
    return {"Padding mm": recorded(review.get("padding_mm"), context.get("padding_mm")),
            "Required clearance mm": recorded(review.get("clearance_mm"), rules.get("clearance")),
            "Outer diameter mm": recorded(review.get("pipe_outer_diameter_mm"), layout.get("pipe", {}).get("diameter")),
            "Grid step mm": recorded(review.get("grid_step_mm"), rules.get("grid_step")),
            "Bend centerline radius mm": recorded(review.get("bend_radius_mm"), (result.get("assembly") or {}).get("bend_radius_mm"), layout.get("proposal", {}).get("bend_radius_mm")),
            "Search bounds XYZ mm": review.get("bounds_mm", layout.get("bounds", {})),
            "Search diagnosis": review.get("boundary_review", "Not recorded"),
            "Boundary evidence": review.get("message", "Not recorded"),
            "Represented barriers": review.get("barriers", [])}


def accepted_route(row):
    result = row.get("result") or {}
    assembly = result.get("assembly") or {}
    coordination = row.get("coordination") or {}
    return (row.get("status") in ("proposed", "unchanged")
            and all(coordination.get(key) is True for key in ("accepted", "original_geometry_revalidated", "pair_clearance_revalidated"))
            and (result.get("candidate") or {}).get("valid") is True and assembly.get("status") == "ready"
            and assembly.get("manufacturing_assessment", {}).get("counts", {}).get("fail", 0) == 0)


def write_system_review(summary, folder):
    """Export only accepted proposals; every requested outcome remains visible."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "result.json").write_text(json.dumps(summary, indent=2, allow_nan=False), encoding="utf-8")
    figure = go.Figure()
    drawing = ezdxf.new("R2018")
    drawing.units = ezdxf.units.MM
    space = drawing.modelspace()
    drawing.appids.add("MARITIME_REVIEW")
    drawing.layers.add("CONTEXT_APPROXIMATION", color=8)
    overview = [["Batch item", "Source handles", "Outcome", "Reason", "Candidate ID", "OD mm", "Start XYZ mm", "End XYZ mm",
                 "Source scan number", "Next action", "Accepted geometry exported", "Export consistency issues", "Source SHA256", "Export status"]]
    parts = [["Batch item", "Source scan number", "Candidate ID", "Source handles", "Source SHA256", "Part", "Geometry",
              "Start X mm", "Start Y mm", "Start Z mm", "End X mm", "End Y mm", "End Z mm", "Outer diameter mm",
              "Centerline length mm", "Straight cut length mm", "Bend arc length mm", "Radius mm", "Angle deg",
              "Bend center X mm", "Bend center Y mm", "Bend center Z mm", "Bend normal X", "Bend normal Y", "Bend normal Z",
              "Arc chord error mm", "Part status"]]
    checks = [["Batch item", "Assessment", "Rule", "Status", "Message", "Measurements", "Evidence origin", "Evidence completeness", "Evidence note"]]
    settings_sheet = [["Batch item", "Candidate ID", "Setting", "Value"]]
    audit_sheet = [["Batch item", "Candidate ID", "Exported", "Piece count", "Mesh count", "Scheduled length mm", "Reported rounded length mm", "Consistency issues"]]
    contacts_sheet = [["Batch item", "Contact number", "Endpoint", "Source handle", "Instance path", "Obstacle ID",
                       "Contact kind", "Evidence", "Remaining pipe clearance mm", "Fixed-path preview",
                       "Reviewer selected", "Connection identity verified"]]
    fixed_sheet = [["Batch item", "Candidate ID", "Endpoint", "Status", "Fixed endpoint X mm", "Fixed endpoint Y mm", "Fixed endpoint Z mm",
                    "Movable anchor X mm", "Movable anchor Y mm", "Movable anchor Z mm", "Fixed stub length mm",
                    "Direction into movable section", "Direction basis", "Selected obstacle IDs", "Remaining blockers", "Fixed stub points", "Connection identity verified"]]
    coverage = [["Item", "Value"], ["Scope", "Coordinated selected sections; not a complete system redesign"],
                ["Full system verified", False], ["Source", summary.get("source", {})],
                ["Coverage", summary.get("coverage", {})], ["Counts", summary.get("counts", {})],
                ["Comparable metrics only", summary.get("metrics", {})],
                ["Coordinate units", "mm; source drawing global frame"],
                ["Batch item versus source scan number", "Batch item is the order in this selected batch, not a MARITIMESCAN route number. Match Candidate ID and source handles; a source scan number is shown only when recorded."],
                ["Parts", "Geometric pieces, not an orderable fitting catalog or fabrication approval"]]
    cards = []
    escape = lambda value: html.escape(str(value))
    seen_context = set()
    for row in summary.get("routes", []):
        index = row["index"]
        handles = ", ".join(row.get("source_handles", []))
        layout, result = row.get("layout") or {}, row.get("result") or {}
        pipe = layout.get("pipe", {})
        assembly = result.get("assembly") or {}
        eligible = accepted_route(row)
        export_issues = geometry_schedule_issues(row) if eligible else []
        accepted = eligible and not export_issues
        export_status = "Accepted geometry exported" if accepted else "No accepted geometry exported"
        if export_issues:
            export_status = "Export withheld: geometry consistency needs review"
        action = ("Regenerate this result: its component schedule and drawing evidence do not agree. " + "; ".join(export_issues)
                  if export_issues else _next_action(row, accepted))
        source_hash = layout.get("cad_context", {}).get("source_sha256", summary.get("source", {}).get("sha256", ""))
        source_number = _source_number(row)
        overview.append([index, handles, row["status"], row.get("message", ""), row["candidate_id"],
                         pipe.get("diameter", ""), pipe.get("start", []), pipe.get("end", []), source_number, action,
                         accepted, export_issues, source_hash, export_status])
        settings = _search_settings(row)
        settings_sheet.extend([index, row["candidate_id"], key, value] for key, value in settings.items())
        if accepted:
            audit_sheet.append([index, row["candidate_id"], True, len(assembly["components"]), len(assembly["meshes"]),
                sum(part["length_mm"] for part in assembly["components"]), assembly["metrics"]["centerline_length_mm"], []])
        else:
            audit_sheet.append([index, row["candidate_id"], False, 0, 0, "", "", export_issues or ["No accepted coordinated proposal"]])

        def identify(entity, role, component_id=""):
            entity.set_xdata("MARITIME_REVIEW", [(1000, row["candidate_id"][:255]), (1000, handles[:255]), (1000, role),
                (1000, f"batch_item={index}"), (1000, f"source_scan_number={source_number}"), (1000, str(component_id)[:255]),
                (1000, str(source_hash)[:255])])
        groups = [("ORIGINAL", assembly.get("reference_meshes", []), 4, "#20c5e1")]
        if accepted:
            groups.append(("PROPOSAL", assembly.get("meshes", []), 3, "#65e572"))
        for role, meshes, color_index, color in groups:
            layer = f"SECTION_{index:02d}_{role}"
            drawing.layers.add(layer, color=color_index)
            for mesh_index, mesh in enumerate(meshes):
                vertices, faces = mesh["vertices"], mesh["faces"]
                triangles = [triangle for face in faces for triangle in ([face] if len(face) == 3 else [[face[0], face[j], face[j+1]] for j in range(1, len(face)-1)])]
                figure.add_trace(go.Mesh3d(x=[p[0] for p in vertices], y=[p[1] for p in vertices], z=[p[2] for p in vertices],
                    i=[f[0] for f in triangles], j=[f[1] for f in triangles], k=[f[2] for f in triangles],
                    color=color, opacity=.35 if role == "ORIGINAL" else .95,
                    name=f"Batch item {index} {role.lower()}", legendgroup=f"{index}-{role}", showlegend=mesh_index == 0,
                    hovertemplate=f"Batch item {index} {role.lower()}<br>Source: {escape(handles)}<br>Component: {escape(mesh.get('id', ''))}<extra></extra>"))
                for face in faces:
                    entity = space.add_3dface([vertices[i] for i in face], dxfattribs={"layer": layer})
                    identify(entity, role, mesh.get("id", ""))
        if result.get("original"):
            space.add_polyline3d(result["original"]["points"], dxfattribs={"layer": f"SECTION_{index:02d}_ORIGINAL"})
        if accepted:
            identify(space.add_polyline3d(assembly["centerline_points"], dxfattribs={"layer": f"SECTION_{index:02d}_PROPOSAL"}), "PROPOSAL_CENTERLINE")
            for part in assembly.get("components", []):
                straight = part.get("kind") == "straight"
                parts.append([index, source_number, row["candidate_id"], handles, source_hash, part["id"], part["kind"],
                    *part["start"], *part["end"], part["outer_diameter_mm"], part["length_mm"],
                    part["length_mm"] if straight else "", part["length_mm"] if not straight else "",
                    part.get("radius_mm", ""), part.get("angle_deg", ""), *part.get("center", ["", "", ""]),
                    *part.get("normal", ["", "", ""]), part.get("chord_error_mm", 0),
                    "Retained geometric review; catalog unverified" if row["status"] == "unchanged" else "Proposed geometric review; catalog unverified"])
                midpoint = [(a+b)/2 for a, b in zip(part["start"], part["end"])]
                label = f"B{index} {part['id']}: L {part.get('length_mm', 0):.1f} mm; OD {pipe.get('diameter', 0):.1f} mm"
                if part.get('kind') == 'bend':
                    label += f"; R {part.get('radius_mm', 0):.1f} mm; {part.get('angle_deg', 0):.1f} deg"
                identify(space.add_text(label, dxfattribs={
                    "layer": f"SECTION_{index:02d}_PROPOSAL", "insert": midpoint, "height": max(2, pipe.get("diameter", 10)*.2)}), "PROPOSAL_DIMENSION", part["id"])
        for name, assessment in [("Original rules", result.get("challenge_review", {}).get("original") or {}),
                                  ("Proposal rules", result.get("challenge_review", {}).get("candidate") or {}),
                                  ("Generated pieces", assembly.get("manufacturing_assessment") or {})]:
            for item in assessment.get("findings", []):
                checks.append([index, name, item.get("rule_id", item.get("id", "")), item.get("status", ""), item.get("message", ""), item.get("measured", {}), *evidence_labels(item)])
        # Display each conservative box once. Pipe capsules are represented by
        # their axes, explicitly labelled; neither is certified source geometry.
        for obstacle in layout.get("obstacles", []):
            identity = json.dumps(obstacle, sort_keys=True)
            if identity in seen_context or str(obstacle.get("id", "")).startswith("system-proposal-"):
                continue
            seen_context.add(identity)
            segments = []
            if obstacle.get("capsules"):
                segments = [[item["start"], item["end"]] for item in obstacle["capsules"]]
            elif "min" in obstacle and "max" in obstacle:
                a, b = obstacle["min"], obstacle["max"]
                vertices = [[a[0], a[1], a[2]], [b[0], a[1], a[2]], [b[0], b[1], a[2]], [a[0], b[1], a[2]],
                            [a[0], a[1], b[2]], [b[0], a[1], b[2]], [b[0], b[1], b[2]], [a[0], b[1], b[2]]]
                segments = [[vertices[i] for i in indices] for indices in ((0,1,2,3,0), (4,5,6,7,4), (0,4), (1,5), (2,6), (3,7))]
            points = [point for segment in segments for point in [*segment, [None, None, None]]]
            if points:
                for segment in segments:
                    space.add_polyline3d(segment, dxfattribs={"layer": "CONTEXT_APPROXIMATION"})
                figure.add_trace(go.Scatter3d(x=[p[0] for p in points], y=[p[1] for p in points], z=[p[2] for p in points], mode="lines",
                    line=dict(color="#8396a7", width=2), opacity=.25, showlegend=False, name="Obstacle approximation",
                    hovertemplate="Obstacle approximation / capsule axis<extra></extra>"))
        metrics = assembly.get("metrics") or {}
        baseline = assembly.get("baseline_metrics") or {}
        detail = ""
        if accepted:
            detail = f"<p>Proposed rounded length: {metrics.get('centerline_length_mm', 0):,.1f} mm · bends: {metrics.get('bend_count', 0)}.</p>"
            if baseline:
                detail = f"<p>Rounded length: {baseline.get('centerline_length_mm', 0):,.1f} → {metrics.get('centerline_length_mm', 0):,.1f} mm. " + f"Bends: {baseline.get('bend_count', 0)} → {metrics.get('bend_count', 0)}.</p>"
            else:
                detail += "<p>Comparable rounded original unavailable: no physical length saving is claimed.</p>"
        contacts = layout.get("cad_context", {}).get("endpoint_contacts", [])
        declared = layout.get("protected_endpoints", [])
        contact_rows = []
        for contact_number, contact in enumerate(contacts, 1):
            selected = any(stub.get("endpoint") == contact.get("endpoint") and
                           contact.get("obstacle_id") in stub.get("obstacle_ids", []) for stub in declared)
            evidence = contact.get("contact_summary", contact.get("scope", "No geometric classification available"))
            status = "Reviewer selected; identity not automatically verified" if selected else "Not approved"
            contacts_sheet.append([index, contact_number, contact.get("endpoint", ""), contact.get("source_handle", ""),
                contact.get("instance_path", []), contact.get("obstacle_id", ""), contact.get("contact_kind", "unclassified"),
                evidence, contact.get("remaining_pipe_clearance_mm", ""), contact.get("stub_preview", {}), selected, False])
            contact_rows.append("<tr>" + "".join("<td>" + escape(value) + "</td>" for value in
                (contact_number, contact.get("endpoint", ""), contact.get("source_handle", ""), evidence, status)) + "</tr>")
        if contact_rows:
            detail += ("<h3>Endpoint contact evidence</h3><p>Clearance-only contact does not prove a physical joint. "
                       "Box and capsule approximations do not establish component identity. Selecting a contact is a reviewer declaration.</p>"
                       "<div class='table-scroll'><table><thead><tr><th>Contact</th><th>End</th><th>Source</th><th>Evidence</th><th>Decision</th></tr></thead><tbody>" +
                       "".join(contact_rows) + "</tbody></table></div>")
        fixed_review = result.get("connection_selection_review") or {}
        fixed_html = ""
        if fixed_review:
            fixed_rows = []
            for endpoint in fixed_review.get("endpoints", []):
                blockers = endpoint.get("blockers", [])
                fixed_sheet.append([index, row["candidate_id"], endpoint.get("endpoint", ""), endpoint.get("status", ""),
                    *endpoint.get("fixed_endpoint", ["", "", ""]), *endpoint.get("movable_anchor", ["", "", ""]),
                    endpoint.get("fixed_stub_length_mm", ""), endpoint.get("departure_direction_into_movable_section", []),
                    endpoint.get("direction_basis", ""), endpoint.get("selected_obstacle_ids", []), blockers,
                    endpoint.get("fixed_stub_points", []), False])
                blocker_text = "; ".join(str(blocker.get("source_handle", blocker.get("obstacle_id", ""))) + " (" +
                    ", ".join(scope.replace("_", " ") for scope in blocker.get("scopes", [])) + ")" for blocker in blockers)
                fixed_rows.append("<tr>" + "".join("<td>" + escape(value) + "</td>" for value in
                    (endpoint.get("endpoint", ""), endpoint.get("fixed_stub_length_mm", 0),
                     endpoint.get("movable_anchor", []), blocker_text or "None under this check")) + "</tr>")
            fixed_html = ("<h3>Fixed connection check</h3><p>" + escape(fixed_review.get("message", "")) +
                          "</p><p>These are supplied geometry envelopes; connection identity is not automatically verified.</p>"
                          "<div class='table-scroll'><table><thead><tr><th>End</th><th>Unchanged stub mm</th><th>Movable anchor XYZ mm</th>"
                          "<th>Remaining source conflicts</th></tr></thead><tbody>" + "".join(fixed_rows) + "</tbody></table></div>")
        source_label = f"Source scan number: {source_number} (client cross-reference). " if source_number != "" else "Source scan number was not recorded. "
        settings_html = "".join("<tr><th>" + escape(key) + "</th><td>" + escape(json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value) + "</td></tr>"
                                for key, value in settings.items())
        cards.append(f"<article><h2>Batch item {index} · {escape(row['status'].replace('_', ' '))}</h2><p><strong>Source: {escape(handles)}</strong></p>"
                     f"<p>{escape(source_label)}Candidate ID: {escape(row['candidate_id'])}</p>"
                     f"<p>{escape(row.get('label', row['candidate_id']))}</p><p>{escape(row.get('message', ''))}</p>"
                     f"<p><strong>{escape(export_status)}</strong></p><p><strong>Next action:</strong> {escape(action)}</p>{fixed_html}{detail}"
                     f"<details><summary>Active search settings and evidence</summary><p>Padding expands the search region. It does not lower clearance or establish ship containment.</p><table>{settings_html}</table></details></article>")
    figure.update_layout(template="plotly_dark", paper_bgcolor="#101e2d", margin=dict(l=0,r=0,t=20,b=0),
                         scene=dict(aspectmode="data", xaxis_title="X (mm)", yaxis_title="Y (mm)", zaxis_title="Z (mm)"), height=650)
    chart = figure.to_html(full_html=False, include_plotlyjs=True, div_id="system-review")
    coverage.extend(["Limitation", text] for text in summary.get("limitations", []))
    write_workbook([("Overview", overview), ("Geometric pieces", parts), ("Rule findings", checks), ("Endpoint contacts", contacts_sheet),
                    ("Fixed connections", fixed_sheet), ("Search settings", settings_sheet), ("Export reconciliation", audit_sheet), ("Coverage", coverage)], folder / "route-review.xlsx")
    space.add_text("COORDINATED SECTION REVIEW. Units mm. Source CAD unchanged. Not a complete system or fabricated pipe model.", dxfattribs={"height": 20})
    drawing.saveas(folder / "route-centerlines.dxf")
    counts = " · ".join(f"{value} {key.replace('_', ' ')}" for key, value in summary.get("counts", {}).items())
    covered = summary.get("coverage", {})
    coverage_text = (f"{covered.get('requested_sections', 0)} sections selected; {covered.get('processed_sections', 0)} processed; "
                     f"{covered.get('accepted_sections', 0)} accepted under the implemented checks. The full drawing is not verified.")
    page = f'''<!doctype html><html><head><meta charset="utf-8"><title>Coordinated section review</title>
<style>body{{margin:0;background:#101e2d;color:#edf4ff;font:17px system-ui}}main{{max-width:1150px;margin:auto;padding:32px}}h1{{font-size:36px}}article{{background:#1b3044;padding:20px;margin:18px 0;border-radius:12px;break-inside:avoid}}a{{color:#79e4f2}}p,li{{line-height:1.5}}.badge{{font-weight:bold;color:#79e4f2}}.table-scroll{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-size:15px}}td,th{{padding:8px;border:1px solid #64778b;text-align:left;vertical-align:top}}@media print{{body{{background:white;color:black}}main{{padding:0}}article{{background:white;border:1px solid #777}}#system-review{{display:none}}}}</style></head>
<body><main><p class="badge">CAD-DERIVED · COORDINATED SECTIONS</p><h1>Review several changes together</h1>
<p>{escape(counts)}</p><p>Cyan: original reference. Green: accepted geometric proposal. Grey: obstacle approximations.
Each section retains its endpoints and pipe diameter. Source CAD is unchanged. Proposed pieces need designer review.</p>
<p>This is a bounded search over selected sections, not whole-system optimization or full specification approval.</p>
<p>Batch item numbers are selection order in this report, not MARITIMESCAN route numbers. Use the recorded source scan number, Candidate ID and source handles to find the same section.</p>
<p><a href="route-review.xlsx">Excel review and global coordinates</a> · <a href="route-centerlines.dxf">3D review DXF</a> · <a href="result.json">Full result</a></p>
{chart}{''.join(cards)}<h2>Coverage and remaining decisions</h2><p>{escape(coverage_text)}</p>
<ul>{''.join('<li>'+escape(item)+'</li>' for item in summary.get('limitations', []))}</ul></main></body></html>'''
    (folder / "report.html").write_text(page, encoding="utf-8")
