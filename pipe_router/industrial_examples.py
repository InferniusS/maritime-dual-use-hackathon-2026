"""Generate invented, connected industrial assemblies from engineering inputs.

These are source geometries, not optimizer responses. Every counted run joins
two actual assembly components. No drawing, approved commercial catalog or
calculated proposal is embedded in this module.
"""
from __future__ import annotations

from copy import deepcopy
import math

SOURCE = "Invented component dimensions for synthetic verification; no commercial or ship approval"
PROFILE = "fincantieri-track2-user-transcription-v1"


def _component(identifier, kind, position, *, nominal=80, axis=(1, 0, 0),
               radius=110, length=180, system="cooling"):
    node = {"id": identifier, "type": kind, "position": list(position),
            "system_id": system, "fixed": True,
            "cad_handle": f"SYN51-{identifier.upper()}", "instance_path": [],
            "nominal_diameter_mm": nominal, "axis": list(axis),
            "dimensions": {"body_radius_mm": radius, "body_length_mm": length},
            "constraints": {"contact_radius_mm": 250},
            "fitting_catalog": {"status": "provisional", "source": SOURCE,
                                "available": True},
            "provenance": "synthetic"}
    if kind == "valve":
        node.update(stem_orientation="horizontal", stem_height_mm=position[2])
    if kind == "flange":
        node["constraints"].update(installation_clearance_mm=25,
                                   external_assembly_boundary=True,
                                   mating_component_status="UNVERIFIED")
    return node


def _span(identifier, start, end, points, *, nominal=80, system="cooling",
          radius=None, modifiable=True):
    outer = {50: 60, 80: 100}[nominal]
    return {"id": identifier, "from_node": start, "to_node": end,
            "system_id": system, "cad_handle": f"SYN51-{identifier.upper()}",
            "instance_path": [], "points": deepcopy(points),
            "outer_diameter_mm": outer, "nominal_diameter_mm": nominal,
            "modifiable": modifiable, "provenance": "synthetic",
            "fitting_catalog": {"status": "provisional", "source": SOURCE,
                                "available": True, "radius_mm": radius or (200 if nominal == 50 else 250),
                                "body_radius_mm": outer/2 + 5,
                                "angles_deg": [45, 90]},
            "manufacturing": {"planarity_required": False}, "constraints": {}}


def _declare_interfaces(network):
    """Declare each incident interface; only explicit reducing parts change DN."""
    for node in network["nodes"]:
        incident = [span for span in network["spans"]
                    if node["id"] in (span["from_node"], span["to_node"])]
        node["constraints"]["port_nominal_diameters_mm"] = {
            span["id"]: span["nominal_diameter_mm"] for span in incident}
        node["constraints"]["port_outer_diameters_mm"] = {
            span["id"]: span["outer_diameter_mm"] for span in incident}
        if len({span["nominal_diameter_mm"] for span in incident}) > 1:
            node["constraints"].update(
                reducer_approved=True,
                reducer_source="Explicit synthetic designer permission for the supplied reducing component; commercial approval unverified")


def build_manifold(run_count=20, *, identifier=None, branch_detours=True,
                   first_station_mm=3000):
    """Build one connected manifold with exactly ``run_count`` eligible spans.

    Every off-take has a valve, an explicit reducer and a terminal flange. Two
    equipment ports bound the main, and remainder spans add real inline valves
    rather than disconnected padding. Any count from 5 through 80 is supported.
    """
    if isinstance(run_count, bool) or not isinstance(run_count, int) or not 5 <= run_count <= 80:
        raise ValueError("run_count must be an integer from 5 through 80")
    if (isinstance(first_station_mm, bool) or not isinstance(first_station_mm, (int, float))
            or not math.isfinite(first_station_mm) or first_station_mm < 3000):
        raise ValueError("first_station_mm must leave at least 2500 mm before the first tee")
    branches = (run_count - 1) // 4
    inline = run_count - (4 * branches + 1)
    last_x = first_station_mm + (branches-1) * 3000
    end_x = last_x + 3000
    identity = identifier or f"industrial-{run_count}"
    network = {
        "schema_version": "pipeforge.network/4.0", "id": identity,
        "title": f"Connected {run_count}-run machinery manifold",
        "description": "Invented branched assembly with finite valves, reducing tees, reducers, terminal flanges and support envelopes. Every counted run belongs to one connected cooling graph.",
        "provenance": "synthetic", "units": "mm",
        "drawing": {"id": f"SYNTHETIC-V51-{identity.upper()}", "revision": "synthetic-v51-1"},
        "coordinate_system": {"frame": "WCS", "up": [0, 0, 1],
                              "fore_aft": [1, 0, 0], "athwartship": [0, 1, 0]},
        "bounds": {"min": [0, 0, 0], "max": [end_x + 1500, 9000, 4000]},
        "systems": [{"id": "cooling", "type": "pressurized"}],
        "nodes": [], "spans": [], "obstacles": [], "supports": [],
        "compartments": [{"id": "machinery-room", "min": [0, 0, 0],
                          "max": [end_x+1500, 9000, 4000], "provenance": "synthetic"}],
        "penetrations": [], "unresolved_connections": [],
        "coverage": {"complete": True, "omitted_count": 0, "unknown_count": 0,
                     "notes": ["Complete only for this entirely invented assembly; not a ship survey."]},
        "accessibility_coverage": {"complete": False},
        "standards_profile": PROFILE, "search": {"grid_step_mm": 500},
        "assembly_metadata": {"generator": "pipeforge.industrial_examples/5.1",
                              "declared_run_count": run_count,
                              "terminal_scope": "Flanges are external assembly tie-ins. Mating equipment, bolt specification and installation sequence remain unverified.",
                              "catalog_scope": SOURCE},
    }
    nodes, spans = network["nodes"], network["spans"]
    inlet = _component("equipment-in", "equipment_port", [500, 2000, 1500])
    outlet = _component("equipment-out", "equipment_port", [end_x, 2000, 1500])
    nodes.extend([inlet, outlet])
    trunk = [inlet]
    for index in range(branches):
        x = first_station_mm + index * 3000
        prefix = f"branch-{index+1:02d}"
        tee = _component(f"{prefix}-tee", "tee", [x, 2000, 1500], radius=140, length=260)
        valve = _component(f"{prefix}-valve", "valve", [x, 4500, 1500],
                           nominal=50, axis=(0, 1, 0), radius=100, length=200)
        reducer = _component(f"{prefix}-reducer", "reducer", [x, 6000, 1500],
                             axis=(0, 1, 0), radius=110, length=250)
        flange = _component(f"{prefix}-flange", "flange", [x, 7500, 1500],
                            axis=(0, 1, 0), radius=140, length=120)
        nodes.extend([tee, valve, reducer, flange])
        trunk.append(tee)
        route = [tee["position"], valve["position"]]
        if branch_detours:
            route = [tee["position"], [x, 2600, 1500], [x+600, 2600, 1500],
                     [x+600, 3900, 1500], [x, 3900, 1500], valve["position"]]
        feed = _span(f"{prefix}-feed", tee["id"], valve["id"], route, nominal=50)
        transition = _span(f"{prefix}-reducer-inlet", valve["id"], reducer["id"],
                           [valve["position"], reducer["position"]], nominal=50)
        interface = _span(f"{prefix}-flanged-outlet", reducer["id"], flange["id"],
                          [reducer["position"], flange["position"]])
        spans.extend([feed, transition, interface])
        network["supports"].append({
            "id": f"{prefix}-hanger", "type": "support", "span_id": transition["id"],
            "position": [x, 5250, 1800], "axis": [0, 0, 1],
            "dimensions": {"body_radius_mm": 25, "body_length_mm": 600},
            "contact_position": [x, 5250, 1500], "contact_radius_mm": 200,
            "source": "Synthetic supplied hanger envelope; actual catalog and ship anchorage unverified",
            "conservative": True, "provenance": "synthetic"})
    for index in range(inline):
        x = last_x + 3000 * (index+1)/(inline+1)
        valve = _component(f"main-inline-valve-{index+1}", "valve", [x, 2000, 1500])
        nodes.append(valve)
        trunk.append(valve)
    trunk.append(outlet)
    for index, (start, end) in enumerate(zip(trunk, trunk[1:])):
        spans.append(_span(f"main-{index+1:02d}", start["id"], end["id"],
                           [start["position"], end["position"]]))
    _declare_interfaces(network)
    assert len(spans) == run_count
    return network


def _add_firemain(network, *, crossing=False, modifiable=False):
    if crossing:
        points = [[3500, 1000, 1500], [3500, 4000, 1500]]
    else:
        end_x = network["bounds"]["max"][0] - 1500
        points = [[500, 3300, 1500], [end_x, 3300, 1500]]
    network["systems"].append({"id": "firemain", "type": "pressurized"})
    for identifier, point in zip(("firemain-in", "firemain-out"), points):
        network["nodes"].append(_component(identifier, "equipment_port", point,
                                            nominal=50, axis=(0, 1, 0) if crossing else (1, 0, 0),
                                            system="firemain"))
    network["spans"].append(_span("firemain-transfer", "firemain-in", "firemain-out",
                                    points, nominal=50, system="firemain", modifiable=modifiable))
    _declare_interfaces(network)


def industrial_scenarios():
    """Return A–G source inputs, freshly generated with stable synthetic IDs."""
    scenes = {}
    for label, count in (("a", 5), ("b", 10), ("c", 20), ("d", 40)):
        identity = f"{label}-industrial-{count}"
        scene = build_manifold(count, identifier=identity)
        scene["title"] = f"{label.upper()} · {count} connected industrial runs"
        scenes[identity] = scene
    scene = build_manifold(20, identifier="e-congested-assembly")
    _add_firemain(scene)
    scene["title"] = "E · Congested connected machinery assembly"
    scene["description"] = "Twenty connected cooling runs share a compartment with a locked firemain crossing all four branch feeds, finite components, hangers, structure, duct, wireway and maintenance access. Repair all affected branches together without altering fixed equipment or the other system."
    end_x = scene["bounds"]["max"][0] - 1500
    scene["obstacles"] = [
        {"id": "deck", "kind": "deck", "min": [0, 0, 0], "max": [end_x+1500, 9000, 500]},
        {"id": "overhead-duct", "kind": "duct", "min": [2000, 2500, 2500], "max": [end_x, 4100, 3100]},
        {"id": "wireway", "kind": "wireway", "min": [1500, 2900, 3300], "max": [end_x, 3500, 3600]},
        {"id": "maintenance-walkway", "kind": "walkway", "min": [500, 500, 500],
         "max": [end_x, 1200, 2460], "floor_point": [500, 500, 500]},
        {"id": "ladder-access", "kind": "ladder", "min": [end_x+400, 6000, 500], "max": [end_x+1200, 7800, 3000]},
    ]
    for index in range(4):
        x = 3000 + index*3000
        scene["obstacles"].append({"id": f"structure-column-{index+1}", "kind": "structure",
                                    "min": [x+1000, 3600, 500], "max": [x+1400, 5600, 2400]})
    for obstacle in scene["obstacles"]:
        obstacle.update(provenance="synthetic", source_handle=f"SYN51-{obstacle['id'].upper()}")
    scene["accessibility_coverage"] = {"complete": True, "notes": ["Only the invented walkway/ladder volumes are covered; installation sequencing remains unverified."]}
    scenes[scene["id"]] = scene
    scene = build_manifold(20, identifier="f-coordinated-assembly",
                           branch_detours=False, first_station_mm=7500)
    scene["title"] = "F · Connected assembly requiring a coordinated corridor exchange"
    scene["description"] = "Twenty connected cooling runs and a crossing movable firemain. The first cooling run has a declared 600 mm bend radius. Retaining the firemain prevents the short cooling alternative, while coordinated reservation can move both systems. All other cooling spans begin straight and fixed components keep their identity and dimensions."
    first = next(span for span in scene["spans"] if span["id"] == "main-01")
    first["points"] = [[500, 2000, 1500], [1500, 2000, 1500], [1500, 5000, 1500],
                       [6500, 5000, 1500], [6500, 2000, 1500], [7500, 2000, 1500]]
    first["fitting_catalog"]["radius_mm"] = 600
    _add_firemain(scene, crossing=True, modifiable=True)
    scenes[scene["id"]] = scene
    scene = build_manifold(20, identifier="g-blocked-assembly")
    scene["title"] = "G · Connected assembly behind a sealed bulkhead"
    scene["description"] = "A solid full-cross-section bulkhead separates the upstream fixed equipment port from the twenty-run connected assembly. Partial downstream improvements cannot be accepted while the complete network fails. Report bounded unsuccessful search separately from timeout; neither is a general infeasibility proof."
    scene["obstacles"] = [{"id": "sealed-bulkhead", "kind": "bulkhead",
                           "min": [1500, 0, 0], "max": [1750, 9000, 4000],
                           "source_handle": "SYN51-SEALED-BULKHEAD", "provenance": "synthetic"}]
    scenes[scene["id"]] = scene
    return scenes
