"""Explicit unit-aware DXF adapter; engineering semantics come from supplied metadata."""
import io,math
from urllib.parse import quote
from pydantic import BaseModel,ConfigDict,Field
import ezdxf
from .models import Model
from .exports import import_dxf
UNITS={'m':(6,1.),'mm':(4,.001),'cm':(5,.01),'in':(1,.0254),'ft':(2,.3048),'km':(7,1000.)}
class CoordinateTransform(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)
    source_units:str='m'
    meters_per_unit:float=Field(default=1.,gt=0)
    def checked(self):
        if self.source_units not in UNITS or abs(self.meters_per_unit-UNITS[self.source_units][1])>1e-12:raise ValueError('Coordinate scale contradicts declared source units')
        return self.model_dump()

def _point(p,scale):return tuple(float(x)*scale for x in p)
def _source_entity(entity,reason):
    return dict(handle=entity.dxf.handle,entity_type=entity.dxftype(),layer=entity.dxf.layer,reason=reason)

def _centerline_points(entity,scale):
    if entity.dxftype()=='LINE':return [_point(entity.dxf.start,scale),_point(entity.dxf.end,scale)]
    if entity.dxftype()!='POLYLINE' or not entity.is_3d_polyline:
        raise ValueError('Only simple open 3D POLYLINE and assigned LINE centerlines are supported; solids do not imply pipe semantics')
    if entity.is_closed:raise ValueError('Closed 3D POLYLINE centerlines are unsupported; the closing segment must not be discarded')
    # Fit/spline vertices describe a different curve from a simple vertex chain.
    if entity.dxf.flags&6 or entity.dxf.get('smooth_type',0) or any(v.dxf.flags&27 for v in entity.vertices):
        raise ValueError('Curve-fit and spline-fit 3D POLYLINE centerlines are unsupported; convert explicitly to an open simple centerline')
    return [_point(v.dxf.location,scale) for v in entity.vertices]

def _original_reference(entity,layers,scale):
    """Recognize an explicitly marked exchange reference, never an arbitrary prefix."""
    pipe=next((pipe for pipe in layers.values() if entity.dxf.layer=='ORIGINAL_'+quote(pipe.id,safe='-_.')),None)
    if pipe is None or entity.dxftype()!='POLYLINE' or not entity.is_3d_polyline or not entity.has_xdata('PIPEFORGE'):return False
    tags=list(entity.get_xdata('PIPEFORGE'));names=[v for code,v in tags if code==1000];sizes=[v for code,v in tags if code==1040]
    return names[:2]==[pipe.id,'ORIGINAL'] and bool(sizes) and abs(sizes[0]*scale-pipe.outer_diameter)<=1e-8

def _coverage(entities,mapped,ignored,handles,model):
    ignored_handles={item['handle'] for item in ignored}
    unresolved=[_source_entity(entity,'No supported explicit engineering binding; classify or explicitly exclude this source entity before relying on complete coverage') for entity in entities if entity.dxf.handle not in mapped|ignored_handles]
    static_ids=[item.id for item in [*model.pipes,*model.obstacles] if item.id not in handles]
    return dict(schema_version='1.0',adapter='dxf',scope='modelspace',source_generation=0,
                status='incomplete' if unresolved else 'unverified' if static_ids else 'complete',
                mapped_entities=len(mapped),unmapped_entities=len(unresolved),
                unresolved_ids=[item['handle'] for item in unresolved],unresolved_entities=unresolved,
                ignored_entities=ignored,static_metadata_ids=static_ids,
                environment='Explicit metadata AABBs; unclassified CAD geometry is retained as unresolved coverage, never inferred as a pipe or obstacle')

def _chain(pipe,parts):
    """Join explicitly assigned centerline entities, anchored by declared flow endpoints."""
    nodes={};adjacency={};segments=set()
    def node(point):
        key=tuple(round(v,8) for v in point);nodes.setdefault(key,point);adjacency.setdefault(key,[]);return key
    for points in parts:
        for a,b in zip(points,points[1:]):
            x,y=node(a),node(b);edge=tuple(sorted((x,y)))
            if x==y or edge in segments:raise ValueError('Duplicate or zero-length CAD centerline segment')
            segments.add(edge);adjacency[x].append(y);adjacency[y].append(x)
    ends=[id for id,neighbors in adjacency.items() if len(neighbors)==1]
    if len(ends)!=2 or any(len(n)>2 for n in adjacency.values()):raise ValueError('Centerline is branched, disconnected, or closed; explicit network connectivity is required')
    start=min(ends,key=lambda k:math.dist(nodes[k],pipe.points[0]));end=next(k for k in ends if k!=start)
    if math.dist(nodes[start],pipe.points[0])>1e-6 or math.dist(nodes[end],pipe.points[-1])>1e-6:raise ValueError('CAD endpoints disagree with declared fixed connections; update explicit metadata rather than inferring flow direction')
    order=[start];previous=None;current=start
    while current!=end:
        neighbors=[k for k in adjacency[current] if k!=previous]
        if len(neighbors)!=1:raise ValueError('Ambiguous centerline connectivity')
        previous,current=current,neighbors[0];order.append(current)
        if len(order)>len(segments)+1:raise ValueError('Centerline cycle')
    if len(order)!=len(segments)+1:raise ValueError('Disconnected centerline entities')
    return [nodes[k] for k in order]

def import_exchange_dxf(text,metadata,source_units=None):
    source=ezdxf.read(io.StringIO(text));declared=source.units
    entities=list(source.modelspace())
    if len(entities)>10_000:raise ValueError('DXF extraction exceeds the supported 10000 modelspace entities; select an explicit smaller engineering scope')
    if source_units is not None and source_units not in UNITS:raise ValueError('Supported source units: m, mm, cm, in, ft, km')
    matching=next((key for key,(code,scale) in UNITS.items() if code==declared),None)
    if declared==0:
        if source_units is None:raise ValueError('Unitless DXF requires explicit source_units; units are never guessed')
        units=source_units
    else:
        if matching is None:raise ValueError('Unsupported DXF INSUNITS; convert explicitly in CAD')
        if source_units is not None and matching!=source_units:raise ValueError('Explicit source units contradict DXF INSUNITS')
        units=matching
    scale=UNITS[units][1];model=metadata.model_copy(deep=True)
    layers={'PIPE_'+quote(p.id,safe='-_.'):p for p in model.pipes}
    pieces={id:[] for id in layers};handles={};mapped=set();ignored=[]
    for entity in entities:
        if _original_reference(entity,layers,scale):
            ignored.append(_source_entity(entity,'Explicit PIPEFORGE ORIGINAL reference with matching engineering identity and source size; inactive comparison geometry'))
            continue
        if not entity.dxf.layer.startswith('PIPE_'):continue
        if entity.dxf.layer not in layers:raise ValueError('Pipe layer has no explicit engineering metadata')
        pipe=layers[entity.dxf.layer]
        points=_centerline_points(entity,scale)
        if entity.has_xdata('PIPEFORGE'):
            tags=list(entity.get_xdata('PIPEFORGE'));names=[v for code,v in tags if code==1000];sizes=[v for code,v in tags if code==1040]
            if names[:2]!=[pipe.id,pipe.system_id] or len(sizes)<2 or abs(sizes[0]*scale-pipe.nominal_diameter)>1e-8 or abs(sizes[1]*scale-pipe.outer_diameter)>1e-8:raise ValueError('DXF identity or size metadata contradicts supplied engineering model/units')
        pieces[entity.dxf.layer].append(points);handles.setdefault(pipe.id,[]).append(entity.dxf.handle);mapped.add(entity.dxf.handle)
    for layer,pipe in layers.items():
        if not pieces[layer]:raise ValueError('Missing source pipe geometry; include every metadata piping system')
        pipe.points=_chain(pipe,pieces[layer])
    model=Model.model_validate(model.model_dump())
    # Reuse strict semantic and obstacle-face consistency checks in canonical meters.
    normalized=ezdxf.new('R2013');normalized.units=6;normalized.appids.new('PIPEFORGE')
    for pipe in model.pipes:
        layer='PIPE_'+quote(pipe.id,safe='-_.');normalized.layers.new(layer)
        normalized.modelspace().add_polyline3d(pipe.points,dxfattribs={'layer':layer})
    environment_layers={'ENV_'+quote(obstacle.id,safe='-_.'):obstacle for obstacle in model.obstacles}
    for entity in source.modelspace().query('3DFACE'):
        if entity.dxf.layer.startswith('ENV_'):
            if entity.dxf.layer not in environment_layers:raise ValueError('Unknown environment geometry layer')
            if entity.dxf.layer not in normalized.layers:normalized.layers.new(entity.dxf.layer)
            normalized.modelspace().add_3dface([_point(entity.dxf.get('vtx'+str(i)),scale) for i in range(4)],dxfattribs={'layer':entity.dxf.layer});mapped.add(entity.dxf.handle)
            handles.setdefault(environment_layers[entity.dxf.layer].id,[]).append(entity.dxf.handle)
    buf=io.StringIO();normalized.write(buf);model=import_dxf(buf.getvalue(),model)
    return dict(model=model.model_dump(mode='json'),object_map=handles,coordinate_transform=CoordinateTransform(source_units=units,meters_per_unit=scale).checked(),adapter='dxf',coverage=_coverage(entities,mapped,ignored,handles,model),source_coordinates={p.id:[[v/scale for v in point] for point in p.points] for p in model.pipes})
