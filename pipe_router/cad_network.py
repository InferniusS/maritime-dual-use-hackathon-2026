"""Infer review-required multi-component runs from compatible CAD endpoints.

This is a geometric graph, not a semantic piping network. It never traverses an
unmatched component or infers valve behavior. Ambiguous endpoint clusters stop
joining. All member source placements and any small endpoint welds are traced.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import hashlib
import itertools
import math

from . import core

MAX_JOIN_ANGLE_DEGREES = 10.0
DIAMETER_ABSOLUTE_TOLERANCE_MM = 0.0001
DIAMETER_RELATIVE_TOLERANCE = 1e-6


def _source_instances(candidate):
    explicit = candidate.get("source_instances")
    if explicit is None:
        handles = candidate.get("source_handles", [])
        path = candidate.get("instance_path", [])
        explicit = [{"source_handle": handle, "instance_path": path} for handle in handles]
    if not isinstance(explicit, list) or not explicit:
        raise ValueError("Every network member needs traceable source instances")
    keys = set()
    for item in explicit:
        if not isinstance(item, dict):
            raise ValueError("Invalid candidate source instance")
        handle, path = item.get("source_handle"), item.get("instance_path", [])
        if not isinstance(handle, str) or not handle or not isinstance(path, (list, tuple)) or not all(isinstance(v, str) for v in path):
            raise ValueError("Invalid candidate source handle or instance path")
        keys.add((handle, tuple(path)))
    return tuple(sorted(keys))


def _source_records(keys):
    return [{"source_handle": handle, "instance_path": list(path)} for handle, path in keys]


def _outward_tangent(points, endpoint):
    origin = points[0 if endpoint == 0 else -1]
    sequence = points[1:] if endpoint == 0 else list(reversed(points[:-1]))
    for neighbor in sequence:
        delta = [a - b for a, b in zip(origin, neighbor)]
        length = math.sqrt(sum(value * value for value in delta))
        if length > 1e-10:
            return [value / length for value in delta]
    return None


def _geometry_fingerprint(member):
    forward = tuple(tuple(round(v, 7) for v in point) for point in member["points"])
    backward = tuple(reversed(forward))
    return member["sources"], round(member["diameter"], 7), min(forward, backward)


def inspect_network(cad, tolerance_mm=1.0):
    """Return only unambiguous open chains of at least two source candidates.

    Coincident candidates are deduplicated only when they refer to the same
    source instances and geometry. Different placements never share identity.
    The endpoint tolerance is an explicit prototype setting in millimetres.
    """
    core._mapping(cad, "cad")
    if cad.get("units") != "mm":
        raise ValueError("Network inspection requires CAD geometry already converted to mm")
    tolerance = core._number(tolerance_mm, "tolerance_mm", positive=True)
    raw_candidates = cad.get("route_candidates")
    if not isinstance(raw_candidates, list):
        raise ValueError("CAD route_candidates must be a list")
    members, fingerprints, id_fingerprints = [], {}, {}
    duplicates, invalid = [], []
    for raw in sorted(raw_candidates, key=lambda value: str(value.get("id", "")) if isinstance(value, dict) else ""):
        try:
            core._mapping(raw, "candidate")
            identifier = raw.get("id")
            if not isinstance(identifier, str) or not identifier:
                raise ValueError("Candidate id is missing")
            points = raw.get("points")
            if not isinstance(points, (list, tuple)) or not 2 <= len(points) <= core.MAX_ROUTE_POINTS:
                raise ValueError("Candidate route needs valid points")
            points = [list(core._point(point, "candidate point")) for point in points]
            if math.dist(points[0], points[-1]) <= 1e-10:
                raise ValueError("Closed or zero-length candidate is not an open network member")
            member = {"id": identifier, "points": points,
                      "diameter": core._number(raw.get("geometric_diameter"), "geometric_diameter", positive=True),
                      "sources": _source_instances(raw), "raw": raw}
            if not all(_outward_tangent(points, endpoint) is not None for endpoint in (0, 1)):
                raise ValueError("Candidate has no usable endpoint tangent")
            fingerprint = _geometry_fingerprint(member)
            if identifier in id_fingerprints and id_fingerprints[identifier] != fingerprint:
                raise ValueError("The same candidate id refers to different geometry")
            id_fingerprints[identifier] = fingerprint
            if fingerprint in fingerprints:
                duplicates.append({"candidate_id": identifier, "retained_id": fingerprints[fingerprint]})
                continue
            fingerprints[fingerprint] = identifier
            members.append(member)
        except ValueError as error:
            invalid.append({"candidate_id": raw.get("id") if isinstance(raw, dict) else None, "reason": str(error)})
    # An ambiguous repeated id must not leave its first interpretation active.
    ambiguous_ids = {item["candidate_id"] for item in invalid if "same candidate id" in item["reason"]}
    members = [member for member in members if member["id"] not in ambiguous_ids]
    endpoints = []
    for index, member in enumerate(members):
        for side in (0, 1):
            endpoints.append({"member": index, "side": side,
                              "point": member["points"][0 if side == 0 else -1],
                              "tangent": _outward_tangent(member["points"], side)})
    parents = list(range(len(endpoints)))

    def find(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(a, b):
        a, b = find(a), find(b)
        if a != b:
            parents[b] = a

    cells = defaultdict(list)
    for index, endpoint in enumerate(endpoints):
        cell = tuple(math.floor(value / tolerance) for value in endpoint["point"])
        for offset in itertools.product((-1, 0, 1), repeat=3):
            other_cell = tuple(a + b for a, b in zip(cell, offset))
            for other in cells[other_cell]:
                if math.dist(endpoint["point"], endpoints[other]["point"]) <= tolerance:
                    union(index, other)
        cells[cell].append(index)
    clusters = defaultdict(list)
    for index in range(len(endpoints)):
        clusters[find(index)].append(index)
    junctions, links = [], {}
    accepted = 0
    for cluster in sorted(clusters.values(), key=lambda items: min(items)):
        if len(cluster) < 2:
            continue
        record = {
            "id": f"junction-{len(junctions) + 1}",
            "point": [sum(endpoints[i]["point"][axis] for i in cluster) / len(cluster) for axis in range(3)],
            "endpoints": [{"candidate_id": members[endpoints[i]["member"]]["id"],
                           "side": "start" if endpoints[i]["side"] == 0 else "end",
                           "point": list(endpoints[i]["point"])} for i in cluster],
            "accepted": False,
        }
        if len(cluster) != 2:
            record["reason"] = "branch_or_ambiguous_cluster"
        else:
            a, b = (endpoints[i] for i in cluster)
            member_a, member_b = members[a["member"]], members[b["member"]]
            diameter_tolerance = max(DIAMETER_ABSOLUTE_TOLERANCE_MM,
                                     max(member_a["diameter"], member_b["diameter"]) * DIAMETER_RELATIVE_TOLERANCE)
            dot = sum(x * y for x, y in zip(a["tangent"], b["tangent"]))
            angle = math.degrees(math.acos(max(-1.0, min(1.0, -dot))))
            gap = math.dist(a["point"], b["point"])
            record.update({"gap_mm": gap, "tangent_misalignment_degrees": angle,
                           "diameter_tolerance_mm": diameter_tolerance})
            if a["member"] == b["member"] or set(member_a["sources"]) & set(member_b["sources"]):
                record["reason"] = "same_source_instance"
            elif abs(member_a["diameter"] - member_b["diameter"]) > diameter_tolerance:
                record["reason"] = "diameter_mismatch"
            elif angle > MAX_JOIN_ANGLE_DEGREES:
                record["reason"] = "tangent_mismatch"
            else:
                record.update({"accepted": True, "reason": "compatible_geometric_endpoints",
                               "weld_method": "midpoint_within_tolerance", "max_endpoint_adjustment_mm": gap / 2})
                first, second = cluster
                links[first] = (second, record)
                links[second] = (first, record)
                accepted += 1
        junctions.append(record)
    # Candidate components have degree at most two because ambiguous junctions
    # never receive links. Trace from open ends and omit closed cycles.
    adjacency = defaultdict(set)
    for endpoint, (other, _) in links.items():
        adjacency[endpoints[endpoint]["member"]].add(endpoints[other]["member"])
    visited, chains, cycles = set(), [], []
    for seed in range(len(members)):
        if seed in visited or seed not in adjacency:
            continue
        component, pending = set(), [seed]
        while pending:
            current = pending.pop()
            if current in component:
                continue
            component.add(current)
            pending.extend(adjacency[current] - component)
        visited.update(component)
        open_ends = [2 * member + side for member in sorted(component) for side in (0, 1)
                     if 2 * member + side not in links]
        if len(open_ends) != 2:
            cycles.append([members[i]["id"] for i in sorted(component)])
            continue
        entrance = min(open_ends, key=lambda i: (members[endpoints[i]["member"]]["id"], endpoints[i]["side"]))
        member_order, points, joins, used = [], [], [], set()
        while True:
            endpoint = endpoints[entrance]
            index, side = endpoint["member"], endpoint["side"]
            if index in used:
                break
            used.add(index)
            member_order.append(index)
            section = deepcopy(members[index]["points"])
            if side == 1:
                section.reverse()
            if points:
                weld = list(joins[-1]["point"])
                points[-1] = weld
                section[0] = weld
                points.extend(section[1:])
            else:
                points.extend(section)
            exit_endpoint = 2 * index + 1 - side
            if exit_endpoint not in links:
                break
            entrance, record = links[exit_endpoint]
            joins.append(deepcopy(record))
        if len(used) != len(component) or len(used) < 2:
            continue
        chosen = [members[i] for i in member_order]
        keys = tuple(sorted({key for member in chosen for key in member["sources"]}))
        member_ids = [member["id"] for member in chosen]
        signature = "\n".join(sorted(member_ids))
        identifier = "network:" + hashlib.sha256(signature.encode()).hexdigest()[:16]
        path_set = {key[1] for key in keys}
        inferred_bends = [member["id"] for member in chosen if member["raw"].get("contains_inferred_bend")]
        length = sum(math.dist(a, b) for a, b in zip(points, points[1:]))
        warnings = [
            "This chain is inferred from geometric endpoints. Pipe identity, required valves, branch completeness and actual port connections are unverified.",
            "Matching endpoints do not establish an engineered connection. Unknown components are not traversed or silently bridged.",
            "Unmatched ends and ambiguous endpoint clusters stop the chain. Branches absent from extracted candidates cannot be detected.",
            "Internal endpoints within the selected tolerance are welded to their midpoint. Original member coordinates remain recorded.",
            "Torus members may contain inferred shorter arcs. Verify the full run in the source CAD before design changes.",
        ]
        chain = {
            "id": identifier, "name": f"Connected candidate run with {len(chosen)} components",
            "points": points, "geometric_diameter": chosen[0]["diameter"],
            "source_instances": _source_records(keys), "member_ids": member_ids,
            "member_count": len(chosen), "geometric_length_mm": length,
            "member_diameters": [{"candidate_id": member["id"], "diameter_mm": member["diameter"]} for member in chosen],
            "join_records": joins, "source_records": [],
            "original_member_routes": [{"candidate_id": member["id"], "points": deepcopy(member["points"]),
                                         "source_instances": _source_records(member["sources"]),
                                         "source_records": deepcopy(member["raw"].get("source_records", []))} for member in chosen],
            "confidence": "inferred_endpoint_network", "requires_review": True,
            "semantic_pipe_size_verified": False, "connectivity_verified": False,
            "required_components_verified": False, "branch_completeness_verified": False,
            "contains_inferred_bend": bool(inferred_bends),
            "inferred_bend_component_count": len(inferred_bends), "inferred_bend_member_ids": inferred_bends,
            "original_bend_count_verified": False, "endpoint_tolerance_mm": tolerance,
            "warnings": warnings,
        }
        if len(path_set) == 1:
            chain["source_handles"] = [key[0] for key in keys]
            chain["instance_path"] = list(next(iter(path_set)))
        chains.append(chain)
    chains.sort(key=lambda chain: (-chain["geometric_length_mm"], chain["id"]))
    counts = {
        "input_candidates": len(raw_candidates), "usable_members": len(members),
        "duplicate_candidates": len(duplicates), "invalid_candidates": len(invalid),
        "endpoint_clusters": len(clusters), "candidate_junctions": len(junctions),
        "accepted_joins": accepted, "rejected_junctions": len(junctions) - accepted,
        "branch_or_ambiguous_junctions": sum(j["reason"] == "branch_or_ambiguous_cluster" for j in junctions),
        "chains": len(chains), "members_in_chains": sum(chain["member_count"] for chain in chains),
        "closed_components_omitted": len(cycles),
    }
    return {
        "units": "mm", "source_sha256": cad.get("source_sha256"), "chains": chains,
        "junctions": junctions, "counts": counts, "duplicate_candidates": duplicates,
        "invalid_candidates": invalid, "closed_components": cycles,
        "settings": {"endpoint_tolerance_mm": tolerance, "maximum_join_angle_degrees": MAX_JOIN_ANGLE_DEGREES,
                     "diameter_absolute_tolerance_mm": DIAMETER_ABSOLUTE_TOLERANCE_MM,
                     "diameter_relative_tolerance": DIAMETER_RELATIVE_TOLERANCE},
        "warnings": [
            "Geometric network inference only. Every chain requires source-CAD review.",
            "Endpoint matches require distinct source placements, almost equal inferred diameter and opposing tangents.",
            "Branches and ambiguous endpoint clusters stop joining. Unknown or unmatched components are never automatically crossed.",
            "This graph cannot certify required valves, component identity, hidden branches or system functionality.",
        ],
    }
