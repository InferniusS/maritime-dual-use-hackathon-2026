"""Physical tangent elbows and bounded conservative capsule sweeps, in mm.

Circular centerline arcs are enclosed by chords expanded by their exact sagitta.
Catalog dimensions are not inferred from nominal pipe size. These are conservative
occupancy envelopes, not commercial fitting solids or fabrication approval.
"""
from __future__ import annotations
from collections import OrderedDict
from copy import deepcopy
import hashlib
import json
import math
import sys
import threading

MAX_FITTING_CACHE_ENTRIES = 256
MAX_FITTING_CACHE_BYTES = 2_000_000
_CACHE = OrderedDict()
_CACHE_LOCK = threading.Lock()
_CACHE_BYTES = 0
_CACHE_HITS = 0
_CACHE_MISSES = 0


def clear_geometry_cache():
    global _CACHE_BYTES, _CACHE_HITS, _CACHE_MISSES
    with _CACHE_LOCK:
        _CACHE.clear(); _CACHE_BYTES = _CACHE_HITS = _CACHE_MISSES = 0


def geometry_cache_stats():
    with _CACHE_LOCK:
        return {"entries":len(_CACHE),"retained_bytes":_CACHE_BYTES,"hits":_CACHE_HITS,"misses":_CACHE_MISSES,
                "max_entries":MAX_FITTING_CACHE_ENTRIES,"max_retained_bytes":MAX_FITTING_CACHE_BYTES,
                "scope":"Physical elbow geometry only; no engineering validation results are cached"}


def _retained_size(value, limit):
    seen, pending, size=set(),[value],0
    while pending:
        item=pending.pop()
        if id(item) in seen:continue
        seen.add(id(item));size+=sys.getsizeof(item)
        if size>limit:return size
        if isinstance(item,dict):pending.extend(item.keys());pending.extend(item.values())
        elif isinstance(item,(list,tuple)):pending.extend(item)
    return size


def _sub(a, b): return [x-y for x, y in zip(a, b)]
def _add(a, b): return [x+y for x, y in zip(a, b)]
def _mul(a, s): return [x*s for x in a]
def _dot(a, b): return sum(x*y for x, y in zip(a, b))


def corner_angle(a, b, c):
    u, v = _sub(b, a), _sub(c, b)
    lengths = math.dist(a, b)*math.dist(b, c)
    return math.degrees(math.acos(max(-1, min(1, _dot(u, v)/lengths)))) if lengths else 0.0


def _fitting_envelopes(span):
    """Extract catalog tangent elbows, swept occupancy, and tangent deficiencies.

    Missing radius leaves the fitting envelope unknown; it is never replaced by
    a nominal-size assumption. Explicit provisional catalogs remain provisional.
    """
    points = span.get("points", [])
    outer = span.get("outer_diameter_mm")
    catalog = span.get("fitting_catalog") or {}
    radius = catalog.get("radius_mm")
    body = catalog.get("body_radius_mm")
    catalog_verified = catalog.get("status") == "verified" and bool(catalog.get("source")) and catalog.get("available") is not False
    fittings, issues = [], []
    takeouts = [0.0]*len(points)
    for index in range(1, len(points)-1):
        a, b, c = points[index-1:index+2]
        turn = corner_angle(a, b, c)
        if turn < 1e-6:
            continue
        identifier = f'{span.get("id", "span")}:elbow:{index}'
        if turn >= 179.999:
            issues.append({"code":"reversal", "vertex_index":index, "angle_deg":turn})
            continue
        if radius is None or not isinstance(radius, (int, float)) or isinstance(radius, bool) or not math.isfinite(radius) or radius <= 0:
            issues.append({"code":"missing_fitting_radius", "vertex_index":index})
            continue
        if body is None or not isinstance(body, (int, float)) or isinstance(body, bool) or not math.isfinite(body) or body <= 0:
            issues.append({"code":"missing_fitting_body", "vertex_index":index})
            continue
        if outer is not None and body < outer/2:
            issues.append({"code":"fitting_body_smaller_than_pipe", "vertex_index":index, "body_radius_mm":body, "pipe_radius_mm":outer/2})
        if catalog.get("available") is False:
            issues.append({"code":"unavailable_catalog_fitting", "vertex_index":index, "source":catalog.get("source")})
        supported = catalog.get("angles_deg")
        # Cutback capability must be explicit. A 90-degree elbow does not imply
        # approval to manufacture every smaller turn.
        if not supported:
            issues.append({"code":"missing_catalog_angles", "vertex_index":index, "angle_deg":turn})
        elif not any(abs(turn-value) <= 1e-5 for value in supported) and not (
                catalog.get("cutback_allowed") is True and any(turn < value for value in supported)):
            issues.append({"code":"unsupported_catalog_angle", "vertex_index":index, "angle_deg":turn})
        u, v = _sub(b, a), _sub(c, b)
        u, v = _mul(u, 1/math.dist(a, b)), _mul(v, 1/math.dist(b, c))
        theta = math.radians(turn)
        takeout = radius*math.tan(theta/2)
        takeouts[index] = takeout
        tangent_start, tangent_end = _sub(b, _mul(u, takeout)), _add(b, _mul(v, takeout))
        normal = _mul(_sub(v, _mul(u, math.cos(theta))), 1/math.sin(theta))
        center = _add(tangent_start, _mul(normal, radius))
        radial = _mul(_sub(tangent_start, center), 1/radius)
        subdivisions = max(1, math.ceil(turn/5.0))
        arc_points = [_add(center, _add(_mul(radial, radius*math.cos(theta*j/subdivisions)), _mul(u, radius*math.sin(theta*j/subdivisions)))) for j in range(subdivisions+1)]
        sagitta = radius*(1-math.cos(theta/subdivisions/2))
        capsules = [{"start":left,"end":right,"radius":body+sagitta,"kind":"fitting","component_id":identifier} for left,right in zip(arc_points,arc_points[1:])]
        fittings.append({"id":identifier,"span_id":span.get("id"),"vertex_index":index,"intersection":list(b),"center":center,"angle_deg":turn,"radius_mm":radius,"takeout_mm":takeout,"body_radius_mm":body,"tangent_start":tangent_start,"tangent_end":tangent_end,"capsules":capsules,"catalog_status":catalog.get("status","missing"),"source":catalog.get("source"),"developed_length_mm":radius*theta})
    straights = []
    for index, (a,b) in enumerate(zip(points,points[1:])):
        length = math.dist(a,b)
        required = takeouts[index]+takeouts[index+1]
        record = {"segment_index":index,"available_mm":length,"required_tangents_mm":required,"between_bends":takeouts[index]>0 and takeouts[index+1]>0,"net_straight_mm":length-required}
        straights.append(record)
        if length <= 1e-9:
            issues.append({"code":"zero_length_segment", **record})
        elif length+1e-6 < required:
            issues.append({"code":"insufficient_tangent_length", **record})
    return {"fittings":fittings,"issues":issues,"straight_lengths":straights,"catalog_verified":catalog_verified}


def fitting_envelopes(span):
    """Return isolated geometry from a bounded, physical-input-keyed cache."""
    global _CACHE_BYTES, _CACHE_HITS, _CACHE_MISSES
    physical={key:span.get(key) for key in ("id","points","outer_diameter_mm","fitting_catalog")}
    key=hashlib.sha256(json.dumps(physical,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).digest()
    with _CACHE_LOCK:
        entry=_CACHE.get(key)
        if entry is not None:
            _CACHE.move_to_end(key);_CACHE_HITS+=1
            return deepcopy(entry[0])
        _CACHE_MISSES+=1
    result=_fitting_envelopes(span)
    size=_retained_size((key,result),MAX_FITTING_CACHE_BYTES)+256
    if size<=MAX_FITTING_CACHE_BYTES:
        with _CACHE_LOCK:
            if key not in _CACHE:
                while _CACHE and (len(_CACHE)>=MAX_FITTING_CACHE_ENTRIES or _CACHE_BYTES+size>MAX_FITTING_CACHE_BYTES):
                    _,(_,old_size)=_CACHE.popitem(last=False);_CACHE_BYTES-=old_size
                _CACHE[key]=(deepcopy(result),size);_CACHE_BYTES+=size
    return result


def span_occupied_segments(span, data=None):
    """Capsules for trimmed pipe straights and physical fitting sweeps.

    If fitting inputs are absent, retain the full original centerline occupancy
    and let independent validation report the missing envelope explicitly.
    """
    data = fitting_envelopes(span) if data is None else data
    fittings = {item["vertex_index"]:item for item in data["fittings"]}
    invalid = any(issue["code"] in {"insufficient_tangent_length","reversal"} for issue in data["issues"])
    result = []
    radius = (span.get("outer_diameter_mm") or 0)/2
    for index,(a,b) in enumerate(zip(span.get("points",[]),span.get("points",[])[1:])):
        start = fittings[index]["tangent_end"] if index in fittings and not invalid else list(a)
        end = fittings[index+1]["tangent_start"] if index+1 in fittings and not invalid else list(b)
        result.append({"start":start,"end":end,"radius":radius,"kind":"pipe","component_id":span.get("id"),"segment_index":index})
    for fitting in data["fittings"]:
        result.extend(fitting["capsules"])
    return result
