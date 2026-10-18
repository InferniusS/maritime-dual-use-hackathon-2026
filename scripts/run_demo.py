"""Run a single-pipe workflow and write an offline review report."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import html
import json
import math
from pathlib import Path
import platform
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ezdxf
from ezdxf import units
import plotly.graph_objects as go

from pipe_router.workflow import run_workflow
from pipe_router.review_export import assembly_panel, challenge_panel, write_review_xlsx


def display_layout(layout, result):
    """Use the normalized geometry so plots and CAD exports share result units."""
    normalized = result.get("normalized_layout", layout)
    if normalized.get("units") != "mm":
        raise ValueError("Report geometry must be normalized to millimeters before rendering")
    return normalized


def box_vertices(box):
    low, high = box["min"], box["max"]
    return [
        [low[0], low[1], low[2]], [high[0], low[1], low[2]],
        [high[0], high[1], low[2]], [low[0], high[1], low[2]],
        [low[0], low[1], high[2]], [high[0], low[1], high[2]],
        [high[0], high[1], high[2]], [low[0], high[1], high[2]],
    ]


def add_box(figure, box, name, color, opacity, *, showlegend=True, legendgroup=None, hover=None):
    points = box_vertices(box)
    triangles = [(0, 1, 2), (0, 2, 3), (4, 6, 5), (4, 7, 6),
                 (0, 4, 5), (0, 5, 1), (1, 5, 6), (1, 6, 2),
                 (2, 6, 7), (2, 7, 3), (3, 7, 4), (3, 4, 0)]
    figure.add_trace(go.Mesh3d(
        x=[p[0] for p in points], y=[p[1] for p in points], z=[p[2] for p in points],
        i=[t[0] for t in triangles], j=[t[1] for t in triangles], k=[t[2] for t in triangles],
        name=name, color=color, opacity=opacity, flatshading=True, showlegend=showlegend,
        legendgroup=legendgroup, meta={"obstacle_id": box["id"]},
        hovertemplate=(hover or name) + "<extra></extra>",
    ))


def clip_mesh(vertices, triangles, low, high):
    """Clip display triangles to analytic bounds without inventing curved caps."""
    result_vertices, result_triangles = [], []
    for triangle in triangles:
        polygon = [vertices[index] for index in triangle]
        for axis in range(3):
            for boundary, direction in ((low[axis], 1), (high[axis], -1)):
                if not polygon:
                    break
                clipped = []
                previous = polygon[-1]
                previous_inside = direction*(previous[axis]-boundary) >= 0
                for current in polygon:
                    current_inside = direction*(current[axis]-boundary) >= 0
                    if current_inside != previous_inside:
                        fraction = (boundary-previous[axis])/(current[axis]-previous[axis])
                        intersection = [a + fraction*(b-a) for a, b in zip(previous, current)]
                        intersection[axis] = boundary
                        clipped.append(intersection)
                    if current_inside:
                        clipped.append(current)
                    previous, previous_inside = current, current_inside
                polygon = clipped
        if len(polygon) >= 3:
            offset = len(result_vertices)
            result_vertices.extend(polygon)
            result_triangles.extend((offset, offset+i, offset+i+1) for i in range(1, len(polygon)-1))
    return result_vertices, result_triangles


def capsule_mesh(capsules, sides=12, hemisphere_steps=4):
    """Tessellate the supplied envelopes for display, not for collision checks."""
    vertices, triangles = [], []
    def cross(a, b):
        return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]
    for capsule in capsules:
        start, end, radius = capsule["start"], capsule["end"], capsule["radius"]
        delta = [b-a for a, b in zip(start, end)]
        length = math.sqrt(sum(v*v for v in delta))
        axis = [v/length for v in delta] if length else [0, 0, 1]
        helper = [1, 0, 0] if abs(axis[0]) < .9 else [0, 1, 0]
        u = cross(axis, helper)
        norm = math.sqrt(sum(v*v for v in u))
        u = [v/norm for v in u]
        v = cross(axis, u)
        rings = []
        for center, first in ((start, -math.pi/2), (end, 0)):
            for step in range(hemisphere_steps + 1):
                angle = first + step * math.pi / (2*hemisphere_steps)
                axial, radial = radius*math.sin(angle), radius*math.cos(angle)
                rings.append([[center[k] + axial*axis[k] + radial*(u[k]*math.cos(math.tau*j/sides) + v[k]*math.sin(math.tau*j/sides))
                               for k in range(3)] for j in range(sides)])
        capsule_vertices = [point for ring in rings for point in ring]
        capsule_triangles = []
        for row in range(len(rings)-1):
            for j in range(sides):
                a, b = row*sides+j, row*sides+(j+1)%sides
                capsule_triangles.extend(((a, b, a+sides), (b, b+sides, a+sides)))
        if "clip_min" in capsule:
            capsule_vertices, capsule_triangles = clip_mesh(
                capsule_vertices, capsule_triangles, capsule["clip_min"], capsule["clip_max"])
        offset = len(vertices)
        vertices.extend(capsule_vertices)
        triangles.extend(tuple(offset+index for index in triangle) for triangle in capsule_triangles)
    return vertices, triangles


def geometry_coverage(layout):
    obstacles = layout.get("obstacles", [])
    envelopes = sum(bool(item.get("capsules")) for item in obstacles)
    text = f"{len(obstacles)} nearby obstacle objects. {envelopes} use pipe-shaped envelopes. {len(obstacles)-envelopes} use box envelopes."
    if envelopes:
        text += " Shaded pipe shapes show the supplied conservative envelopes. They are not exact CAD solids. Broad search boxes for these objects are not shown as occupied space."
        if any("clip_min" in capsule for item in obstacles for capsule in item.get("capsules", [])):
            text += " Trimmed envelope ends use the supplied analytic bounds. Display meshes can have open cut faces."
    counts = layout.get("cad_context", {}).get("counts", {})
    missing = counts.get("solid_instances_without_extracted_geometry")
    if isinstance(missing, int) and missing:
        text += f" {missing} source solid instances have no extracted geometry."
    curves = counts.get("explicit_curve_objects_unchecked")
    if isinstance(curves, int) and curves:
        text += f" {curves} explicit curve objects are not collision obstacles."
    if layout.get("cad_context"):
        text += " This count does not establish complete ship geometry."
    return text


def diagnostic_text(item):
    message = str(item.get("message", item.get("code", "Review finding")))
    details = []
    if item.get("stage"):
        details.append(str(item["stage"]).replace("_", " "))
    if item.get("source_handle"):
        source = "source " + str(item["source_handle"])
        already_named = re.search(r"\bsource\s+" + re.escape(str(item["source_handle"])) + r"(?![\w-])", message, re.IGNORECASE)
        placement = "/".join(map(str, item.get("instance_path", [])))
        if not already_named:
            details.append(source + (" in " + placement if placement else ""))
        elif placement and placement not in message:
            details.append("instance " + placement)
    if item.get("obstacle") and str(item["obstacle"]) not in message:
        details.append(str(item["obstacle"]))
    if item.get("obstacle_method"):
        details.append(str(item["obstacle_method"]).replace("_", " "))
    if item.get("point") is not None:
        details.append("point " + ", ".join(f"{value:g}" for value in item["point"]) + " mm")
    return message + (" (" + " · ".join(details) + ")" if details else "")


def accepted_assembly(result):
    assembly = result.get("assembly") or {}
    return assembly if assembly.get("status") == "ready" and result.get("candidate") else None


def add_assembly_meshes(figure, meshes, *, name, color, opacity, group):
    for index, mesh in enumerate(meshes):
        points, faces = mesh["vertices"], mesh["faces"]
        figure.add_trace(go.Mesh3d(
            x=[p[0] for p in points], y=[p[1] for p in points], z=[p[2] for p in points],
            i=[face[0] for face in faces], j=[face[1] for face in faces], k=[face[2] for face in faces],
            color=color, opacity=opacity, name=name, showlegend=index == 0, legendgroup=group,
            flatshading=False, meta={"assembly_component_id": mesh["id"], "geometry_type": mesh.get("geometry_type", "outer_envelope")},
            hovertemplate=html.escape(str(mesh["id"])) + "<br>" + html.escape(name) + "<br>Geometric outer envelope<extra></extra>",
        ))


def print_route_svg(layout, result):
    """A print-safe isometric centerline diagram independent of WebGL."""
    layout = display_layout(layout, result)
    assembly = accepted_assembly(result)
    routes = [("Original", result["original"]["points"], "#b96313")]
    if result.get("candidate"):
        routes.append(("Suggested", assembly["centerline_points"] if assembly else result["candidate"]["points"], "#008b75"))
    # Equal-axis isometric projection; source coordinates are never altered.
    def project(point):
        x, y, z = point
        return ((x-y) / math.sqrt(2), (x+y) / math.sqrt(6) - z * math.sqrt(2/3))
    all_points = [point for _, points, _ in routes for point in points] + box_vertices(layout["bounds"])
    projected = [project(point) for point in all_points]
    low = [min(p[axis] for p in projected) for axis in (0, 1)]
    high = [max(p[axis] for p in projected) for axis in (0, 1)]
    scale = min(860/max(high[0]-low[0], 1), 440/max(high[1]-low[1], 1))
    def xy(point):
        a, b = project(point)
        return 70 + (a-low[0])*scale, 65 + (b-low[1])*scale
    def polyline(points, color, width, dash=""):
        coords = ' '.join(f'{x:.2f},{y:.2f}' for x, y in map(xy, points))
        return f'<polyline points="{coords}" fill="none" stroke="{color}" stroke-width="{width}" stroke-linejoin="round" stroke-linecap="round"' + (f' stroke-dasharray="{dash}"' if dash else '') + '/>'
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 590" role="img" aria-label="Printable isometric route comparison">',
             '<rect width="1000" height="590" fill="white"/>',
             '<text x="30" y="28" fill="#17334d" font-size="18" font-family="sans-serif">Isometric centerline comparison · dimensions in mm</text>']
    for obstacle in layout.get("obstacles", []):
        if obstacle.get("capsules"):
            for capsule in obstacle["capsules"]:
                parts.append(polyline([capsule["start"], capsule["end"]], "#b8c3ce", 1))
        else:
            vertices = box_vertices(obstacle)
            for indices in ((0,1,2,3,0), (4,5,6,7,4), (0,4), (1,5), (2,6), (3,7)):
                parts.append(polyline([vertices[i] for i in indices], "#c4ccd4", 1))
    for label, points, color in routes:
        parts.append(polyline(points, color, 3, "7 4" if label == "Original" else ""))
    for label, point in (("Start", layout["pipe"]["start"]), ("End", layout["pipe"]["end"])):
        x, y = xy(point)
        parts.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="5" fill="#24384d"/><text x="{x+8:.2f}" y="{y-8:.2f}" font-size="14" font-family="sans-serif">{label}</text>')
    if assembly:
        for part in assembly.get("components", []):
            if part.get("kind") != "straight":
                continue
            midpoint = [(a+b)/2 for a, b in zip(part["start"], part["end"])]
            x, y = xy(midpoint)
            label = html.escape(f"{part['id']}: {part['length_mm']:.1f} mm")
            parts.append(f'<text x="{x+8:.2f}" y="{y-8:.2f}" fill="#006954" stroke="white" stroke-width="3" paint-order="stroke" font-family="sans-serif" font-size="12">{label}</text>')
    parts.extend(['<text x="30" y="548" fill="#b96313" font-size="15" font-family="sans-serif">Dashed orange: original</text>',
                  '<text x="270" y="548" fill="#008b75" font-size="15" font-family="sans-serif">Green: suggested</text>',
                  '<text x="30" y="574" fill="#45596d" font-size="13" font-family="sans-serif">Centerlines and obstacle references only. Use the 3D model and component schedule for geometric review.</text>', '</svg>'])
    return ''.join(parts)


def make_figure(layout, result):
    layout = display_layout(layout, result)
    figure = go.Figure()
    vertices = box_vertices(layout["bounds"])
    edges = [(0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6),
             (6, 7), (7, 4), (0, 4), (1, 5), (2, 6), (3, 7)]
    outline = [p for a, b in edges for p in (vertices[a], vertices[b], [None] * 3)]
    figure.add_trace(go.Scatter3d(
        x=[p[0] for p in outline], y=[p[1] for p in outline], z=[p[2] for p in outline],
        mode="lines", line={"color": "#71839b", "width": 2},
        name="Assumed local search area" if layout.get("cad_context") else "Supplied routing boundary",
        hoverinfo="skip",
    ))
    sources = {item["id"]: item for item in layout.get("cad_context", {}).get("obstacle_sources", [])}
    shown_groups = set()
    for obstacle in layout["obstacles"]:
        kind = "pipe-envelopes" if obstacle.get("capsules") else "box-envelopes"
        showlegend = kind not in shown_groups
        shown_groups.add(kind)
        source = sources.get(obstacle["id"], obstacle)
        hover = html.escape(obstacle["id"])
        if source.get("source_handle"):
            hover += "<br>Source " + html.escape(str(source["source_handle"]))
        if source.get("instance_path"):
            hover += "<br>Instance " + html.escape("/".join(map(str, source["instance_path"])))
        hover += "<br>Conservative envelope"
        if obstacle.get("capsules"):
            points, triangles = capsule_mesh(obstacle["capsules"])
            figure.add_trace(go.Mesh3d(
                x=[p[0] for p in points], y=[p[1] for p in points], z=[p[2] for p in points],
                i=[t[0] for t in triangles], j=[t[1] for t in triangles], k=[t[2] for t in triangles],
                name="Pipe envelopes", color="#99b8d0", opacity=.45, showlegend=showlegend,
                legendgroup=kind, meta={"obstacle_id": obstacle["id"]},
                hovertemplate=hover + "<extra></extra>",
            ))
        else:
            add_box(figure, obstacle, "Box envelopes", "#8394ab", 0.55,
                    showlegend=showlegend, legendgroup=kind, hover=hover)

    assembly = accepted_assembly(result)
    reference_meshes = (result.get("assembly") or {}).get("reference_meshes", [])
    rounded_reference = (result.get("assembly") or {}).get("reference_representation") == "rounded_baseline"
    if reference_meshes:
        reference_label = "Original rounded reference" if rounded_reference else "Original width reference (unfitted)"
        add_assembly_meshes(figure, reference_meshes, name=reference_label, color="#f5a55b", opacity=.38, group="original-width")
    if assembly:
        add_assembly_meshes(figure, assembly.get("meshes", []), name="Rounded pipe proposal", color="#26d2b3", opacity=.92, group="rounded-proposal")
    for key, label, color, dash in (
        ("original", "Original route", "#f5a55b", "dash"),
        ("candidate", "Suggested route", "#26d2b3", "solid"),
    ):
        route = result.get(key)
        if route is None:
            continue
        points = assembly["centerline_points"] if key == "candidate" and assembly else route["points"]
        hide_centerline = (key == "candidate" and assembly) or (key == "original" and rounded_reference)
        figure.add_trace(go.Scatter3d(
            x=[p[0] for p in points], y=[p[1] for p in points], z=[p[2] for p in points],
            mode="lines+markers", name=label, visible="legendonly" if hide_centerline else True,
            legendgroup=key + "-centerline",
            line={"color": color, "width": 7, "dash": dash},
            marker={"size": 3, "color": color},
            hovertemplate=label + "<br>x %{x} mm<br>y %{y} mm<br>z %{z} mm<extra></extra>",
        ))
    for index, record in enumerate(layout.get("protected_endpoints", [])):
        points = record["points"]
        figure.add_trace(go.Scatter3d(
            x=[p[0] for p in points], y=[p[1] for p in points], z=[p[2] for p in points],
            mode="lines", name="Fixed end sections", legendgroup="fixed-sections",
            showlegend=index == 0, line={"color": "#ffd166", "width": 10},
            hovertemplate="Fixed " + record["endpoint"] + " section<br>x %{x} mm<br>y %{y} mm<br>z %{z} mm<extra></extra>",
        ))
    stops = [item for item in result.get("diagnostics", []) if isinstance(item.get("point"), (list, tuple))
             and len(item["point"]) == 3 and all(isinstance(v, (int, float)) and math.isfinite(v) for v in item["point"])]
    if stops:
        figure.add_trace(go.Scatter3d(
            x=[item["point"][0] for item in stops], y=[item["point"][1] for item in stops], z=[item["point"][2] for item in stops],
            mode="markers", name="Search diagnostic points", marker={"color": "#ff7588", "size": 7, "symbol": "diamond"},
            text=[html.escape(diagnostic_text(item)) for item in stops], hovertemplate="%{text}<extra></extra>",
        ))
    points = [layout["pipe"]["start"], layout["pipe"]["end"]]
    figure.add_trace(go.Scatter3d(
        x=[p[0] for p in points], y=[p[1] for p in points], z=[p[2] for p in points],
        mode="markers+text", text=["Start", "End"], textposition="top center",
        marker={"color": "#fff0b3", "size": 6}, name="Fixed connections",
        hovertemplate="%{text}<br>x %{x} mm<br>y %{y} mm<br>z %{z} mm<extra></extra>",
    ))
    axis = {"backgroundcolor": "#102035", "gridcolor": "#23364c", "showbackground": True}
    figure.update_layout(
        template="plotly_dark", paper_bgcolor="#102035", plot_bgcolor="#102035",
        height=560, margin={"l": 5, "r": 5, "t": 10, "b": 5},
        legend={"orientation": "h", "y": 1.08, "x": 0, "groupclick": "togglegroup"},
        scene={"aspectmode": "data", "camera": {"eye": {"x": 1.5, "y": 1.7, "z": 1.1}},
               "xaxis": {**axis, "title": "x / mm"},
               "yaxis": {**axis, "title": "y / mm"},
               "zaxis": {**axis, "title": "z / mm"}},
    )
    if assembly:
        def visibility(mode):
            values = []
            for trace in figure.data:
                group = trace.legendgroup
                original_trace = group in ("original-width", "original-centerline")
                candidate_trace = group in ("rounded-proposal", "candidate-centerline")
                hidden = (mode == "Before" and candidate_trace) or (mode == "After" and original_trace)
                values.append(False if hidden else trace.visible if trace.visible is not None else True)
            return values
        figure.update_layout(
            height=620, margin={"l": 5, "r": 5, "t": 90, "b": 5},
            updatemenus=[{"type": "buttons", "direction": "right", "x": 0, "y": 1.24, "active": 2,
                          "bgcolor": "#cbd9e8", "bordercolor": "#55dcc8", "font": {"color": "#102035"},
                          "buttons": [{"label": mode, "method": "update", "args": [{"visible": visibility(mode)}]}
                                      for mode in ("Before", "After", "Both")]}],
        )
    return figure


def write_dxf(layout, result, destination):
    """Export separate review geometry; never modify a source drawing."""
    layout = display_layout(layout, result)
    synthetic = layout.get("provenance") == "synthetic"
    prefix = "SYNTHETIC" if synthetic else "CAD_DERIVED"
    document = ezdxf.new("R2018")
    document.units = units.MM
    document.header["$MEASUREMENT"] = 1
    space = document.modelspace()
    for key, color in (("original", 30), ("candidate", 4)):
        if result.get(key) is None:
            continue
        layer = prefix + "_" + key.upper() + "_CENTERLINE"
        document.layers.add(layer, color=color)
        space.add_polyline3d(result[key]["points"], dxfattribs={"layer": layer})
    assembly = accepted_assembly(result)
    mesh_groups = [(prefix + "_ORIGINAL_WIDTH_REFERENCE", (result.get("assembly") or {}).get("reference_meshes", []), 30)]
    if assembly:
        mesh_groups.append((prefix + "_PROPOSED_PIPE_ENVELOPE", assembly.get("meshes", []), 3))
        rounded_layer = prefix + "_ROUNDED_CENTERLINE"
        document.layers.add(rounded_layer, color=3)
        space.add_polyline3d(assembly["centerline_points"], dxfattribs={"layer": rounded_layer})
        dimension_layer = prefix + "_PROPOSED_DIMENSIONS"
        document.layers.add(dimension_layer, color=7)
        for part in assembly.get("components", []):
            midpoint = [(a+b)/2 for a, b in zip(part["start"], part["end"])]
            note = f"{part['id']}: {part['length_mm']:.1f} mm"
            if part["kind"] == "bend":
                note += f" arc, R{part['radius_mm']:g}, {part['angle_deg']:g} deg"
            else:
                note += " straight geometric cut length"
            space.add_text(note, dxfattribs={"layer": dimension_layer, "insert": midpoint, "height": max(2, layout["pipe"]["diameter"]*.2)})
    for layer, meshes, color in mesh_groups:
        if not meshes:
            continue
        document.layers.add(layer, color=color)
        for mesh in meshes:
            for face in mesh["faces"]:
                space.add_3dface([mesh["vertices"][index] for index in face], dxfattribs={"layer": layer})
    if layout.get("protected_endpoints"):
        fixed_layer = prefix + "_FIXED_END_SECTIONS"
        document.layers.add(fixed_layer, color=2)
        for record in layout["protected_endpoints"]:
            space.add_polyline3d(record["points"], dxfattribs={"layer": fixed_layer})
    obstacle_layer = prefix + "_OBSTACLE"
    document.layers.add(obstacle_layer, color=8)
    envelope_layer = prefix + "_ENVELOPE_AXES"
    if any(item.get("capsules") for item in layout["obstacles"]):
        document.layers.add(envelope_layer, color=9)
    for obstacle in layout["obstacles"]:
        if obstacle.get("capsules"):
            for index, capsule in enumerate(obstacle["capsules"]):
                start, end = capsule["start"], capsule["end"]
                if start != end:
                    space.add_line(start, end, dxfattribs={"layer": envelope_layer})
                else:
                    space.add_point(start, dxfattribs={"layer": envelope_layer})
                space.add_text(
                    f"{obstacle['id']} envelope axis {index+1}. Radius {capsule['radius']:g} mm. Not a pipe centerline."
                    + (" Clip bounds " + ", ".join(f"{v:g}" for v in capsule["clip_min"]) + " to "
                       + ", ".join(f"{v:g}" for v in capsule["clip_max"]) + " mm." if "clip_min" in capsule else ""),
                    dxfattribs={"layer": envelope_layer, "insert": start, "height": max(2, min(20, capsule["radius"]/3))},
                )
            continue
        points = box_vertices(obstacle)
        for indices in ((0, 1, 2, 3, 0), (4, 5, 6, 7, 4), (0, 4), (1, 5), (2, 6), (3, 7)):
            space.add_polyline3d([points[i] for i in indices], dxfattribs={"layer": obstacle_layer})
    label = "SYNTHETIC TEST ONLY" if synthetic else "CAD-DERIVED PROTOTYPE REVIEW"
    if layout.get("benchmark", {}).get("synthetic_mutation"):
        label = "CONTROLLED DETOUR TEST. Artificial detour added to CAD-derived geometry"
    shape_note = (f" Proposed OD {assembly['outer_diameter_mm']:g} mm; centerline bend radius {assembly['bend_radius_mm']:g} mm."
                  " Rounded proposal meshes are geometric outer envelopes, not verified catalog or fabricated pipe solids.") if assembly else ""
    if (result.get("assembly") or {}).get("reference_meshes"):
        shape_note += (" Original rounded reference uses the same geometric radius assumption."
                       if result["assembly"].get("reference_representation") == "rounded_baseline" else
                       " Original width reference has unfitted corners.")
    space.add_text(f"{label}. Centerlines in mm. Unverified rules. Source CAD has not been modified." + shape_note,
                   dxfattribs={"height": max(2, layout["pipe"]["diameter"]), "insert": layout["bounds"]["min"]})
    document.saveas(destination)


def write_html(layout, result, elapsed, destination):
    layout = display_layout(layout, result)
    escape = html.escape
    synthetic = layout.get("provenance") == "synthetic"
    benchmark = layout.get("benchmark", {})
    controlled_detour = benchmark.get("synthetic_mutation") is True
    gravity = layout.get("system_type") == "gravity"
    original, candidate = result["original"], result["candidate"]
    assembly = accepted_assembly(result)
    def metric(route, key):
        if route is None:
            return "No accepted route"
        if key == "valid":
            return "Pass geometric checks" if route[key] else "Needs attention"
        return f"{route[key]:,.0f}" + (" mm" if key == "length" else "")
    direction_label = "Sampled direction changes (not fittings)" if not synthetic else "Direction changes"
    rows = "".join(
        f"<tr><th>{label}</th><td>{metric(original, key)}</td><td>{metric(candidate, key)}</td></tr>"
        for label, key in (("Search polyline length" if assembly else "Route length", "length"), (direction_label, "bends"), ("Geometric checks", "valid"))
    )
    if assembly:
        metrics = assembly.get("metrics", {})
        baseline = assembly.get("baseline_metrics") or {}
        for label, key, unit in (("Rounded centerline length", "centerline_length_mm", " mm"),
                                 ("Rounded bends in geometric proposal", "bend_count", "")):
            before = (f"{baseline[key]:,.1f}{unit}" if unit else f"{baseline[key]:g}") if key in baseline else "Not assessed as an assembly"
            after = (f"{metrics[key]:,.1f}{unit}" if unit else f"{metrics[key]:g}") if key in metrics else "Not available"
            rows += f"<tr><th>{label}</th><td>{before}</td><td>{after}</td></tr>"
    points = "" if candidate is None else "".join(
        "<tr><td>" + str(i + 1) + "</td>" + "".join(f"<td>{value:g}</td>" for value in point) + "</tr>"
        for i, point in enumerate(candidate["points"])
    )
    issue_items = []
    for key, label in (("original", "Original"), ("candidate", "Suggested")):
        if result.get(key):
            issue_items.extend(f"<li>{label}: {escape(diagnostic_text(issue))}</li>" for issue in result[key]["issues"])
    issues = "<ul>" + "".join(issue_items) + "</ul>" if issue_items else "<p>No geometric issues found by the implemented checks. Review the rule findings separately.</p>"
    diagnostics = ""
    if result.get("diagnostics"):
        diagnostics = '<section class="panel"><h2>What stopped the search</h2><ul>' + "".join(
            f"<li>{escape(diagnostic_text(item))}</li>" for item in result["diagnostics"]
        ) + "</ul></section>"
    assumptions = list(dict.fromkeys(layout.get("assumptions", []) + result.get("assumptions", [])))
    limits = result.get("limitations", [])
    assumption_list = "".join(f"<li>{escape(item)}</li>" for item in assumptions)
    limitation_list = "".join(f"<li>{escape(item)}</li>" for item in limits)
    plot = make_figure(layout, result).to_html(
        full_html=False, include_plotlyjs=True, div_id="route-view",
        config={"responsive": True, "displaylogo": False, "displayModeBar": False, "scrollZoom": False,
                "modeBarButtonsToRemove": ["sendChartToCloud"]},
    )
    saved = original["length"] - candidate["length"] if candidate else 0
    if candidate and original["length"] > 0:
        context = "test" if synthetic else "review"
        headline = f"{saved:,.0f} mm shorter in this {context}" if saved > 0 else "Route review complete"
    else:
        headline = "No accepted route from this search"
    if assembly:
        headline = {"clearance_repair": "Rounded pipe proposal repairs modeled clearance",
                    "fewer_bends": "Rounded pipe proposal uses fewer bends",
                    "fewer_elevation_changes": "Rounded pipe proposal uses fewer vertical runs",
                    "shorter_length": "Shorter rounded pipe proposal",
                    "unchanged": "Original rounded route retained"}.get(assembly.get("change_type"), "Review pipe proposal")
    provenance_tag = "SYNTHETIC TEST" if synthetic else "CAD-DERIVED REVIEW"
    benchmark_panel = ""
    if controlled_detour:
        provenance_tag = "CONTROLLED DETOUR TEST"
        headline = f"{saved:,.0f} mm removed from an artificial detour" if candidate and saved > 0 else "Controlled detour test"
        baseline_length = benchmark.get("baseline", {}).get("validation", {}).get("length")
        baseline_text = ""
        if isinstance(baseline_length, (int, float)):
            baseline_text = f"<p><strong>Unchanged CAD baseline: {baseline_length:,.0f} mm</strong></p>"
            if candidate and candidate["length"] > baseline_length:
                baseline_text += f"<p>The suggested route is still {candidate['length'] - baseline_length:,.0f} mm longer than that baseline. This result shows recovery from an added detour. It does not show an improvement over the unchanged CAD route.</p>"
        benchmark_panel = (
            '<section class="panel controlled"><h2>Artificial detour added for this test</h2>'
            '<p>The added detour is not a defect found in the source drawing. The source CAD has not been edited.</p>'
            + baseline_text + '</section>'
        )
    provenance_text = (
        "The geometry is invented for testing. It is not the supplied ship model."
        if synthetic else
        "The input is marked as derived from CAD. Its extraction method and completeness still need review against the source drawing."
    )
    system_text = "Gravity drain" if gravity else "Pressurized pipe"
    boundary_text = "an assumed local search area" if layout.get("cad_context") else "the supplied boundary"
    coverage_text = f"Checks cover fixed endpoints and {boundary_text}. They check the pipe plus selected clearance against the supplied obstacle envelopes."
    coverage_text += " " + geometry_coverage(layout)
    if layout.get("protected_endpoints"):
        coverage_text += " Yellow end sections must remain unchanged. Contact with their listed connection objects is allowed only along those fixed sections. The movable route still checks every obstacle. Connection identities remain assumptions."
    challenge = result.get("rule_profile") == "fmg-v3"
    rule_text = "Selected checks use the supplied V3 brief; missing evidence remains needs_review." if challenge else "The selected rule values are provisional."
    if gravity and challenge:
        coverage_text += " Drain checks use 1:25 fore-aft and 1:100 athwartship minimum fall when ship axes and flow are confirmed. Missing axes, exact pitch and unsupported directions remain review items."
    elif gravity:
        coverage_text += f" Each segment is checked for downhill flow and the selected minimum slope of {layout['rules']['min_slope']:g} in the start-to-end direction. Positive Z is assumed to point upward."
    else:
        coverage_text += " New pressure route suggestions use axis-aligned segments."
    if result.get("comparison_objective") == "rounded_bends_elevation_length":
        coverage_text += " Bounded rounded alternatives are ranked by generated bend count, vertical runs, then rounded centerline length."
    if not synthetic:
        coverage_text += " Existing CAD centerlines retain their supplied directions and samples. Sampled direction changes are not verified fitting counts."
        if result.get("comparison_objective") == "rounded_bends_elevation_length":
            coverage_text += " A verified fitting saving over the original is not established by its polyline samples."
        else:
            coverage_text += " Route comparisons use polyline length only."
    if "bend_radius" in layout["rules"]:
        coverage_text += (" Rounded outer envelopes are checked conservatively against the supplied obstacles. Catalog and fabrication suitability remain unverified." if assembly else
                          " Available tangent length is checked against the selected bend radius. Actual curved fitting volumes are not checked.")
    source_units = escape(str(result.get("input_units", layout["units"])))
    export_text = "synthetic centerlines and obstacle outlines" if synthetic else "CAD-derived review centerlines and the supplied obstacle outlines"
    if any(item.get("capsules") for item in layout["obstacles"]):
        export_text = "review centerlines, box outlines and labeled envelope axes with their radii. Envelope axes are not physical pipes"
    if assembly:
        export_text += ", plus separate rounded proposal meshes, a rounded centerline and component length labels"
    print_plot = print_route_svg(layout, result)
    reference_caption = ("Orange shows the rounded original reference using the same geometric radius assumption."
                         if (result.get("assembly") or {}).get("reference_representation") == "rounded_baseline" else
                         "Orange shapes are an unfitted original width reference.")
    plot_caption = ("Green rounded shapes show the proposed outer envelope at the selected diameter. " + reference_caption
                    if assembly else "The collision check also accounts for pipe thickness.")
    content = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pipe route review</title>
<style>
* {{ box-sizing: border-box }}
body {{ margin: 0; background: #091421; color: #e5edf6; font-family: system-ui, sans-serif; line-height: 1.6 }}
main {{ max-width: 1120px; margin: auto; padding: 32px 22px 70px }}
h1 {{ font-size: clamp(26px, 4vw, 40px); line-height: 1.15; margin: 16px 0 }}
h2 {{ font-size: 21px; margin-top: 0 }}
p {{ max-width: 850px }}
.tag {{ color: #ffd493; font-weight: 700; letter-spacing: .08em; font-size: 12px }}
.muted {{ color: #afbed0 }}
.panel {{ margin-top: 24px; padding: 24px; background: #102035; border: 1px solid #253850; border-radius: 14px }}
.controlled {{ border-color: #9d7538; background: #282416 }}
.columns {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px }}
table {{ width: 100%; border-collapse: collapse; text-align: left }}
th,td {{ border-bottom: 1px solid #2c4058; padding: 12px 8px }}
thead th {{ color: #afbed0 }}
li {{ margin-bottom: 10px }}
a {{ color: #55dcc8 }}
.plot {{ overflow: hidden; padding: 10px }}
.print-figure {{ display: none }}
.print-figure svg {{ width: 100%; height: auto }}
.pill {{ display: inline-block; background: #24354b; padding: 4px 10px; border-radius: 7px }}
@media(max-width: 760px) {{ .columns {{ grid-template-columns: 1fr }} .panel {{ padding: 16px }} }}
@media print {{
  body {{ background: white; color: #132c43 }}
  main {{ max-width: none; padding: 0 }}
  .panel {{ background: white; border-color: #8090a0; padding: 12px; border-radius: 0 }}
  .muted, thead th {{ color: #354c61 }}
  .plot {{ display: none }}
  .print-figure {{ display: block; break-inside: avoid }}
  .columns {{ display: block }}
  a {{ color: #006858 }}
  tr {{ break-inside: avoid }}
}}
</style></head><body><main>
<span class="tag">TRACK 2 / {provenance_tag}</span>
<h1>{headline}</h1>
<p>{escape(layout.get('name', 'Pipe layout'))}. {provenance_text} {rule_text} This report does not certify compliance with engineering standards.</p>
<p class="muted">{system_text}. Fixed connections. Same {layout['pipe']['diameter']:g} mm outer diameter. Selected clearance {layout['rules']['clearance']:g} mm. Declared input units {source_units}. This report uses millimeters.</p>
<p><a href="#route-view">View 3D comparison</a> &nbsp; <a href="route-review.xlsx">Download Excel review</a></p>
{benchmark_panel}
<div class="panel"><table><thead><tr><th>Measure</th><th>Original</th><th>Suggested</th></tr></thead><tbody>{rows}</tbody></table>
<details><summary>What this comparison checks</summary><p class="muted">{coverage_text}</p></details></div>
{diagnostics}
{assembly_panel(result)}
<div class="panel plot">{plot}</div>
<div class="panel print-figure">{print_plot}</div>
<p class="muted">Drag to rotate. Click a legend item to hide it. Lines show pipe centers. {plot_caption}</p>
{challenge_panel(result)}
<div class="columns"><section class="panel"><h2>Check results</h2>{issues}
<p>These checks do not confirm fitting availability or hydraulic performance. Every corner and connection still needs designer review.</p>
<p class="muted">{escape(result['search']['reason'])}</p>
<p class="muted">Search finished in {elapsed:.3f} seconds on this computer. This is not a hardware comparison.</p></section>
<section class="panel"><h2>Suggested centerline points</h2><p class="muted">Coordinates in millimeters. These are route suggestions for review.</p>
<table><thead><tr><th>Point</th><th>x</th><th>y</th><th>z</th></tr></thead><tbody>{points}</tbody></table></section></div>
<section class="panel"><h2>Assumptions used in this review</h2><ul>{assumption_list}</ul></section>
<section class="panel"><h2>What still needs work</h2><ul>{limitation_list}</ul>
<p>Review the source geometry and unit assumptions before interpreting real dimensions. {rule_text}</p></section>
<p><a href="result.json">Full result and inputs</a> &nbsp; <a href="route-centerlines.dxf">{'Proposal geometry for CAD review' if assembly else 'Centerlines for CAD review'}</a> &nbsp; <a href="route-review.xlsx">Download Excel review</a></p>
<p class="muted">The DXF contains {export_text}. It does not modify the source ship drawing or contain finished pipe solids and fittings. The report works offline.</p>
</main><script>
let printDetails = [];
window.addEventListener('beforeprint', () => {{
  printDetails = Array.from(document.querySelectorAll('details')).map(item => [item, item.open]);
  printDetails.forEach(([item]) => {{ item.open = true; }});
}});
window.addEventListener('afterprint', () => {{
  printDetails.forEach(([item, open]) => {{ item.open = open; }});
  printDetails = [];
}});
</script></body></html>"""
    destination.write_text(content, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layout", type=Path, default=ROOT / "examples" / "test-room.json")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "generated" / "first-route-demo")
    args = parser.parse_args()
    source = args.layout.resolve()
    output = args.output.resolve()
    targets = [output / name for name in ("result.json", "index.html", "route-centerlines.dxf")]
    if source in [target.resolve() for target in targets]:
        parser.error("Choose an output folder that will not overwrite the input layout.")
    try:
        raw = source.read_bytes()
        layout = json.loads(raw)
        start = time.perf_counter()
        result = run_workflow(layout)
        elapsed = time.perf_counter() - start
        normalized = result["normalized_layout"]
        output.mkdir(parents=True, exist_ok=True)
        report = {"generated_at": datetime.now(timezone.utc).isoformat(),
                  "input_sha256": hashlib.sha256(raw).hexdigest(), "source_layout": layout,
                  "layout": normalized,
                  "result": result, "runtime_seconds": elapsed,
                  "runtime": {"os": platform.system(), "architecture": platform.machine(),
                              "python": platform.python_version()}}
        targets[0].write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        write_dxf(normalized, result, targets[2])
        write_review_xlsx(normalized, result, output / "route-review.xlsx")
        write_html(normalized, result, elapsed, targets[1])
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        parser.exit(2, f"Cannot generate demo: {exc}\n")
    print(f"{normalized['provenance']} review result: {result['status']}")
    for key in ("original", "candidate"):
        route = result.get(key)
        if route:
            print(f"{key}: {route['length']:g} mm / {route['bends']} direction changes / checks {route['valid']}")
    print(f"Report: {targets[1]}")
    print("Prototype rules only. Official compliance and source geometry completeness are not verified.")


if __name__ == "__main__":
    main()
