"""Combined, traceable review exports for coordinated CAD sections."""
from __future__ import annotations

import html
import json
from pathlib import Path

import ezdxf
import plotly.graph_objects as go

from .review_export import write_workbook


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
    overview = [["Section", "Source handles", "Outcome", "Reason", "Candidate ID", "OD mm", "Start XYZ mm", "End XYZ mm"]]
    parts = [["Section", "Source handles", "Part", "Geometry", "Length mm", "Radius mm", "Angle deg", "Start XYZ mm", "End XYZ mm", "Part status"]]
    checks = [["Section", "Assessment", "Rule", "Status", "Message", "Measurements"]]
    coverage = [["Item", "Value"], ["Scope", "Coordinated selected sections; not a complete system redesign"],
                ["Full system verified", False], ["Source", summary.get("source", {})],
                ["Coverage", summary.get("coverage", {})], ["Counts", summary.get("counts", {})],
                ["Comparable metrics only", summary.get("metrics", {})],
                ["Coordinate units", "mm; source drawing global frame"],
                ["Parts", "Geometric pieces, not an orderable fitting catalog or fabrication approval"]]
    cards = []
    escape = lambda value: html.escape(str(value))
    seen_context = set()
    for row in summary.get("routes", []):
        index = row["index"]
        handles = ", ".join(row.get("source_handles", []))
        layout, result = row.get("layout") or {}, row.get("result") or {}
        pipe = layout.get("pipe", {})
        overview.append([index, handles, row["status"], row.get("message", ""), row["candidate_id"],
                         pipe.get("diameter", ""), pipe.get("start", []), pipe.get("end", [])])
        assembly = result.get("assembly") or {}
        accepted = accepted_route(row)
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
                    name=f"Section {index} {role.lower()}", legendgroup=f"{index}-{role}", showlegend=mesh_index == 0,
                    hovertemplate=f"Section {index} {role.lower()}<br>Source: {escape(handles)}<extra></extra>"))
                for face in faces:
                    entity = space.add_3dface([vertices[i] for i in face], dxfattribs={"layer": layer})
                    entity.set_xdata("MARITIME_REVIEW", [(1000, row["candidate_id"][:255]), (1000, handles[:255]), (1000, role)])
        if result.get("original"):
            space.add_polyline3d(result["original"]["points"], dxfattribs={"layer": f"SECTION_{index:02d}_ORIGINAL"})
        if accepted:
            space.add_polyline3d(assembly["centerline_points"], dxfattribs={"layer": f"SECTION_{index:02d}_PROPOSAL"})
            for part in assembly.get("components", []):
                parts.append([index, handles, part.get("id", ""), part.get("kind", ""), part.get("length_mm", ""),
                              part.get("radius_mm", ""), part.get("angle_deg", ""), part.get("start", []), part.get("end", []), "geometric review"])
                midpoint = [(a+b)/2 for a, b in zip(part["start"], part["end"])]
                space.add_text(f"S{index} {part['id']}: {part.get('length_mm', 0):.1f} mm", dxfattribs={
                    "layer": f"SECTION_{index:02d}_PROPOSAL", "insert": midpoint, "height": max(2, pipe.get("diameter", 10)*.2)})
        for name, assessment in [("Original rules", result.get("challenge_review", {}).get("original") or {}),
                                  ("Proposal rules", result.get("challenge_review", {}).get("candidate") or {}),
                                  ("Generated pieces", assembly.get("manufacturing_assessment") or {})]:
            for item in assessment.get("findings", []):
                checks.append([index, name, item.get("rule_id", item.get("id", "")), item.get("status", ""), item.get("message", ""), item.get("measured", {})])
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
        cards.append(f"<article><h2>Section {index} · {escape(row['status'].replace('_', ' '))}</h2><p><strong>Source: {escape(handles)}</strong></p>"
                     f"<p>{escape(row.get('label', row['candidate_id']))}</p><p>{escape(row.get('message', ''))}</p>{detail}</article>")
    figure.update_layout(template="plotly_dark", paper_bgcolor="#101e2d", margin=dict(l=0,r=0,t=20,b=0),
                         scene=dict(aspectmode="data", xaxis_title="X (mm)", yaxis_title="Y (mm)", zaxis_title="Z (mm)"), height=650)
    chart = figure.to_html(full_html=False, include_plotlyjs=True, div_id="system-review")
    coverage.extend(["Limitation", text] for text in summary.get("limitations", []))
    write_workbook([("Overview", overview), ("Geometric pieces", parts), ("Rule findings", checks), ("Coverage", coverage)], folder / "route-review.xlsx")
    space.add_text("COORDINATED SECTION REVIEW. Units mm. Source CAD unchanged. Not a complete system or fabricated pipe model.", dxfattribs={"height": 20})
    drawing.saveas(folder / "route-centerlines.dxf")
    counts = " · ".join(f"{value} {key.replace('_', ' ')}" for key, value in summary.get("counts", {}).items())
    covered = summary.get("coverage", {})
    coverage_text = (f"{covered.get('requested_sections', 0)} sections selected; {covered.get('processed_sections', 0)} processed; "
                     f"{covered.get('accepted_sections', 0)} accepted under the implemented checks. The full drawing is not verified.")
    page = f'''<!doctype html><html><head><meta charset="utf-8"><title>Coordinated section review</title>
<style>body{{margin:0;background:#101e2d;color:#edf4ff;font:17px system-ui}}main{{max-width:1150px;margin:auto;padding:32px}}h1{{font-size:36px}}article{{background:#1b3044;padding:20px;margin:18px 0;border-radius:12px;break-inside:avoid}}a{{color:#79e4f2}}p,li{{line-height:1.5}}.badge{{font-weight:bold;color:#79e4f2}}@media print{{body{{background:white;color:black}}main{{padding:0}}article{{background:white;border:1px solid #777}}#system-review{{display:none}}}}</style></head>
<body><main><p class="badge">CAD-DERIVED · COORDINATED SECTIONS</p><h1>Review several changes together</h1>
<p>{escape(counts)}</p><p>Cyan: original reference. Green: accepted geometric proposal. Grey: obstacle approximations.
Each section retains its endpoints and pipe diameter. Source CAD is unchanged. Proposed pieces need designer review.</p>
<p>This is a bounded search over selected sections, not whole-system optimization or full specification approval.</p>
<p><a href="route-review.xlsx">Excel review and global coordinates</a> · <a href="route-centerlines.dxf">3D review DXF</a> · <a href="result.json">Full result</a></p>
{chart}{''.join(cards)}<h2>Coverage and remaining decisions</h2><p>{escape(coverage_text)}</p>
<ul>{''.join('<li>'+escape(item)+'</li>' for item in summary.get('limitations', []))}</ul></main></body></html>'''
    (folder / "report.html").write_text(page, encoding="utf-8")
