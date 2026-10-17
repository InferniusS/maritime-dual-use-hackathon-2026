"""Read a supported subset of real DXF ACIS geometry without editing its source.

This is a preview importer, not a solid modeller or a semantic pipe importer.
Boundary edges and cylindrical faces come from source geometry. Torus patches
use an explicitly marked shorter-arc inference. Neither inferred axes nor mesh
bounds establish collision safety or system connectivity.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import math
from pathlib import Path

import ezdxf
from ezdxf.acis import sat, sab
from ezdxf.acis.const import AcisException
from ezdxf.math import Matrix44, Vec3

UNIT_FACTORS = {"mm": 1.0, "in": 25.4, "m": 1000.0}
TAU = math.tau
SAMPLES = 32


def _data(entity):
    """Flatten binary ACIS vectors to match the textual ACIS token positions."""
    result = []
    for item in entity.data:
        value = item.value if isinstance(item, sab.Token) else item
        if isinstance(value, tuple):
            result.extend(value)
        else:
            result.append(value)
    return result


def _name(entity):
    return getattr(entity, "name", "")


def _vec(values):
    return Vec3(*(float(v) for v in values))


def _parse_acis(entity):
    if entity.sab:
        return sab.parse_sab(entity.sab)
    lines = entity.sat
    if len(lines) < 4:
        raise ValueError("No readable ACIS payload")
    # DXF group-code 1/3 chunks can split a number or keyword mid-token.
    # Joining with spaces corrupts those tokens. The # terminators are the
    # record boundaries. The first three header lines are separate records.
    body = "".join(lines[3:]).replace("#", "#\n")
    return sat.parse_sat("\n".join(lines[:3]) + "\n" + body)


def _body_matrix(face):
    try:
        shell = _data(face)[3]
        lump = _data(shell)[5]
        body = _data(lump)[3]
        transform = _data(body)[3]
        if _name(transform) != "transform":
            return Matrix44()
        values = [float(v) for v in _data(transform)[:12]]
        return Matrix44([
            *values[:3], 0, *values[3:6], 0, *values[6:9], 0,
            *values[9:12], 1,
        ])
    except (IndexError, TypeError, ValueError, AttributeError) as error:
        raise ValueError("Cannot resolve the ACIS body transform") from error


def _face_edges(face):
    result, seen_loops = [], set()
    loop = _data(face)[2]
    while _name(loop) == "loop" and id(loop) not in seen_loops:
        seen_loops.add(id(loop))
        loop_data = _data(loop)
        coedge, seen_coedges = loop_data[2], set()
        while _name(coedge) == "coedge" and id(coedge) not in seen_coedges:
            seen_coedges.add(id(coedge))
            values = _data(coedge)
            edge = values[4]
            if _name(edge) == "edge" and edge not in result:
                result.append(edge)
            coedge = values[1]
        loop = loop_data[1]
    return result


def _ellipse(edge):
    values = _data(edge)
    curve = values[6]
    if _name(curve) != "ellipse-curve":
        return None
    data = _data(curve)
    center, normal, major = _vec(data[1:4]), _vec(data[4:7]), _vec(data[7:10])
    ratio = float(data[10])
    if normal.magnitude < 1e-12 or major.magnitude < 1e-12 or ratio <= 0:
        raise ValueError("Degenerate ellipse boundary")
    return {
        "center": center, "normal": normal.normalize(), "major": major,
        "ratio": ratio, "radius": major.magnitude,
        "start": float(values[2]), "end": float(values[4]),
    }


def _full_circle(edge):
    circle = _ellipse(edge)
    if circle is None or not math.isclose(circle["ratio"], 1, abs_tol=1e-7):
        return None
    if not math.isclose(abs(circle["end"] - circle["start"]), TAU, abs_tol=1e-6):
        return None
    return circle


def _edge_points(edge):
    ellipse = _ellipse(edge)
    if ellipse is not None:
        minor = ellipse["normal"].cross(ellipse["major"]) * ellipse["ratio"]
        span = ellipse["end"] - ellipse["start"]
        count = max(4, math.ceil(abs(span) / TAU * SAMPLES))
        if count > 10000:
            raise ValueError("Unsupported ellipse span")
        return [ellipse["center"] + ellipse["major"] * math.cos(t) + minor * math.sin(t)
                for t in (ellipse["start"] + span * i / count for i in range(count + 1))]
    values = _data(edge)
    if _name(values[6]) == "straight-curve":
        data = _data(values[6])
        origin, direction = _vec(data[1:4]), _vec(data[4:7])
        return [origin + direction * float(values[i]) for i in (2, 4)]
    return None


def _rings_mesh(rings):
    vertices = [p for ring in rings for p in ring]
    faces = []
    width = len(rings[0])
    for row in range(len(rings) - 1):
        for j in range(width):
            a, b = row * width + j, row * width + (j + 1) % width
            c, d = a + width, b + width
            faces.extend([[a, b, c], [b, d, c]])
    return vertices, faces


def _ring(center, x_axis, y_axis, radius):
    return [center + radius * (x_axis * math.cos(TAU * j / SAMPLES)
                              + y_axis * math.sin(TAU * j / SAMPLES))
            for j in range(SAMPLES)]


def _surface_patch(face):
    """Return bounded geometry only for the simple supported face topologies."""
    surface = _data(face)[5]
    edges = _face_edges(face)
    if len(edges) != 2:
        return None
    circles = [_full_circle(e) for e in edges]
    if any(c is None for c in circles):
        return None
    a, b = circles
    data = _data(surface)
    delta = b["center"] - a["center"]
    same_radius = math.isclose(a["radius"], b["radius"], rel_tol=1e-7, abs_tol=1e-7)
    if _name(surface) == "cone-surface":
        axis = _vec(data[4:7]).normalize()
        # The ACIS cone's sine/cosine of half-angle distinguish a cylinder.
        if abs(float(data[13])) > 1e-8 or not same_radius:
            return None
        if delta.magnitude < 1e-8 or delta.cross(axis).magnitude > max(1e-6, delta.magnitude * 1e-7):
            return None
        if any(abs(abs(c["normal"].dot(axis)) - 1) > 1e-7 for c in circles):
            return None
        x_axis = a["major"].normalize()
        y_axis = axis.cross(x_axis).normalize()
        rings = [_ring(c["center"], x_axis, y_axis, a["radius"]) for c in circles]
        vertices, faces = _rings_mesh(rings)
        return vertices, faces, {
            "kind": "cylinder", "points": [a["center"], b["center"]],
            "radius": a["radius"], "confidence": "analytic_geometry",
            "evidence": "Cylinder face bounded by two full circular edges",
        }
    if _name(surface) == "plane-surface":
        if delta.magnitude > 1e-6 or same_radius:
            return None
        axis = _vec(data[4:7]).normalize()
        x_axis = a["major"].normalize()
        y_axis = axis.cross(x_axis).normalize()
        vertices, faces = _rings_mesh([
            _ring(c["center"], x_axis, y_axis, c["radius"]) for c in circles
        ])
        return vertices, faces, {"kind": "annulus", "confidence": "analytic_geometry"}
    if _name(surface) == "torus-surface" and same_radius:
        center, normal = _vec(data[1:4]), _vec(data[4:7]).normalize()
        major_radius, minor_radius = float(data[7]), abs(float(data[8]))
        if not math.isclose(minor_radius, a["radius"], rel_tol=1e-6):
            return None
        va, vb = a["center"] - center, b["center"] - center
        if major_radius <= minor_radius or any(
            abs(v.dot(normal)) > 1e-5 or not math.isclose(v.magnitude, major_radius, rel_tol=1e-6)
            for v in (va, vb)
        ):
            return None
        u = va.normalize()
        v = normal.cross(u).normalize()
        angle = math.atan2(vb.dot(v), vb.dot(u))
        if abs(angle) < 1e-7:
            return None
        # The two boundary circles do not disambiguate the complementary arc.
        # This patch is explicitly only a short-arc visual inference.
        steps = max(4, math.ceil(abs(angle) / (math.pi / 24)))
        centers, rings = [], []
        for i in range(steps + 1):
            t = angle * i / steps
            radial = u * math.cos(t) + v * math.sin(t)
            location = center + major_radius * radial
            centers.append(location)
            rings.append(_ring(location, radial, normal, minor_radius))
        vertices, faces = _rings_mesh(rings)
        return vertices, faces, {
            "kind": "torus", "points": centers, "radius": minor_radius,
            "confidence": "inferred_short_arc",
            "evidence": "Torus surface and two full circular trim edges. Shorter arc chosen for preview",
        }
    return None


def _xyz(points, matrix, factor):
    result = [[float(v) * factor for v in matrix.transform(p)] for p in points]
    if not all(math.isfinite(v) for p in result for v in p):
        raise ValueError("Nonfinite geometry coordinate")
    return result


def _candidate_chains(candidates, tolerance=0.001):
    """Group geometrically continuous axes inside a single source solid only.

    This is not network extraction. Selecting the largest coincident radius
    identifies an envelope candidate, not a nominal pipe size or a valid port.
    """
    by_solid = {}
    for item in candidates:
        if item["geometric_radius"] is None:
            continue
        key = (item["source_handle"], tuple(item["instance_path"]))
        by_solid.setdefault(key, []).append(item)
    result = []
    for (handle, instance), items in by_solid.items():
        # Keep the larger of concentric inner and outer face axes.
        unique = []
        for item in items:
            a, b = item["points"][0], item["points"][-1]
            match = None
            for i, existing in enumerate(unique):
                c, d = existing["points"][0], existing["points"][-1]
                if item["kind"] == existing["kind"] and (
                    (math.dist(a, c) <= tolerance and math.dist(b, d) <= tolerance)
                    or (math.dist(a, d) <= tolerance and math.dist(b, c) <= tolerance)
                ):
                    match = i
                    break
            if match is None:
                unique.append(item)
            elif item["geometric_radius"] > unique[match]["geometric_radius"]:
                unique[match] = item
        if not unique:
            continue
        radii = [item["geometric_radius"] for item in unique]
        if max(radii) - min(radii) > tolerance:
            continue  # Multi-radius solids may be fittings or equipment.
        nodes, adjacency, endpoints = [], {}, []
        for index, item in enumerate(unique):
            edge_nodes = []
            for point in (item["points"][0], item["points"][-1]):
                node = next((i for i, other in enumerate(nodes) if math.dist(point, other) <= tolerance), None)
                if node is None:
                    node = len(nodes)
                    nodes.append(point)
                adjacency.setdefault(node, []).append(index)
                edge_nodes.append(node)
            endpoints.append(edge_nodes)
        ends = [node for node, edges in adjacency.items() if len(edges) == 1]
        if len(ends) != 2 or any(len(edges) > 2 for edges in adjacency.values()):
            continue
        node, used, points, selected = ends[0], set(), [], []
        while True:
            available = [i for i in adjacency[node] if i not in used]
            if not available:
                break
            index = available[0]
            used.add(index)
            item = unique[index]
            forward = endpoints[index][0] == node
            section = item["points"] if forward else list(reversed(item["points"]))
            points.extend(section if not points else section[1:])
            selected.append(item)
            node = endpoints[index][1 if forward else 0]
        if len(used) != len(unique):
            continue
        result.append({
            "id": "/".join([*instance, handle]) + ":candidate-run",
            "name": f"Source solid {handle} candidate run",
            "points": points, "geometric_diameter": 2 * max(radii),
            "source_handles": [handle], "instance_path": list(instance),
            "source_records": [item["source_record"] for item in selected],
            "confidence": "inferred_geometry", "requires_review": True,
            "semantic_pipe_size_verified": False, "connectivity_verified": False,
            "endpoint_tolerance_mm": tolerance,
            "contains_inferred_bend": any(item["confidence"] == "inferred_short_arc" for item in selected),
            "evidence": "Continuous surface axes within one source solid. Outer radius selected from coincident faces. Endpoints are geometric candidates, not verified ports.",
        })
    return result


def inspect_cad(path, source_units="mm"):
    """Extract preview geometry and candidate axes from a local DXF.

    source_units explicitly controls all output scaling, including when DXF
    headers disagree. It never relabels coordinates without rescaling them.
    """
    path = Path(path)
    if source_units not in UNIT_FACTORS:
        raise ValueError("source_units must be mm, in or m")
    factor = UNIT_FACTORS[source_units]
    document = ezdxf.readfile(path)
    units_code = document.header.get("$INSUNITS", 0)
    expected_code = {"mm": 4, "in": 1, "m": 6}[source_units]
    warnings = [
        "Geometry preview only. This does not establish engineering compliance or complete solid collision geometry.",
        "Pipe identity, nominal size, ports, flow direction and network connectivity are not verified.",
        "Axis and route candidates can belong to fittings, fasteners or equipment. They require semantic review before routing.",
        "The full ship structure and other systems are not guaranteed to be present in this drawing.",
    ]
    assumption = units_code != expected_code
    if assumption:
        warnings.append(f"Source units are assumed to be {source_units}. DXF $INSUNITS is {units_code}. All output is converted to mm.")
    objects, candidates = [], []
    counts = Counter()
    unsupported = Counter()

    def add_object(base, suffix, geometry, **extra):
        objects.append({**base, "id": f'{base["id"]}:{suffix}', "geometry": geometry, **extra})

    def walk(entities, parent_matrix, instance_path, block_stack=(), inherited_layer=None):
        for entity_index, entity in enumerate(entities):
            kind, handle = entity.dxftype(), entity.dxf.get("handle") or f"virtual{entity_index}"
            layer = entity.dxf.get("layer", "0")
            if layer == "0" and inherited_layer is not None:
                layer = inherited_layer
            base = {"id": "/".join([*instance_path, handle]), "type": kind,
                    "source_handle": handle, "instance_path": list(instance_path), "layer": layer}
            if kind == "INSERT":
                name = entity.dxf.name
                if name in block_stack or len(block_stack) > 16:
                    warnings.append(f"Skipped recursive or deeply nested INSERT {handle}")
                    continue
                block = document.blocks.get(name)
                if block is None:
                    warnings.append(f"Missing block {name} for INSERT {handle}")
                    continue
                inserts = list(entity.multi_insert()) if entity.mcount > 1 else [entity]
                for index, insert in enumerate(inserts):
                    counts["insert_instances"] += 1
                    matrix = Matrix44.chain(insert.matrix44(), parent_matrix)
                    walk(block, matrix, [*instance_path, f"{handle}[{index}]"], (*block_stack, name), layer)
                continue
            if kind in ("3DSOLID", "BODY", "REGION"):
                counts["solid_instances"] += 1
                try:
                    builder = _parse_acis(entity)
                    index_of = {id(item): i for i, item in enumerate(builder.entities)}
                    seen_edges = set()
                    for face in builder.entities:
                        if _name(face) != "face":
                            continue
                        counts["acis_faces"] += 1
                        face_index = index_of[id(face)]
                        matrix = Matrix44.chain(_body_matrix(face), parent_matrix)
                        for edge in _face_edges(face):
                            if id(edge) in seen_edges:
                                continue
                            seen_edges.add(id(edge))
                            points = _edge_points(edge)
                            if points:
                                add_object(base, f"edge{index_of[id(edge)]}",
                                           {"kind": "polyline", "points": _xyz(points, matrix, factor)},
                                           confidence="source_boundary", source_record=index_of[id(edge)])
                                counts["boundary_curves"] += 1
                            else:
                                unsupported[f"edge:{_name(_data(edge)[6])}"] += 1
                        patch = _surface_patch(face)
                        if patch is None:
                            unsupported[f"face:{_name(_data(face)[5])}"] += 1
                            continue
                        vertices, faces, detail = patch
                        add_object(base, f"face{face_index}",
                                   {"kind": "mesh", "vertices": _xyz(vertices, matrix, factor), "faces": faces},
                                   confidence=detail["confidence"], surface_type=detail["kind"], source_record=face_index)
                        counts["surface_meshes"] += 1
                        if detail.get("points"):
                            # Radii only survive uniform transforms as scalars.
                            basis = [matrix.transform_direction(Vec3(*a)) for a in ((1,0,0),(0,1,0),(0,0,1))]
                            axes = [v.magnitude for v in basis]
                            uniform = (min(axes) > 1e-12
                                       and max(axes) - min(axes) <= max(axes) * 1e-7
                                       and all(abs(basis[i].dot(basis[j])) <= axes[i] * axes[j] * 1e-7
                                               for i, j in ((0,1), (0,2), (1,2))))
                            candidates.append({
                                "id": f'{base["id"]}:axis{face_index}', "source_handle": handle,
                                "instance_path": list(instance_path), "source_record": face_index,
                                "kind": detail["kind"], "points": _xyz(detail["points"], matrix, factor),
                                "geometric_radius": detail["radius"] * axes[0] * factor if uniform else None,
                                "confidence": detail["confidence"], "evidence": detail["evidence"],
                                "semantic_pipe_size_verified": False, "connectivity_verified": False,
                            })
                    counts["parsed_solid_instances"] += 1
                except (ValueError, TypeError, IndexError, AttributeError, ZeroDivisionError, AcisException, ezdxf.DXFError) as error:
                    warnings.append(f"Partial or failed extraction of solid {base['id']}: {error}")
                    counts["failed_solid_instances"] += 1
            elif kind == "LINE":
                add_object(base, "curve", {"kind": "polyline", "points": _xyz([entity.dxf.start, entity.dxf.end], parent_matrix, factor)},
                           confidence="source_curve", pipe_centerline_verified=False)
                counts["explicit_curves"] += 1
            elif kind in ("ARC", "CIRCLE", "ELLIPSE"):
                try:
                    points = list(entity.flattening(0.5 / factor))
                    add_object(base, "curve", {"kind": "polyline", "points": _xyz(points, parent_matrix, factor)},
                               confidence="source_curve", pipe_centerline_verified=False)
                    counts["explicit_curves"] += 1
                except (ValueError, TypeError, AttributeError):
                    unsupported[kind] += 1
            elif kind in ("LWPOLYLINE", "POLYLINE"):
                try:
                    walk(entity.virtual_entities(), parent_matrix, [*instance_path, handle], block_stack, layer)
                except (ValueError, TypeError, AttributeError):
                    unsupported[kind] += 1
            else:
                unsupported[kind] += 1

    walk(document.modelspace(), Matrix44(), [])
    if any(item["confidence"] == "inferred_short_arc" for item in objects):
        warnings.append("Torus bend meshes and axes use the shorter arc between trim circles. These inferred patches need CAD comparison and are not collision solids.")
    if unsupported:
        warnings.append("Some geometry is not reconstructed. See unsupported_geometry counts. Preview bounds cover extracted samples only.")
    vertices = [point for item in objects for point in item["geometry"].get("vertices", item["geometry"].get("points", []))]
    bounds = ({"min": [min(p[i] for p in vertices) for i in range(3)],
               "max": [max(p[i] for p in vertices) for i in range(3)]} if vertices else None)
    return {
        "schema_version": 1, "name": path.stem, "provenance": "cad", "source": str(path.resolve()),
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "source_units": source_units,
        "source_units_header": units_code, "unit_assumption": assumption, "units": "mm",
        "scale_to_mm": factor, "objects": objects, "bounds": bounds,
        "bounds_scope": "Sampled extracted geometry, not a conservative solid envelope",
        "warnings": warnings, "routes": [], "axis_candidates": candidates,
        "route_candidates": _candidate_chains(candidates),
        "counts": dict(counts), "unsupported_geometry": dict(unsupported),
        "pipe_graph_ready": False, "compliance_verified": False,
    }


def _reduce_ring_mesh(item):
    """Reduce only the exact ring topology emitted by this importer.

    No generic mesh simplification is attempted. Unknown topology is returned
    unchanged rather than risking a disconnected or incorrectly indexed mesh.
    """
    if item.get("surface_type") not in {"cylinder", "annulus", "torus"}:
        return None
    geometry = item.get("geometry", {})
    if geometry.get("kind") != "mesh":
        return None
    vertices, faces = geometry.get("vertices", []), geometry.get("faces", [])
    if len(vertices) < 2 * SAMPLES or len(vertices) % SAMPLES:
        return None
    ring_count = len(vertices) // SAMPLES
    if item["surface_type"] in {"cylinder", "annulus"} and ring_count != 2:
        return None
    if len(faces) != 2 * SAMPLES * (ring_count - 1):
        return None
    if any(not isinstance(p, (list, tuple)) or len(p) != 3
           or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in p)
           for p in vertices):
        return None
    # Verify the mesh before interpreting its vertex order as a ring grid.
    for row in range(ring_count - 1):
        for j in range(SAMPLES):
            a = row * SAMPLES + j
            b = row * SAMPLES + (j + 1) % SAMPLES
            c, d = a + SAMPLES, b + SAMPLES
            offset = 2 * (row * SAMPLES + j)
            if faces[offset] != [a, b, c] or faces[offset + 1] != [b, d, c]:
                return None
    rows = list(range(0, ring_count, 3))
    if rows[-1] != ring_count - 1:
        rows.append(ring_count - 1)
    rings = [[vertices[row * SAMPLES + j] for j in range(0, SAMPLES, 4)]
             for row in rows]
    reduced_vertices, reduced_faces = _rings_mesh(rings)
    return {
        **item,
        "geometry": {**geometry, "vertices": reduced_vertices, "faces": reduced_faces},
        "display_detail": "reduced_ring_samples",
    }


def lightweight_preview(result):
    """Return a smaller display-only view of a large import without mutating it.

    Full source geometry and interpretation metadata remain available in the
    original result. Small drawings pass through unchanged. Reported import
    counts still describe the full input. Display counts are under ``preview``.
    """
    objects = result.get("objects", [])
    if len(objects) <= 2000:
        return result
    shown = []
    counts = Counter()
    for item in objects:
        geometry = item.get("geometry", {})
        kind = geometry.get("kind")
        if kind == "polyline" and item.get("type") in {"3DSOLID", "BODY", "REGION"}:
            counts["omitted_solid_boundary_curves"] += 1
            continue
        if kind == "mesh":
            counts["input_mesh_vertices"] += len(geometry.get("vertices", []))
            counts["input_mesh_faces"] += len(geometry.get("faces", []))
            reduced = _reduce_ring_mesh(item)
            displayed = reduced if reduced is not None else item
            counts["reduced_mesh_count" if reduced is not None else "unchanged_mesh_count"] += 1
            counts["displayed_mesh_vertices"] += len(displayed["geometry"].get("vertices", []))
            counts["displayed_mesh_faces"] += len(displayed["geometry"].get("faces", []))
            shown.append(displayed)
        else:
            if kind == "polyline":
                counts["retained_explicit_curves"] += 1
            shown.append(item)
    detail = {
        "mode": "reduced_surfaces", "display_only": True,
        "total_object_count": len(objects), "displayed_object_count": len(shown),
        "circumference_samples": SAMPLES // 4, "along_path_stride": 3,
        **{key: counts[key] for key in (
            "omitted_solid_boundary_curves", "reduced_mesh_count", "unchanged_mesh_count",
            "input_mesh_vertices", "input_mesh_faces", "displayed_mesh_vertices",
            "displayed_mesh_faces", "retained_explicit_curves",
        )},
    }
    warning = (
        "Large drawing preview uses fewer surface samples and omits solid boundary curves. "
        "Both end rings are retained. This reduces display detail only and is not collision geometry. "
        "Import counts and bounds describe the full extracted data. The full geometry download is unchanged."
    )
    return {**result, "objects": shown, "preview": detail,
            "warnings": [*result.get("warnings", []), warning]}
