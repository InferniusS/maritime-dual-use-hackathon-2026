"""Per-span fabrication takeoff, with explicit distinction between plan and approval.

All lengths are global-WCS millimetres. A connected system is never treated as
one manufactured piece. Generated cuts are proposals; undocumented existing
welds, material/process approval, and catalog provenance remain unverified.
"""
from __future__ import annotations

from copy import deepcopy
import math
from typing import Any

_EPS = 1e-7
MAX_MANUFACTURING_PIECES_PER_SPAN = 256
MAX_MANUFACTURING_PIECES = 1024
MAX_MANUFACTURING_POINTS = 16384
MAX_MANUFACTURING_WELDS_PER_SPAN = 4096
MAX_MANUFACTURING_WELDS = 8192


class ManufacturingBudgetExceeded(ValueError):
    """Input is valid geometry but its takeoff exceeds the bounded workload."""


def _budget_error(message):
    raise ManufacturingBudgetExceeded(f"Manufacturing resource budget exceeded: {message}")


def _check(identifier, status, message, evidence=None, component_ids=None):
    return {"id": identifier, "status": status, "severity": "hard", "message": message,
            "evidence": evidence or {}, "component_ids": component_ids or []}


def _limits():
    # This indirection uses the same versioned source as independent validation.
    from .network_standards import requirement
    return {"max_length_mm": requirement("manufacturing.length")["value"],
            "min_weld_spacing_mm": requirement("manufacturing.weld_spacing")["value"],
            "max_bends": requirement("manufacturing.bends")["value"],
            "cutback_increment_deg": requirement("manufacturing.cutback")["value"]}


def _length(points):
    return sum(math.dist(a, b) for a, b in zip(points, points[1:]))


def _point_on(points, distance):
    if distance <= _EPS:
        return list(points[0])
    travelled = 0.0
    for a, b in zip(points, points[1:]):
        length = math.dist(a, b)
        if travelled + length >= distance - _EPS and length:
            fraction = max(0.0, min(1.0, (distance - travelled) / length))
            return [x + (y - x) * fraction for x, y in zip(a, b)]
        travelled += length
    return list(points[-1])


def _slice(points, start, end):
    result, station = [_point_on(points, start)], 0.0
    for a, b in zip(points, points[1:]):
        station += math.dist(a, b)
        if start + _EPS < station < end - _EPS:
            result.append(list(b))
    last = _point_on(points, end)
    if math.dist(result[-1], last) > _EPS:
        result.append(last)
    return result


def _angles(points):
    result, station = [], 0.0
    for index in range(1, len(points) - 1):
        a, b, c = points[index - 1:index + 2]
        first, second = math.dist(a, b), math.dist(b, c)
        station += first
        if first <= _EPS or second <= _EPS:
            continue
        dot = sum((b[i] - a[i]) * (c[i] - b[i]) for i in range(3)) / (first * second)
        angle = math.degrees(math.acos(max(-1.0, min(1.0, dot))))
        if angle > 1e-5:
            result.append({"vertex_index": index, "station_mm": station, "angle_deg": angle})
    return result


def _planarity(points):
    """Maximum distance from the first non-collinear plane; collinear is planar."""
    origin, normal = points[0], None
    for index in range(1, len(points) - 1):
        a = [points[index][i] - origin[i] for i in range(3)]
        b = [points[index + 1][i] - origin[i] for i in range(3)]
        cross = [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]
        norm = math.sqrt(sum(item*item for item in cross))
        if norm > _EPS:
            normal = [item / norm for item in cross]
            break
    if normal is None:
        return 0.0
    return max(abs(sum((point[i] - origin[i]) * normal[i] for i in range(3))) for point in points)


def _physical_path(span):
    """Develop a sampled physical bend path with analytic station lengths.

    Each primitive preserves its exact length. Coordinates on arcs are computed
    from their actual circle, rather than cutting corners with straight chords.
    """
    from .network_fittings import fitting_envelopes
    assessment = fitting_envelopes(span)
    points = span["points"]
    by_vertex = {fitting["vertex_index"]: fitting for fitting in assessment["fittings"]}
    primitives, cursor = [], list(points[0])
    complete = not assessment.get("issues")
    for corner in _angles(points):
        index = corner["vertex_index"]
        fitting = by_vertex.get(index)
        if not fitting or not fitting.get("radius_mm") or not fitting.get("tangent_start") or not fitting.get("tangent_end"):
            complete = False
            vertex = list(points[index])
            primitives.append({"kind": "straight", "start": cursor, "end": vertex, "length_mm": math.dist(cursor, vertex)})
            cursor = vertex
            primitives.append({"kind": "unverified_bend", "start": vertex, "end": vertex, "length_mm": 0.0, "vertex_index": index})
            continue
        start, end = list(fitting["tangent_start"]), list(fitting["tangent_end"])
        if math.dist(cursor, start) > _EPS:
            primitives.append({"kind": "straight", "start": cursor, "end": start, "length_mm": math.dist(cursor, start)})
        radius, angle = float(fitting["radius_mm"]), math.radians(float(fitting["angle_deg"]))
        incoming = [(points[index][i] - points[index - 1][i]) / math.dist(points[index], points[index - 1]) for i in range(3)]
        outgoing = [(points[index + 1][i] - points[index][i]) / math.dist(points[index + 1], points[index]) for i in range(3)]
        cosine = sum(incoming[i]*outgoing[i] for i in range(3))
        sine = math.sqrt(max(0.0, 1.0-cosine*cosine))
        if sine <= _EPS:
            complete = False
            primitives.append({"kind": "straight", "start": start, "end": end, "length_mm": math.dist(start, end)})
        else:
            inward = [(outgoing[i]-cosine*incoming[i])/sine for i in range(3)]
            center = [start[i] + radius*inward[i] for i in range(3)]
            radial = [(start[i]-center[i])/radius for i in range(3)]
            primitives.append({"kind": "bend", "start": start, "end": end, "length_mm": radius*angle,
                               "radius_mm": radius, "angle_rad": angle, "center": center,
                               "radial": radial, "tangent": incoming, "fitting_id": fitting["id"]})
        cursor = end
    if math.dist(cursor, points[-1]) > _EPS:
        primitives.append({"kind": "straight", "start": cursor, "end": list(points[-1]), "length_mm": math.dist(cursor, points[-1])})
    station = 0.0
    for primitive in primitives:
        primitive["start_station_mm"] = station
        station += primitive["length_mm"]
        primitive["end_station_mm"] = station
    return primitives, assessment, complete


def _primitive_point(primitive, station):
    fraction = max(0.0, min(1.0, (station-primitive["start_station_mm"])/primitive["length_mm"]))
    if primitive["kind"] == "straight":
        return [a+(b-a)*fraction for a, b in zip(primitive["start"], primitive["end"])]
    angle = primitive["angle_rad"]*fraction
    return [primitive["center"][i] + primitive["radius_mm"]*(primitive["radial"][i]*math.cos(angle)+primitive["tangent"][i]*math.sin(angle)) for i in range(3)]


def _path_slice(primitives, start, end):
    coordinates, arcs = [], []
    for primitive in primitives:
        low, high = max(start, primitive["start_station_mm"]), min(end, primitive["end_station_mm"])
        if high <= low+_EPS:
            continue
        count = max(1, math.ceil(math.degrees(primitive.get("angle_rad", 0))/5))
        positions = [low+(high-low)*index/count for index in range(count+1)]
        for position in positions:
            point = _primitive_point(primitive, position)
            if not coordinates or math.dist(coordinates[-1], point) > _EPS:
                coordinates.append(point)
        if primitive["kind"] == "bend":
            arcs.append({key: deepcopy(primitive[key]) for key in ("fitting_id", "radius_mm", "center", "start", "end", "length_mm")})
    return coordinates, arcs


def _generated_pieces(span, primitives, limits, physical_verified, piece_budget=MAX_MANUFACTURING_PIECES_PER_SPAN):
    """Cut only on straights, preserving entire bends in one piece."""
    total = primitives[-1]["end_station_mm"] if primitives else 0.0
    maximum, bend_max, spacing = limits["max_length_mm"], limits["max_bends"], limits["min_weld_spacing_mm"]
    if total > maximum*piece_budget+_EPS:
        _budget_error(f"span needs more than {piece_budget} pieces at the maximum manufacturing length")
    cuts, failures, start = [0.0], [], 0.0
    declared_welds = (span.get("manufacturing") or {}).get("welds") or []
    existing_stations = [weld["station_mm"] for weld in declared_welds if isinstance(weld, dict) and isinstance(weld.get("station_mm"), (int, float)) and math.isfinite(weld["station_mm"])]
    while start < total-_EPS:
        if len(cuts)-1 >= piece_budget:
            _budget_error(f"span exceeds {piece_budget} manufacturing pieces")
        upper = min(total, start+maximum)
        bends = [item for item in primitives if item["kind"] in ("bend", "unverified_bend") and item["start_station_mm"] >= start-_EPS]
        if len(bends) > bend_max and bends[bend_max]["start_station_mm"] < upper:
            upper = bends[bend_max]["start_station_mm"]
        if upper >= total-_EPS:
            cuts.append(total)
            break
        options = []
        for item in primitives:
            if item["kind"] != "straight":
                continue
            # Keep proposed welds off fitting tangencies and prior cuts. The
            # standard controls weld-to-weld spacing; the tangent margin is an
            # explicit conservative planning assumption, not an added standard.
            low = max(start+spacing, item["start_station_mm"]+spacing)
            high = min(upper, item["end_station_mm"]-spacing)
            while high >= low-_EPS:
                conflicts = [station for station in existing_stations if _EPS < abs(high-station) < spacing-_EPS]
                if not conflicts:
                    break
                # A known existing weld is an explicit fabrication boundary.
                # Reuse it when it is in range; otherwise move the new cut back.
                reuse = [station for station in conflicts if low-_EPS <= station <= high+_EPS]
                high = max(reuse) if reuse else min(conflicts)-spacing
            if high >= low-_EPS:
                options.append(high)
        if not options:
            failures.append("No straight cut location meets the piece/bend limit and conservative tangent margin")
            cuts.append(total)
            break
        start = max(options)
        cuts.append(start)
        if len(cuts)-1 > piece_budget:
            _budget_error(f"span exceeds {piece_budget} manufacturing pieces")
    pieces = []
    for index, (low, high) in enumerate(zip(cuts, cuts[1:]), 1):
        points, arcs = _path_slice(primitives, low, high)
        welds = []
        for station in (low, high):
            if _EPS < station < total-_EPS:
                welds.append({"id": f"{span['id']}:cut:{cuts.index(station)}", "station_mm": station,
                              "position": _primitive_point(next(p for p in primitives if p["start_station_mm"]-_EPS <= station <= p["end_station_mm"]+_EPS), station),
                              "status": "proposed"})
        pieces.append({"id": f"{span['id']}:piece:{index}", "span_id": span["id"], "cad_handle": span.get("cad_handle"),
                       "system_id": span.get("system_id"), "nominal_diameter_mm": span.get("nominal_diameter_mm"),
                       "points": points, "length_mm": high-low, "length_basis": "physical developed centerline" if physical_verified else "provisional centerline",
                       "start_station_mm": low, "end_station_mm": high, "bend_count": sum(p["kind"] in ("bend", "unverified_bend") and low-_EPS <= p["start_station_mm"] < high-_EPS for p in primitives), "arcs": arcs, "welds": welds,
                       "verification_status": "UNVERIFIED", "status": "proposed",
                       "notes": ["Fabrication/cut proposal only. Verify material, process, existing welds and approved component catalog before manufacture.",
                                 "Piece takeoff includes bend lengths. Listed elbows are constituent fitting requirements, not additional pipe length."]})
    return pieces, failures, cuts


def _metadata(span, weld_budget=MAX_MANUFACTURING_WELDS_PER_SPAN):
    metadata = span.get("manufacturing")
    if metadata is None:
        return {}
    if not isinstance(metadata, dict):
        raise ValueError("manufacturing must be an object")
    point_count, weld_count = 0, 0
    def welds(records):
        nonlocal weld_count
        if records is None:
            return
        if not isinstance(records, list):
            raise ValueError("Weld metadata must be a list")
        weld_count += len(records)
        if weld_count > weld_budget:
            _budget_error(f"weld metadata exceeds the remaining {weld_budget}-record budget")
        for weld in records:
            if not isinstance(weld, dict):
                raise ValueError("Each weld must be an object")
            if "id" in weld and (not isinstance(weld["id"],str) or not weld["id"] or len(weld["id"]) > 256):
                raise ValueError("Weld identifiers must be non-empty text up to 256 characters")
            station = weld.get("station_mm")
            if not isinstance(station, (int,float)) or isinstance(station,bool) or not math.isfinite(station) or station < 0:
                raise ValueError("Explicit welds require finite non-negative developed station_mm")
    welds(metadata.get("welds"))
    pieces = metadata.get("pieces", [])
    if not isinstance(pieces, list):
        raise ValueError("Manufacturing pieces must be a list")
    if len(pieces) > MAX_MANUFACTURING_PIECES_PER_SPAN:
        _budget_error(f"supplied span exceeds {MAX_MANUFACTURING_PIECES_PER_SPAN} pieces")
    for piece in pieces:
        if not isinstance(piece, dict):
            raise ValueError("Each manufacturing piece must be an object")
        if "id" in piece and (not isinstance(piece["id"],str) or not piece["id"] or len(piece["id"]) > 256):
            raise ValueError("Manufacturing piece identifiers must be non-empty text up to 256 characters")
        points = piece.get("points")
        if not isinstance(points, list) or not 2 <= len(points) <= 4096:
            raise ValueError("Each manufactured piece needs 2 to 4096 explicit global points")
        point_count += len(points)
        for point in points:
            if not isinstance(point,(list,tuple)) or len(point) != 3 or not all(isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and abs(value) <= 1e9 for value in point):
                raise ValueError("Manufacturing piece coordinates must be finite XYZ numbers")
        for key in ("start_station_mm","end_station_mm"):
            value = piece.get(key)
            if value is not None and (not isinstance(value,(int,float)) or isinstance(value,bool) or not math.isfinite(value) or value < 0):
                raise ValueError("Manufacturing piece stations must be finite and non-negative")
        welds(piece.get("welds"))
    if point_count > 10000:
        raise ValueError("Manufacturing metadata exceeds the 10000-point budget")
    for key in ("planarity_required","process_verified"):
        if key in metadata and not isinstance(metadata[key],bool):
            raise ValueError(f"manufacturing.{key} must be boolean")
    return metadata


def _point_at_station(primitives, station):
    for primitive in primitives:
        if primitive["length_mm"] > _EPS and primitive["start_station_mm"]-_EPS <= station <= primitive["end_station_mm"]+_EPS:
            return _primitive_point(primitive,station)
    return primitives[-1]["end"]


def _locate_station(primitives, point):
    candidates = []
    for primitive in primitives:
        if primitive["length_mm"] <= _EPS:
            continue
        if primitive["kind"] == "straight":
            vector = [b-a for a,b in zip(primitive["start"],primitive["end"])]
            fraction = sum((value-base)*delta for value,base,delta in zip(point,primitive["start"],vector))/sum(delta*delta for delta in vector)
            if not -_EPS <= fraction <= 1+_EPS:
                continue
            station = primitive["start_station_mm"]+max(0,min(1,fraction))*primitive["length_mm"]
        else:
            relative = [value-center for value,center in zip(point,primitive["center"])]
            angle = math.atan2(sum(a*b for a,b in zip(relative,primitive["tangent"])),sum(a*b for a,b in zip(relative,primitive["radial"])))
            if not -_EPS <= angle <= primitive["angle_rad"]+_EPS:
                continue
            station = primitive["start_station_mm"]+max(0,min(primitive["angle_rad"],angle))*primitive["radius_mm"]
        if math.dist(_primitive_point(primitive,station),point) <= 1e-4:
            if not any(abs(station-value) < 1e-4 for value in candidates):
                candidates.append(station)
    return candidates[0] if len(candidates) == 1 else None


def manufacture_network(network: dict[str, Any]) -> dict[str, Any]:
    """Plan/evaluate pieces independently for each span without modifying input."""
    if network.get("units") != "mm":
        raise ValueError("Manufacturing requires normalized millimetre geometry")
    limits = _limits()
    all_pieces, all_fittings, all_welds, checks = [], [], [], []
    output_point_count, input_weld_count = 0, 0
    for span in network.get("spans", []):
        span_id = span["id"]
        manufacturing = _metadata(span,min(MAX_MANUFACTURING_WELDS_PER_SPAN,MAX_MANUFACTURING_WELDS-input_weld_count))
        input_weld_count += len(manufacturing.get("welds") or [])+sum(len(piece.get("welds") or []) for piece in manufacturing.get("pieces",[]))
        existing = manufacturing.get("pieces") or []
        remaining_pieces = MAX_MANUFACTURING_PIECES-len(all_pieces)
        piece_budget = min(MAX_MANUFACTURING_PIECES_PER_SPAN,remaining_pieces)
        if piece_budget <= 0 or len(existing) > piece_budget:
            _budget_error(f"network exceeds {MAX_MANUFACTURING_PIECES} pieces")
        if not existing and len(_angles(span["points"])) > limits["max_bends"]*piece_budget:
            _budget_error(f"bend count requires more than {piece_budget} pieces")
        primitives, fitting_result, physical_known = _physical_path(span)
        impossible_fittings=[issue for issue in fitting_result["issues"] if not issue["code"].startswith("missing_")]
        missing_fittings=[issue for issue in fitting_result["issues"] if issue["code"].startswith("missing_")]
        checks.append(_check("manufacturing.fitting_geometry",
                             "FAIL" if impossible_fittings else "UNVERIFIED" if missing_fittings else "PASS",
                             "Physical fitting takeouts, supported angles and body dimensions must fit before manufacture",
                             {"violations":impossible_fittings,"unverified":missing_fittings},[span_id]))
        fittings = deepcopy(fitting_result["fittings"])
        represented = {fitting["vertex_index"] for fitting in fittings}
        for corner in _angles(span["points"]):
            if corner["vertex_index"] not in represented:
                fittings.append({"id": f"{span_id}:elbow:{corner['vertex_index']}", "span_id": span_id,
                                 "vertex_index": corner["vertex_index"], "intersection": list(span["points"][corner["vertex_index"]]),
                                 "angle_deg": corner["angle_deg"], "radius_mm": None, "source": None,
                                 "catalog_status": "missing", "capsules": []})
        for fitting in fittings:
            fitting.update({"component_type": "elbow", "system_id": span.get("system_id"),
                            "cad_handle": span.get("cad_handle"), "nominal_diameter_mm": span.get("nominal_diameter_mm"),
                            "verification_status": "PASS" if fitting_result.get("catalog_verified") and fitting.get("radius_mm") else "UNVERIFIED"})
        all_fittings.extend(fittings)
        if existing:
            planned, failures, cuts = [], [], []
        else:
            planned, failures, cuts = _generated_pieces(span, primitives, limits, physical_known, piece_budget)
        pieces, mapped_welds, mapping_failures = [], [], []
        all_mapped = bool(existing) and physical_known
        if existing:
            for index, raw in enumerate(existing, 1):
                piece = deepcopy(raw)
                points = piece.get("points")
                if not isinstance(points, list) or not 2 <= len(points) <= 4096:
                    raise ValueError(f"Manufactured piece {span_id}:{index} needs explicit global points")
                local_path, local_fittings, local_known = _physical_path({**span,"points":points})
                low, high = _locate_station(primitives,points[0]), _locate_station(primitives,points[-1])
                mapped = low is not None and high is not None and high > low and local_known and physical_known
                local_length = local_path[-1]["end_station_mm"] if local_path else 0
                if mapped and abs((high-low)-local_length) > 1e-4:
                    mapped = False
                if mapped:
                    for primitive in local_path:
                        for fraction in (0,.5,1):
                            station = primitive["start_station_mm"]+fraction*primitive["length_mm"]
                            if math.dist(_primitive_point(primitive,station),_point_at_station(primitives,low+station)) > 1e-4:
                                mapped = False
                                break
                if mapped:
                    for key, actual in (("start_station_mm",low),("end_station_mm",high)):
                        if key in piece and abs(piece[key]-actual) > 1e-4:
                            mapping_failures.append({"piece_id":piece.get("id",index),"reason":"Declared piece station differs from global geometry"})
                    piece["start_station_mm"],piece["end_station_mm"] = low,high
                    for weld in piece.get("welds",[]):
                        if weld["station_mm"] > local_length+_EPS:
                            mapping_failures.append({"piece_id":piece.get("id",index),"reason":"Piece-local weld outside physical piece"})
                        mapped_welds.append(low+weld["station_mm"])
                all_mapped = all_mapped and mapped
                piece.update({"id": piece.get("id", f"{span_id}:piece:{index}"), "span_id": span_id,
                              "length_mm": local_length if local_known else _length(points), "bend_count": len(_angles(points)), "status": "supplied",
                              "verification_status": "UNVERIFIED", "system_id": span.get("system_id"),
                              "nominal_diameter_mm": span.get("nominal_diameter_mm"), "cad_handle": span.get("cad_handle"),
                              "notes": ["Supplied fabrication piece; verify process and catalog approval."]})
                pieces.append(piece)
        else:
            pieces = planned
        output_point_count += sum(len(piece["points"]) for piece in pieces)
        if output_point_count > MAX_MANUFACTURING_POINTS:
            _budget_error(f"network exceeds {MAX_MANUFACTURING_POINTS} manufactured-piece coordinates")
        all_pieces.extend(pieces)
        if all_mapped:
            intervals = sorted((piece["start_station_mm"],piece["end_station_mm"]) for piece in pieces)
            previous = 0.0
            for low,high in intervals:
                if abs(low-previous) > 1e-4:
                    mapping_failures.append({"reason":"Supplied pieces leave a gap or overlap","previous_end_mm":previous,"next_start_mm":low})
                previous = high
            if abs(previous-(primitives[-1]["end_station_mm"] if primitives else 0)) > 1e-4:
                mapping_failures.append({"reason":"Supplied pieces do not cover the full physical span"})
        coverage_status = "FAIL" if mapping_failures else "PASS" if all_mapped or (not existing and physical_known) else "UNVERIFIED"
        checks.append(_check("manufacturing.coverage", coverage_status,
                             "Supplied piece coverage requires developed-station/connection confirmation" if existing else "Generated pieces partition the complete physical span without gaps",
                             {"piece_count": len(pieces), "plan_status": "supplied" if existing else "proposed", "mapping_failures":mapping_failures}, [span_id]))
        physical_status = "PASS" if physical_known and (not existing or all_mapped) else "UNVERIFIED"
        lengths = [{"piece_id": piece["id"], "length_mm": piece["length_mm"]} for piece in pieces]
        longest = max((item["length_mm"] for item in lengths), default=0)
        length_status = "FAIL" if longest > limits["max_length_mm"]+_EPS or failures else physical_status
        checks.append(_check("manufacturing.length", length_status, "Individual manufactured piece lengths, not total network length", {"max_length_mm": limits["max_length_mm"], "pieces": lengths, "plan_failures": failures, "plan_status": "supplied" if existing else "proposed"}, [span_id]))
        maximum_bends = max((piece["bend_count"] for piece in pieces), default=0)
        checks.append(_check("manufacturing.bends", "FAIL" if maximum_bends > limits["max_bends"] else physical_status,
                             "Bend count evaluated per manufactured piece", {"max_bends": limits["max_bends"], "pieces": [{"piece_id": p["id"], "bend_count": p["bend_count"]} for p in pieces]}, [span_id]))
        source_welds = manufacturing.get("welds")
        total = primitives[-1]["end_station_mm"] if primitives else 0
        # Flatten independently located welds for CAD/Excel schedules. Missing
        # or invalid physical geometry never produces invented coordinates.
        source_stations=[float(weld["station_mm"]) for weld in source_welds or []]+mapped_welds
        proposed_stations=[] if existing else [station for station in cuts[1:-1]
                                              if not any(abs(station-known)<=_EPS for known in source_stations)]
        for index,(station,kind) in enumerate([(station,"supplied") for station in source_stations]+
                                            [(station,"proposed") for station in proposed_stations],1):
            mapped=bool(primitives) and physical_known and -_EPS<=station<=total+_EPS
            all_welds.append({"id":f"{span_id}:weld:{index}","span_id":span_id,
                              "system_id":span.get("system_id"),"cad_handle":span.get("cad_handle"),
                              "station_mm":station,"position":_point_at_station(primitives,station) if mapped else None,
                              "status":kind,"verification_status":"PASS" if mapped else "UNVERIFIED",
                              "verification_scope":"Developed-station coordinate only; spacing and process are separate checks"})
        if source_welds is None and not (existing and all_mapped and all("welds" in piece for piece in pieces)):
            checks.append(_check("manufacturing.weld_spacing", "UNVERIFIED", "Existing weld positions are absent; proposed cut spacing is not proof of existing weld compliance", {"minimum_mm": limits["min_weld_spacing_mm"], "proposed_cut_stations_mm": cuts[1:-1], "proposed_cut_spacing_mm": [b-a for a,b in zip(cuts[1:-1], cuts[2:-1])]}, [span_id]))
        else:
            stations = []
            for weld in source_welds or []:
                station = weld.get("station_mm")
                if not isinstance(station, (int, float)) or not math.isfinite(station) or station < 0:
                    raise ValueError("Explicit welds require finite non-negative developed station_mm")
                stations.append(float(station))
            stations.extend(mapped_welds)
            proposed_new_stations = [] if existing else [station for station in cuts[1:-1] if not any(abs(station-declared) <= _EPS for declared in stations)]
            stations.extend(proposed_new_stations)
            stations.sort()
            spacings = [b-a for a,b in zip(stations, stations[1:])]
            total = primitives[-1]["end_station_mm"] if primitives else 0
            outside = any(station > total+_EPS for station in stations)
            status = "FAIL" if outside or mapping_failures or any(value < limits["min_weld_spacing_mm"]-_EPS for value in spacings) else "PASS"
            if not physical_known and status == "PASS":
                status = "UNVERIFIED"
            checks.append(_check("manufacturing.weld_spacing", status, "Weld-to-weld developed centerline distances", {"minimum_mm": limits["min_weld_spacing_mm"], "stations_mm": stations, "spacings_mm": spacings, "outside_span": outside, "includes_proposed_cuts": not bool(existing)}, [span_id]))
        angles = _angles(span["points"])
        bad_angles = [item for item in angles if abs(item["angle_deg"]/limits["cutback_increment_deg"]-round(item["angle_deg"]/limits["cutback_increment_deg"])) > 1e-5]
        checks.append(_check("manufacturing.cutback", "FAIL" if bad_angles else ("PASS" if angles else "NOT_APPLICABLE"), "Elbow cutback angles must use the configured increments", {"increment_deg": limits["cutback_increment_deg"], "angles_deg": [item["angle_deg"] for item in angles], "invalid_angles": bad_angles}, [span_id]))
        if manufacturing.get("planarity_required") is True:
            deviations = [{"piece_id": piece["id"], "out_of_plane_mm": _planarity(piece["points"])} for piece in pieces]
            tolerance = manufacturing.get("planarity_tolerance_mm")
            if tolerance is None:
                status = "UNVERIFIED"
            elif not isinstance(tolerance, (int, float)) or isinstance(tolerance,bool) or not math.isfinite(tolerance) or tolerance < 0:
                raise ValueError("Planarity tolerance must be finite and non-negative")
            else:
                status = "FAIL" if any(item["out_of_plane_mm"] > tolerance+_EPS for item in deviations) else "PASS"
            checks.append(_check("manufacturing.planarity", status, "Transportation planarity evaluated per piece; acceptance tolerance requires designer confirmation", {"tolerance_mm": tolerance, "deviations": deviations}, [span_id]))
        else:
            checks.append(_check("manufacturing.planarity", "NOT_APPLICABLE" if manufacturing.get("planarity_required") is False else "UNVERIFIED", "Planarity requirement not specified" if "planarity_required" not in manufacturing else "No planarity requirement declared for these pieces", {}, [span_id]))
        checks.append(_check("manufacturing.process", "PASS" if manufacturing.get("process_verified") is True and fitting_result.get("catalog_verified") else "UNVERIFIED", "Material, fabrication process and approved fitting catalog must be confirmed independently", {"process_verified": manufacturing.get("process_verified", False), "catalog_verified": fitting_result.get("catalog_verified", False)}, [span_id]))
    return {"pieces": all_pieces, "fittings": all_fittings, "welds": all_welds, "checks": checks, "limits": limits,
            "notes": ["Generated cuts are proposed fabrication boundaries. No industrial approval is inferred from geometric limits."]}
