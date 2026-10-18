import io,json
import ezdxf
import pytest
from backend.cad_exchange import import_exchange_dxf,CoordinateTransform
from backend.scenarios import get_scenario
from backend.models import Model,Obstacle
from backend.exports import export_data

def drawing(unit=4):
    m=get_scenario('efficient');doc=ezdxf.new();doc.units=unit
    for p in m.pipes:
        layer='PIPE_'+p.id;doc.layers.new(layer);doc.modelspace().add_polyline3d([[v*1000 for v in point] for point in p.points],dxfattribs={'layer':layer})
    text=io.StringIO();doc.write(text);return m,doc,text.getvalue()

def test_millimeter_exchange_has_explicit_scale_ids_and_handles():
    m,doc,text=drawing();result=import_exchange_dxf(text,m)
    assert Model.model_validate(result['model'])==m
    assert result['coordinate_transform']=={'source_units':'mm','meters_per_unit':.001}
    assert result['object_map']['P-101']==[next(iter(doc.modelspace())).dxf.handle]
    assert result['source_coordinates']['P-101'][0]==[1000,1000,1000]

def test_unitless_requires_engineer_declaration_and_conflicting_units_rejected():
    m,doc,text=drawing(0)
    with pytest.raises(ValueError,match='Unitless'):import_exchange_dxf(text,m)
    assert Model.model_validate(import_exchange_dxf(text,m,'mm')['model'])==m
    m,doc,text=drawing(4)
    with pytest.raises(ValueError,match='contradict'):import_exchange_dxf(text,m,'m')

def test_assigned_line_entities_join_with_explicit_flow_endpoints():
    m,doc,text=drawing();entity=next(iter(doc.modelspace()));doc.modelspace().delete_entity(entity)
    doc.modelspace().add_line((10000,1000,1000),(5000,1000,1000),dxfattribs={'layer':'PIPE_P-101'})
    doc.modelspace().add_line((1000,1000,1000),(5000,1000,1000),dxfattribs={'layer':'PIPE_P-101'})
    buf=io.StringIO();doc.write(buf);result=import_exchange_dxf(buf.getvalue(),m)
    assert result['model']['pipes'][0]['points']==[[1.,1.,1.],[5.,1.,1.],[10.,1.,1.]]
    assert len(result['object_map']['P-101'])==2

def test_branching_and_endpoint_ambiguity_are_not_silently_inferred():
    m,doc,text=drawing();doc.modelspace().add_line((1000,1000,1000),(1000,2000,1000),dxfattribs={'layer':'PIPE_P-101'})
    buf=io.StringIO();doc.write(buf)
    with pytest.raises(ValueError,match='endpoints'):import_exchange_dxf(buf.getvalue(),m)

def test_meter_export_reimports_with_source_handles_and_consistent_obstacles():
    m=get_scenario('inefficient');text,_=export_data(m,m,'dxf')
    result=import_exchange_dxf(text,m)
    assert Model.model_validate(result['model'])==m
    assert result['coordinate_transform']['meters_per_unit']==1
    assert result['coverage']['mapped_entities']>=len(m.pipes)+6*len(m.obstacles)
    assert result['coverage']['status']=='complete'
    assert len(result['object_map'][m.obstacles[0].id])==6

def test_contradictory_source_transform_is_rejected():
    with pytest.raises(ValueError,match='contradicts'):CoordinateTransform(source_units='mm',meters_per_unit=1).checked()

def test_closed_3d_polyline_is_rejected_instead_of_dropping_its_closing_edge():
    m,doc,_=drawing();original=next(iter(doc.modelspace()));layer=original.dxf.layer
    doc.modelspace().delete_entity(original)
    m.pipes[0].points=[(1,1,1),(3,1,1),(3,3,1)]
    doc.modelspace().add_polyline3d([[v*1000 for v in point] for point in m.pipes[0].points],close=True,dxfattribs={'layer':layer})
    buf=io.StringIO();doc.write(buf)
    with pytest.raises(ValueError,match='Closed.*closing segment'):
        import_exchange_dxf(buf.getvalue(),m)

@pytest.mark.parametrize('unsupported',['curve_fit','spline_fit','smooth_type','spline_vertex'])
def test_non_simple_3d_polyline_cannot_be_reinterpreted_as_a_vertex_chain(unsupported):
    m,doc,_=drawing();polyline=next(iter(doc.modelspace()))
    if unsupported=='curve_fit':polyline.dxf.flags|=2
    elif unsupported=='spline_fit':polyline.dxf.flags|=4
    elif unsupported=='smooth_type':polyline.dxf.smooth_type=6
    else:polyline.vertices[0].dxf.flags|=16
    buf=io.StringIO();doc.write(buf)
    with pytest.raises(ValueError,match='Curve-fit and spline-fit'):
        import_exchange_dxf(buf.getvalue(),m)

@pytest.mark.parametrize('entity_type',['ARC','CIRCLE','LWPOLYLINE'])
def test_unsupported_assigned_geometry_fails_instead_of_supplying_an_invented_centerline(entity_type):
    m,doc,_=drawing();polyline=next(iter(doc.modelspace()));layer=polyline.dxf.layer
    doc.modelspace().delete_entity(polyline)
    if entity_type=='ARC':doc.modelspace().add_arc((1000,1000,1000),1000,0,90,dxfattribs={'layer':layer})
    elif entity_type=='CIRCLE':doc.modelspace().add_circle((1000,1000,1000),1000,dxfattribs={'layer':layer})
    else:doc.modelspace().add_lwpolyline([(1000,1000),(10000,1000)],dxfattribs={'layer':layer})
    buf=io.StringIO();doc.write(buf)
    with pytest.raises(ValueError,match='Only simple open'):
        import_exchange_dxf(buf.getvalue(),m)

def test_unclassified_real_solid_preserves_source_identity_and_incomplete_coverage():
    from ezdxf.acis import api as acis
    from ezdxf.render import forms
    m,doc,_=drawing();doc.layers.new('STRUCTURE')
    solid=doc.modelspace().add_3dsolid(dxfattribs={'layer':'STRUCTURE'})
    acis.export_dxf(solid,[acis.body_from_mesh(forms.cube(center=False).scale(1000,1000,1000).translate(1500,500,500))])
    buf=io.StringIO();doc.write(buf)
    result=import_exchange_dxf(buf.getvalue(),m);coverage=result['coverage']
    assert Model.model_validate(result['model'])==m
    assert coverage['status']=='incomplete' and coverage['scope']=='modelspace'
    assert coverage['unresolved_ids']==[solid.dxf.handle]
    assert coverage['unmapped_entities']==1
    assert coverage['unresolved_entities'][0]['entity_type']=='3DSOLID'
    assert coverage['unresolved_entities'][0]['layer']=='STRUCTURE'
    assert coverage['unresolved_entities'][0]['reason']
    json.dumps(coverage,allow_nan=False)

def test_arbitrary_original_prefix_does_not_hide_unclassified_geometry():
    m,doc,_=drawing();doc.layers.new('ORIGINAL_Unclassified-structure')
    entity=doc.modelspace().add_line((0,0,0),(1000,0,0),dxfattribs={'layer':'ORIGINAL_Unclassified-structure'})
    buf=io.StringIO();doc.write(buf);coverage=import_exchange_dxf(buf.getvalue(),m)['coverage']
    assert coverage['status']=='incomplete'
    assert coverage['unresolved_ids']==[entity.dxf.handle]
    assert coverage['ignored_entities']==[]

def test_marked_original_exchange_reference_retains_explicit_ignore_reason():
    m=get_scenario('inefficient');proposed=m.model_copy(deep=True)
    proposed.pipes[0].points=[m.pipes[0].points[0],m.pipes[0].points[-1]]
    text,_=export_data(m,proposed,'dxf');coverage=import_exchange_dxf(text,proposed)['coverage']
    assert coverage['status']=='complete' and coverage['unmapped_entities']==0
    assert len(coverage['ignored_entities'])==1
    assert coverage['ignored_entities'][0]['layer']=='ORIGINAL_P-101'
    assert coverage['ignored_entities'][0]['reason']

def test_unbound_static_metadata_is_qualified_without_claiming_cad_extraction():
    m,doc,text=drawing();m.obstacles.append(Obstacle(id='STATIC-METADATA',kind='equipment',min=(4,4,.2),max=(6,6,2)))
    coverage=import_exchange_dxf(text,m)['coverage']
    assert coverage['status']=='unverified' and coverage['unmapped_entities']==0
    assert coverage['static_metadata_ids']==['STATIC-METADATA']
