"""Bounded topology/spatial large-neighborhood search for industrial networks.

This module proposes local subproblems, never certifies an engineering design.
Every observation comes from the independent complete-network validator. An
invalid repair incumbent is strictly internal and can never become a proposal.
"""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import hashlib
import json
import math

NEIGHBORHOOD_SIZE = 8
MAX_NEIGHBORHOOD_SIZE = 16
MAX_LOCAL_GRID_NODES = 50000
MAX_GEOMETRY_CACHE_BYTES = 4*1024*1024
MAX_GEOMETRY_CACHE_ENTRIES = 256


def violation_count(validation):
    """Count measured violations, rather than just aggregated failing checks."""
    return sum(max(1, len(check.get("evidence", {}).get("violations", [])))
               for check in validation.get("checks", []) if check.get("severity") == "hard" and check.get("status") == "FAIL")


def repair_rank(validation, score):
    return (violation_count(validation), validation["hard_failures"], score["objective"])


def _box(span, margin=50.8):
    catalog = span.get("fitting_catalog") or {}
    padding = max(span["outer_diameter_mm"]/2, catalog.get("body_radius_mm") or 0, catalog.get("radius_mm") or 0)+margin
    return {"min": [min(p[i] for p in span["points"])-padding for i in range(3)],
            "max": [max(p[i] for p in span["points"])+padding for i in range(3)]}


def boxes_overlap(left, right):
    return all(left["min"][i] <= right["max"][i] and right["min"][i] <= left["max"][i] for i in range(3))


def component_obstacles(component, span, clearance_mm=50.8):
    """Retain material outside a strict subset of an explicit support contact.

    The contact sphere is bounded by supplied hardware and pipe dimensions.
    A rectangular cut remains wholly inside that sphere; thin support cross
    sections permit a taller axial cut without excluding unrelated material.
    The attachment and every returning route primitive are independently checked.
    """
    if component["min"] is None:
        return []
    boxes = [{"min":list(component["min"]), "max":list(component["max"])}]
    from .network_components import support_contact_radius
    radius = support_contact_radius(component, span, clearance_mm)
    center = component.get("contact_position")
    contact = component["type"] == "support" and span["id"] in component.get("incident_span_ids", []) and isinstance(radius, (int, float)) and not isinstance(radius, bool) and math.isfinite(radius) and 0 < radius <= 1000 and isinstance(center, (list, tuple)) and len(center) == 3
    if contact:
        uniform = radius/math.sqrt(3.)
        extents = [max(abs(center[i]-component["min"][i]), abs(component["max"][i]-center[i])) for i in range(3)]
        axial = max(range(3), key=lambda axis:extents[axis])
        halves = [min(uniform, extent) for extent in extents]
        halves[axial] = math.sqrt(max(0., radius*radius-sum(half*half for axis,half in enumerate(halves) if axis != axial)))
        cube = {"min":[p-half for p,half in zip(center,halves)], "max":[p+half for p,half in zip(center,halves)]}
        if boxes_overlap(boxes[0], cube):
            remaining = deepcopy(boxes[0])
            boxes = []
            for axis in range(3):
                if remaining["min"][axis] < cube["min"][axis]:
                    slab = deepcopy(remaining)
                    slab["max"][axis] = min(remaining["max"][axis], cube["min"][axis])
                    if all(a < b for a,b in zip(slab["min"],slab["max"])):
                        boxes.append(slab)
                if remaining["max"][axis] > cube["max"][axis]:
                    slab = deepcopy(remaining)
                    slab["min"][axis] = max(remaining["min"][axis], cube["max"][axis])
                    if all(a < b for a,b in zip(slab["min"],slab["max"])):
                        boxes.append(slab)
                remaining["min"][axis] = max(remaining["min"][axis], cube["min"][axis])
                remaining["max"][axis] = min(remaining["max"][axis], cube["max"][axis])
                if any(a >= b for a,b in zip(remaining["min"],remaining["max"])):
                    break
    result = []
    for index, box in enumerate(boxes):
        item = {"id":f"network-component:{component['id']}:{index}", **box, "geometry_note":component["source"]}
        if contact:
            item["geometry_note"] = "Conservative material outside an inscribed rectangular contact bounded by actual support/pipe dimensions; independent attachment and returning-leg validation remain required"
            item["applied_contact_radius_mm"] = radius
        if component["representation"] == "capsules":
            caps = []
            for cap in component["capsules"]:
                low = [max(box["min"][i], min(cap["start"][i],cap["end"][i])-cap["radius"]) for i in range(3)]
                high = [min(box["max"][i], max(cap["start"][i],cap["end"][i])+cap["radius"]) for i in range(3)]
                if all(a <= b for a,b in zip(low,high)):
                    capsule = deepcopy(cap)
                    if contact:
                        capsule.update(clip_min=low, clip_max=high)
                    caps.append(capsule)
            if not caps:
                continue
            item["capsules"] = caps
        result.append(item)
    return result


def interaction_graph(network, identifiers):
    spans = {s["id"]: s for s in network["spans"] if s["id"] in identifiers}
    boxes = {identifier: _box(span) for identifier, span in spans.items()}
    graph = {identifier: {} for identifier in spans}
    topology_edges, spatial_edges = 0, 0
    for index, (identifier, span) in enumerate(spans.items()):
        for other_id, other in list(spans.items())[index+1:]:
            connected = bool({span["from_node"], span["to_node"]} & {other["from_node"], other["to_node"]})
            spatial = boxes_overlap(boxes[identifier], boxes[other_id])
            if connected or spatial:
                weight = 4*int(connected)+int(spatial)
                graph[identifier][other_id] = graph[other_id][identifier] = weight
                topology_edges += int(connected)
                spatial_edges += int(spatial)
    return graph, {"topology_edges": topology_edges, "spatial_edges": spatial_edges, "selected_spans": len(spans),
                   "spatial_scope": "Conservative pipe/fitting bounding-box interactions; complete independent narrowphase remains authoritative"}


def affected_span_ids(network, validation):
    known = {s["id"] for s in network["spans"]}
    incident = {n["id"]: {s["id"] for s in network["spans"] if n["id"] in (s["from_node"], s["to_node"])} for n in network["nodes"]}
    affected = set()
    def add(identifier):
        if identifier in known:
            affected.add(identifier)
        affected.update(incident.get(identifier, set()))
    for check in validation.get("checks", []):
        if check.get("status") != "FAIL" or check.get("severity") != "hard":
            continue
        for identifier in check.get("component_ids", []):
            add(identifier)
        for row in check.get("evidence", {}).get("violations", []):
            if isinstance(row, dict):
                for key in ("id", "span_id", "other_id", "component_id"):
                    add(row.get(key))
    return affected


class NeighborhoodSearch:
    """Maintain a private monotone repair incumbent and bounded neighborhoods."""
    def __init__(self, network, identifiers, validation, score):
        self.source = network
        self.identifiers = list(identifiers)
        self.spans = {s["id"]: s for s in network["spans"]}
        self.graph, self.graph_summary = interaction_graph(network, identifiers)
        self.incumbent, self.validation, self.score = network, validation, score
        self.rank = repair_rank(validation, score)
        self.accepted_repairs = 0
        self.completed_neighborhoods = 0
        self.active_index = 0
        self.neighborhoods = []
        self._geometry_cache = OrderedDict()
        self.geometry_cache_bytes = self.geometry_cache_hits = self.geometry_cache_misses = 0
        self.affected = affected_span_ids(network, validation)
        self.priorities = {}
        for identifier in identifiers:
            span = self.spans[identifier]
            length = sum(math.dist(a, b) for a, b in zip(span["points"], span["points"][1:]))
            direct = max(1., math.dist(span["points"][0], span["points"][-1]))
            self.priorities[identifier] = (identifier not in self.affected, -length/direct, -len(self.graph[identifier]), identifier)
        self.primary = self._cover()
        self.total_neighborhoods = len(self.primary)

    def _priority(self, identifier):
        return self.priorities[identifier]

    def _expand(self, seed, available, maximum):
        group = [seed]
        candidates = set(available)-{seed}
        distances, frontier = {seed:0}, [seed]
        while frontier:
            current = frontier.pop(0)
            for neighbor in self.graph[current]:
                if neighbor not in distances:
                    distances[neighbor] = distances[current]+1
                    frontier.append(neighbor)
        while candidates and len(group) < maximum:
            touching = [identifier for identifier in candidates if any(identifier in self.graph[old] for old in group)]
            if not touching:
                # Earlier neighborhoods can separate the remaining graph into
                # islands. Their fixed connecting context stays in the model;
                # fill the local budget using nearest topology/spatial islands
                # rather than spawning one full-network evaluation per flange.
                touching = list(candidates)
            # Keep direct spatial competitors in the same subproblem before
            # following a long chain of graph-adjacent, distant pipe sections.
            origin = self.spans[seed]["points"][0]
            identifier = min(touching, key=lambda item: (distances.get(item, 1000), -sum(self.graph[old].get(item, 0) for old in group),
                              math.dist(origin, self.spans[item]["points"][0]), self._priority(item)))
            group.append(identifier)
            candidates.remove(identifier)
        return group

    def _cover(self):
        remaining, groups = set(self.identifiers), []
        while remaining:
            seed = min(remaining, key=self._priority)
            group = self._expand(seed, remaining, NEIGHBORHOOD_SIZE)
            groups.append(group)
            remaining.difference_update(group)
        return groups

    def observe(self, candidate, validation, score, physical_geometry_known):
        rank = repair_rank(validation, score)
        if physical_geometry_known and rank < self.rank:
            self.incumbent, self.validation, self.score, self.rank = candidate, validation, score, rank
            self.affected = affected_span_ids(candidate, validation)
            self.accepted_repairs += 1
            return True
        return False

    def can_preserve_straight(self, network, span):
        """Reuse independently checked, unaffected direct geometry conservatively."""
        old = next(s for s in self.incumbent["spans"] if s["id"] == span["id"])
        if len(span["points"]) != 2 or span["points"] != old["points"] or span["id"] in self.affected:
            return False
        old_nodes = {n["id"]: n["position"] for n in self.incumbent["nodes"]}
        if any(n["position"] != old_nodes[n["id"]] for n in network["nodes"]):
            return False
        old_spans = {s["id"]: s for s in self.incumbent["spans"]}
        extent = _box(span)
        return not any(other["points"] != old_spans[other["id"]]["points"] and boxes_overlap(extent, _box(other)) for other in network["spans"])

    def cached_obstacle(self, other, span, network, build):
        common = {other["from_node"], other["to_node"]} & {span["from_node"], span["to_node"]}
        key = hashlib.sha256(json.dumps([other, span["from_node"], span["to_node"], span["outer_diameter_mm"],
                 [node for node in network["nodes"] if node["id"] in common]], sort_keys=True, allow_nan=False).encode()).hexdigest()
        if key in self._geometry_cache:
            self.geometry_cache_hits += 1
            item, weight = self._geometry_cache[key]
            self._geometry_cache.move_to_end(key)
            return deepcopy(item)
        self.geometry_cache_misses += 1
        item = build(other, span, network)
        weight = len(json.dumps(item, allow_nan=False).encode())
        if weight <= MAX_GEOMETRY_CACHE_BYTES//4:
            self._geometry_cache[key] = (deepcopy(item), weight)
            self.geometry_cache_bytes += weight
        while len(self._geometry_cache) > MAX_GEOMETRY_CACHE_ENTRIES or self.geometry_cache_bytes > MAX_GEOMETRY_CACHE_BYTES:
            _, (_, old_weight) = self._geometry_cache.popitem(last=False)
            self.geometry_cache_bytes -= old_weight
        return item

    def _node_variants(self, group):
        yield self.incumbent, None
        moved = {}
        for node in self.incumbent["nodes"]:
            if node["fixed"] or not node.get("movement_region"):
                continue
            incident = {s["id"] for s in self.incumbent["spans"] if node["id"] in (s["from_node"], s["to_node"])}
            if not incident or not incident <= set(group):
                continue
            options = node.get("allowed_positions") or [[(a+b)/2 for a, b in zip(node["movement_region"]["min"], node["movement_region"]["max"])]]
            alternative = next((p for p in options if math.dist(p, node["position"]) > 1e-6), None)
            if alternative is not None:
                moved[node["id"]] = list(alternative)
        if moved:
            variant = deepcopy(self.incumbent)
            for node in variant["nodes"]:
                if node["id"] in moved:
                    node["position"] = moved[node["id"]]
            for span in variant["spans"]:
                if span["from_node"] in moved:
                    span["points"][0] = moved[span["from_node"]]
                if span["to_node"] in moved:
                    span["points"][-1] = moved[span["to_node"]]
            yield variant, sorted(moved)

    def plans(self, routing_orders):
        groups = [(group, "primary") for group in self.primary]
        index = 0
        while index < len(groups):
            group, phase = groups[index]
            self.active_index = index+1
            item = {"index": index+1, "span_ids": list(group), "phase": phase, "evaluations": 0,
                    "fixed_boundary_node_ids": sorted(n["id"] for n in self.source["nodes"] if
                        any(s["id"] in group for s in self.source["spans"] if n["id"] in (s["from_node"], s["to_node"])) and
                        any(s["id"] not in group for s in self.source["spans"] if n["id"] in (s["from_node"], s["to_node"])))}
            self.neighborhoods.append(item)
            # Base variants are fetched from the latest independently checked
            # incumbent. Permitted component moves are bounded to this group.
            for variant, moved in self._node_variants(group):
                for mode in ("retain_existing", "prioritized_reservations"):
                    for order in routing_orders(variant, group)[:2]:
                        base = variant if moved else self.incumbent
                        yield base, moved, order, mode, item
            self.completed_neighborhoods += 1
            index += 1
            if index == len(self.primary):
                # Target remaining measured conflicts across primary boundaries.
                # Larger overlapping neighborhoods permit coordinated repairs
                # involving two prior groups, without freeing the whole model.
                failing = affected_span_ids(self.incumbent, self.validation) & set(self.identifiers)
                seeds = sorted(failing, key=self._priority)[:4]
                seen = {frozenset(g) for g, _ in groups}
                for seed in seeds:
                    expanded = self._expand(seed, set(self.identifiers), MAX_NEIGHBORHOOD_SIZE)
                    if frozenset(expanded) not in seen:
                        groups.append((expanded, "conflict_repair"))
                        seen.add(frozenset(expanded))
                self.total_neighborhoods = len(groups)


def _safe_grid_step(bounds, requested, target):
    widths = [b-a for a, b in zip(bounds["min"], bounds["max"])]
    step = max(float(requested), max(widths)/470.)
    for _ in range(16):
        counts = [max(4, math.ceil(width/step)+5) for width in widths]
        if max(counts) <= 480 and math.prod(counts) <= target:
            return step, math.prod(counts)
        step *= max(1.15, (math.prod(counts)/target)**(1/3))
    raise ValueError("Could not establish a bounded local routing grid")


def spatial_search_stages(layout, waypoints=()):
    """Conservative local windows with memory-preflighted coarse/fine fallback."""
    original = layout["bounds"]
    points = [*layout["pipe"]["original_route"], *waypoints]
    requested = layout["rules"]["grid_step"]
    radius = layout["rules"].get("bend_radius", 0.)
    padding = max(2000., 3*radius, 2*requested)
    stages, seen = [], set()
    for scale, resolution, target in ((1., 2., 25000), (1., 1., MAX_LOCAL_GRID_NODES), (3., 1., MAX_LOCAL_GRID_NODES)):
        bounds = {"min": [max(original["min"][i], min(p[i] for p in points)-padding*scale) for i in range(3)],
                  "max": [min(original["max"][i], max(p[i] for p in points)+padding*scale) for i in range(3)]}
        step, estimated_nodes = _safe_grid_step(bounds, requested*resolution, target)
        key = tuple(bounds["min"]+bounds["max"]+[step])
        if key in seen:
            continue
        seen.add(key)
        local = deepcopy(layout)
        local["bounds"], local["rules"]["grid_step"] = bounds, step
        # Every omitted obstacle has a certified disjoint conservative envelope
        # from the entire searched box, rather than a distance-to-center guess.
        local["obstacles"] = [deepcopy(o) for o in layout["obstacles"] if boxes_overlap(o, bounds)]
        stages.append((local, {"stage": len(stages)+1, "grid_step_mm": step, "estimated_grid_nodes_upper_bound": estimated_nodes,
                               "grid_node_budget": target, "bounds": bounds, "active_obstacles": len(local["obstacles"]),
                               "estimated_direction_states_upper_bound":6*estimated_nodes+1,
                               "estimated_search_memory_bytes":4096*estimated_nodes,
                               "memory_estimate_scope":"4 KiB planning allowance per geometric grid node for lazy direction states, heap and edge caches; measured peak memory is reported by benchmark tooling",
                               "excluded_disjoint_obstacles": len(layout["obstacles"])-len(local["obstacles"]),
                               "scope": "Local permitted subset of source bounds; all intersecting supplied finite obstacles retained"}))
    return stages
