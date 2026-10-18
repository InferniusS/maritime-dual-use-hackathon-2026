"""Declared finite component envelopes, independent of route-search decisions.

Bounds enclose supplied cylindrical dimensions; they are conservative geometry,
not commercial solids, catalog availability, or installation certification.
"""
from __future__ import annotations

from copy import deepcopy
import itertools
import math

from backend.geometry import segment_distance

PHYSICAL_NODE_TYPES = frozenset({"valve", "tee", "junction", "fitting", "flange", "reducer", "elbow", "support"})
_NEW_FINITE_TYPES = frozenset({"flange", "reducer", "elbow", "support"})


def _positive(value):
    return isinstance(value, (int,float)) and not isinstance(value,bool) and math.isfinite(value) and value > 0


def _point(value):
    if not isinstance(value,(list,tuple)) or len(value)!=3 or not all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v) for v in value):
        raise ValueError("Component envelope coordinates require finite global XYZ values")
    return list(value)


def _bounds(capsules):
    return ([min(min(cap["start"][i],cap["end"][i])-cap["radius"] for cap in capsules) for i in range(3)],
            [max(max(cap["start"][i],cap["end"][i])+cap["radius"] for cap in capsules) for i in range(3)])


def _contains(box, point):
    return all(a-1e-6<=v<=b+1e-6 for a,v,b in zip(box[0],point,box[1]))


def _dimension_body(record, position, issues):
    dimensions=record.get("dimensions") or {}
    radius,length=dimensions.get("body_radius_mm"),dimensions.get("body_length_mm")
    if not _positive(radius):
        return None
    axis=record.get("axis")
    if length is not None:
        if not _positive(length):
            issues.append({"code":"invalid_body_length","status":"FAIL"});return None
        if axis is None:
            issues.append({"code":"missing_component_axis","status":"UNVERIFIED"});return None
        axis=_point(axis);norm=math.sqrt(sum(v*v for v in axis))
        if norm<1e-12:
            issues.append({"code":"invalid_component_axis","status":"FAIL"});return None
        axis=[v/norm for v in axis]
        extent=[abs(v)*length/2+radius*math.sqrt(max(0,1-v*v)) for v in axis]
        return {"representation":"aabb","min":[p-r for p,r in zip(position,extent)],
                "max":[p+r for p,r in zip(position,extent)],"capsules":[],
                "source":"Conservative cylinder bounds from supplied body dimensions and WCS axis"}
    if record.get("type") in _NEW_FINITE_TYPES:
        issues.append({"code":"missing_body_length","status":"UNVERIFIED"})
    cap={"start":position,"end":position,"radius":radius,"kind":"fitting","component_id":record["id"]}
    low,high=_bounds([cap])
    return {"representation":"capsules","min":low,"max":high,"capsules":[cap],
            "source":"Supplied conservative spherical body envelope; full component solid not verified"}


def _explicit_body(record, position, issues):
    envelope=record.get("physical_envelope")
    if envelope is None:return None
    representation=envelope.get("representation")
    if not envelope.get("source"):
        issues.append({"code":"missing_envelope_source","status":"UNVERIFIED"})
    if envelope.get("conservative") is not True:
        issues.append({"code":"envelope_conservatism_unconfirmed","status":"UNVERIFIED"})
    anchor=envelope.get("anchor")
    if record.get("fixed") is False and anchor is None:
        issues.append({"code":"movable_envelope_anchor_missing","status":"UNVERIFIED"})
    delta=[0.,0.,0.] if anchor is None else [p-a for p,a in zip(position,_point(anchor))]
    def translated(point):return [v+d for v,d in zip(_point(point),delta)]
    if representation=="aabb":
        low,high=translated(envelope.get("min")),translated(envelope.get("max"))
        if any(a>=b for a,b in zip(low,high)):raise ValueError("Physical component AABB requires min < max")
        return {"representation":"aabb","min":low,"max":high,"capsules":[],"source":envelope.get("source")}
    if representation=="capsules":
        raw=envelope.get("capsules")
        if not isinstance(raw,list) or not 1<=len(raw)<=128:
            raise ValueError("Physical component requires 1 to 128 declared capsules")
        capsules=[]
        for item in raw:
            radius=item.get("radius_mm",item.get("radius"))
            if not _positive(radius):raise ValueError("Physical component capsule requires a positive radius_mm")
            capsules.append({"start":translated(item.get("start")),"end":translated(item.get("end")),
                             "radius":radius,"kind":"fitting","component_id":record["id"]})
        low,high=_bounds(capsules)
        return {"representation":"capsules","min":low,"max":high,"capsules":capsules,"source":envelope.get("source")}
    raise ValueError("Physical component envelope representation must be aabb or capsules")


def component_geometry(network):
    """Return isolated, WCS-mm finite-envelope DTOs for nodes and supports.

    A declared envelope cannot remove known body dimensions. Contradictory input
    fails and retains a conservative union, so an undersized override never hides
    a collision. Unknown geometry is returned with explicit issues and no bounds.
    """
    spans=network.get("spans",[])
    span_map={span["id"]:span for span in spans}
    records=[]
    for node in network.get("nodes",[]):
        if node.get("type") in PHYSICAL_NODE_TYPES or node.get("physical_envelope") is not None:
            incident=[span["id"] for span in spans if node["id"] in (span.get("from_node"),span.get("to_node"))]
            records.append((node,incident,node["id"]))
    for support in network.get("supports",[]):
        if support.get("type")=="support" or support.get("physical_envelope") is not None or support.get("dimensions"):
            record={**support,"id":"support:"+str(support.get("id","unknown")),"type":"support"}
            records.append((record,[support["span_id"]] if support.get("span_id") else [],support.get("id")))
    result=[]
    for record,incident,source_id in records:
        issues=[]
        radius=(record.get("dimensions") or {}).get("body_radius_mm")
        if record.get("type")!="support" and _positive(radius):
            port_outer=(record.get("constraints") or {}).get("port_outer_diameters_mm",{})
            sizes=[port_outer.get(identifier,span_map.get(identifier,{}).get("outer_diameter_mm")) for identifier in incident]
            largest=max((value for value in sizes if _positive(value)),default=None)
            if largest is not None and 2*radius+1e-6<largest:
                issues.append({"code":"component_body_smaller_than_known_connection","status":"FAIL",
                               "body_diameter_mm":2*radius,"largest_pipe_outer_diameter_mm":largest})
        supplied_position=record.get("position",record.get("contact_position",record.get("pipe_center")))
        if supplied_position is None:
            envelope=record.get("physical_envelope") or {}
            supplied_position=envelope.get("anchor")
            if supplied_position is None and envelope.get("representation")=="aabb":
                supplied_position=[(a+b)/2 for a,b in zip(envelope.get("min",[]),envelope.get("max",[]))]
        position=_point(supplied_position) if supplied_position is not None else None
        derived=_dimension_body(record,position,issues) if supplied_position is not None else None
        explicit=_explicit_body(record,position,issues)
        body=explicit or derived
        if explicit and derived:
            corners=list(itertools.product(*zip(derived["min"],derived["max"])))
            contains=all(_contains((explicit["min"],explicit["max"]),point) for point in corners)
            if explicit["representation"]=="capsules":
                # One convex capsule containing the whole known AABB proves
                # containment. A union's bounding box alone does not.
                contains=contains and any(all(float(segment_distance(point,point,cap["start"],cap["end"]))<=cap["radius"]+1e-6 for point in corners) for cap in explicit["capsules"])
            if not contains:
                issues.append({"code":"declared_envelope_does_not_enclose_known_body","status":"FAIL"})
                body={"representation":"aabb","min":[min(a,b) for a,b in zip(explicit["min"],derived["min"])],
                      "max":[max(a,b) for a,b in zip(explicit["max"],derived["max"])],"capsules":[],
                      "source":"Conservative union retained after inconsistent envelope/dimension input"}
        if body is None:
            issues.append({"code":"physical_geometry_missing","status":"UNVERIFIED"})
            body={"representation":"unknown","min":None,"max":None,"capsules":[],"source":None}
        position_basis="supplied" if position is not None else "unknown"
        if position is None and body["min"] is not None:
            position=[(a+b)/2 for a,b in zip(body["min"],body["max"])]
            position_basis="calculated_envelope_center"
        elif position is None:
            issues.append({"code":"component_position_missing","status":"UNVERIFIED"})
        derived_covers=derived is not None and not any(item["code"] in {
            "missing_body_length","missing_component_axis","invalid_body_length","invalid_component_axis"} for item in issues)
        declaration=(record.get("physical_envelope") or {}).get("conservative")
        conservatism=True if derived_covers else declaration if explicit is not None else None
        conservative_basis="supplied_dimensions" if derived_covers else "source_declaration" if declaration is True else "unconfirmed"
        status="FAIL" if any(item["status"]=="FAIL" for item in issues) else "UNVERIFIED" if issues else "PASS"
        system_id=record.get("system_id")
        if system_id is None and len(incident)==1:system_id=span_map.get(incident[0],{}).get("system_id")
        result.append({"id":record["id"],"source_id":source_id,"type":record.get("type"),"position":position,
                       **body,"status":status,"issues":issues,"conservative":conservatism,"conservative_basis":conservative_basis,
                       "declared_conservative":declaration,"exact_solid_verified":False,"position_basis":position_basis,
                       "incident_span_ids":incident,"dimensions":deepcopy(record.get("dimensions",{})),
                       "system_id":system_id,"system_identity_basis":"supplied" if record.get("system_id") is not None else "associated_span" if system_id is not None else "unknown",
                       "constraints":deepcopy(record.get("constraints",{})),"axis":deepcopy(record.get("axis")),
                       "nominal_diameter_mm":record.get("nominal_diameter_mm"),"cad_handle":record.get("cad_handle"),
                       "fitting_catalog":deepcopy(record.get("fitting_catalog")),
                       "contact_position":deepcopy(record.get("contact_position",record.get("constraints",{}).get("contact_position",None if record.get("type")=="support" else position))),
                       "contact_radius_mm":record.get("contact_radius_mm",record.get("constraints",{}).get("contact_radius_mm")),
                       "notes":["Supplied envelope; conservative coverage is reported separately. No exact component solid, stock availability or installation approval inferred."]})
    return result


def connection_contact_radius(node, left_span, right_span, clearance_mm, *, include_fitting_allowance=False):
    """Bound a shared pipe connection exception by its supplied physical body.

    A declared radius is not evidence of a larger component. Planner reservations
    may include their explicitly conservative elbow-body margin; validation uses
    the actual endpoint pipe radii and independently swept fittings.
    """
    def pipe_radius(span):
        radius=(span.get("outer_diameter_mm") or 0)/2
        if include_fitting_allowance:
            radius=max(radius,(span.get("fitting_catalog") or {}).get("body_radius_mm") or 0)
        return radius
    base=pipe_radius(left_span)+pipe_radius(right_span)+clearance_mm
    declared=(node.get("constraints") or {}).get("contact_radius_mm")
    if not _positive(declared) or declared>1000 or declared<=base:return base
    position=_point(node["position"])
    dimensions=node.get("dimensions") or {}
    radius,length=dimensions.get("body_radius_mm"),dimensions.get("body_length_mm")
    # The circumscribed radius of a long thin body would authorize a huge
    # sphere of empty off-axis space. Only a ball contained in supplied body
    # geometry may extend the exception beyond the two physical pipe radii.
    local_radius=min(radius,length/2) if _positive(radius) and _positive(length) else radius if _positive(radius) else 0.
    envelope=node.get("physical_envelope")
    if not local_radius and envelope is not None and envelope.get("conservative") is True and envelope.get("source"):
        body=_explicit_body(node,position,[])
        if body["representation"]=="capsules":
            local_radius=max(0.,max(cap["radius"]-float(segment_distance(position,position,cap["start"],cap["end"])) for cap in body["capsules"]))
        else:local_radius=max(0.,min(min(p-a,b-p) for p,a,b in zip(position,body["min"],body["max"])))
    return declared if local_radius>0 and declared<=local_radius+base+1e-6 else base


def support_contact_radius(component, span, clearance_mm):
    """Bound an attached support's requested contact ball by supplied hardware."""
    pipe_radius=(span.get("outer_diameter_mm") or 0)/2
    radius=(component.get("dimensions") or {}).get("body_radius_mm")
    if not _positive(radius):
        if component.get("capsules"):radius=max(cap["radius"] for cap in component["capsules"])
        elif component.get("min") is not None:radius=min((b-a)/2 for a,b in zip(component["min"],component["max"]))
        else:return 0.
    base=radius+pipe_radius+clearance_mm
    declared=component.get("contact_radius_mm")
    contact=component.get("contact_position")
    if not _positive(declared) or declared>1000 or contact is None or component.get("min") is None:return min(base,1000.)
    if component["representation"]=="capsules":
        local=max(0.,max(cap["radius"]-float(segment_distance(contact,contact,cap["start"],cap["end"])) for cap in component["capsules"]))
    else:local=max(0.,min(min(p-a,b-p) for p,a,b in zip(contact,component["min"],component["max"])))
    return declared if declared<=local+base+1e-6 else min(base,1000.)
