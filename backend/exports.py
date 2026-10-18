import csv,io,json,itertools
from urllib.parse import quote,unquote
import ezdxf
from .models import Model
from .geometry import angle
from .catalog import select_fitting
from .validation import validate,metrics,analyze

def change_report(original,proposed,clearance=.1):
    if {p.id for p in original.pipes}!={p.id for p in proposed.pipes}:raise ValueError('Pipe membership must be preserved')
    if original.model_dump(exclude={'pipes'})!=proposed.model_dump(exclude={'pipes'}):raise ValueError('Fixed environment or source reference changed')
    changes=[]
    for p in proposed.pipes:
        old=next(q for q in original.pipes if q.id==p.id)
        if p==old:continue
        # Multi-change validation checks fixed systems separately; candidate environment
        # includes every final route, so coordinated proposals cannot hide final clashes.
        baseline=proposed.model_copy(deep=True)
        baseline.pipes=[old.model_copy(deep=True) if q.id==p.id else q.model_copy(deep=True) for q in proposed.pipes]
        if not old.editable:raise ValueError('Fixed pipe changed')
        rows=validate(baseline,proposed,p.id,clearance)
        if any(r['status']=='FAIL' for r in rows):raise ValueError(f'Cannot export invalid modification {p.id}')
        fittings=[]
        for a,b,c in zip(p.points,p.points[1:],p.points[2:]):
            theta=angle(a,b,c)
            if theta<1e-5:continue
            f=select_fitting(p,theta)
            fittings.append(dict(location=b,angle=theta,catalog=f.model_dump() if f else None,nominal_diameter=p.nominal_diameter))
        changes.append(dict(pipe_id=p.id,system_id=p.system_id,original_coordinates=old.points,proposed_coordinates=p.points,nominal_diameter=p.nominal_diameter,outer_diameter=p.outer_diameter,connections=[p.points[0],p.points[-1]],required_fittings=fittings,before=metrics(old),after=metrics(p),validation=rows,removed_segments=[dict(id=f'{p.id}:existing:{i}',start=a,end=b) for i,(a,b) in enumerate(zip(old.points,old.points[1:])) if (a,b) not in list(zip(p.points,p.points[1:]))],added_segments=[dict(id=f'{p.id}:proposed:{i}',start=a,end=b) for i,(a,b) in enumerate(zip(p.points,p.points[1:])) if (a,b) not in list(zip(old.points,old.points[1:]))],rationale=f'Computed length {metrics(old)["length"]:.4f} to {metrics(p)["length"]:.4f} m; bends {metrics(old)["bends"]} to {metrics(p)["bends"]}; connections and component dimensions preserved. Qualified designer review required'))
    return dict(project_id=proposed.id,units='m',coordinate_system=proposed.coordinate_system,source_to_export_transform='identity',final_analysis=analyze(proposed,clearance),approval='UNREVIEWED — NOT FOR CONSTRUCTION',assumptions=proposed.assumptions,changes=changes,model=proposed.model_dump(),unresolved=['Organizer standards not supplied','Commercial fitting approval not established','Nozzle orientation, supports and stress analysis not implemented'])

def export_data(original,proposed,format='json',clearance=.1):
    report=change_report(original,proposed,clearance)
    if format=='json':return json.dumps(report,indent=2),'application/json'
    if format=='csv':
        buf=io.StringIO();w=csv.writer(buf);w.writerow(['pipe_id','system_id','route','point','x_m','y_m','z_m','nominal_diameter_m','outer_diameter_m'])
        for c in report['changes']:
            for key in ('original_coordinates','proposed_coordinates'):
                for i,p in enumerate(c[key]):w.writerow([c['pipe_id'],c['system_id'],key,i,*p,c['nominal_diameter'],c['outer_diameter']])
        return buf.getvalue(),'text/csv'
    if format=='report':
        text=['PIPEFORGE / ENGINEERING CHANGE REPORT',report['approval'],'Units: meters. Right handed Z up. Identity source transform.',*report['assumptions']]
        for c in report['changes']:
            text.extend([f"\nPIPE {c['pipe_id']} / {c['system_id']}",f"Nominal {c['nominal_diameter']} m; OD {c['outer_diameter']} m",'Existing: '+json.dumps(c['original_coordinates']),'Proposed: '+json.dumps(c['proposed_coordinates']),'Fittings: '+json.dumps(c['required_fittings']),*[r['requirement']+': '+r['status']+' — '+r['detail']+(f"; measured={r['measured']}, required={r['required']} {r.get('units','')}" if r.get('measured') is not None else '') for r in c['validation']]])
        text.extend(['\nUNRESOLVED',*report['unresolved']]);return '\n'.join(text),'text/plain'
    if format=='dxf':
        doc=ezdxf.new('R2013');doc.units=6;doc.appids.new('PIPEFORGE'); space=doc.modelspace()
        for p in proposed.pipes:
            layer='PIPE_'+quote(p.id,safe='-_.')
            doc.layers.new(layer,dxfattribs={'color':4 if p.editable else 8})
            entity=space.add_polyline3d(p.points,dxfattribs={'layer':layer})
            entity.set_xdata('PIPEFORGE',[(1000,p.id),(1000,p.system_id),(1040,p.nominal_diameter),(1040,p.outer_diameter)])
        for old in original.pipes:
            new=next(p for p in proposed.pipes if p.id==old.id)
            if old.points!=new.points:
                layer='ORIGINAL_'+quote(old.id,safe='-_.');doc.layers.new(layer,dxfattribs={'color':8})
                e=space.add_polyline3d(old.points,dxfattribs={'layer':layer})
                e.set_xdata('PIPEFORGE',[(1000,old.id),(1000,'ORIGINAL'),(1040,old.outer_diameter)])
        for obstacle in proposed.obstacles:
            layer='ENV_'+quote(obstacle.id,safe='-_.');doc.layers.new(layer,dxfattribs={'color':9})
            vertices=list(itertools.product(*zip(obstacle.min,obstacle.max)))
            for face in ((0,1,3,2),(4,6,7,5),(0,4,5,1),(2,3,7,6),(0,2,6,4),(1,5,7,3)):
                e=space.add_3dface([vertices[i] for i in face],dxfattribs={'layer':layer})
                e.set_xdata('PIPEFORGE',[(1000,obstacle.id),(1000,obstacle.kind)])
        # Centerlines are the authoritative proposal; no fictional solid elbows.
        buf=io.StringIO();doc.write(buf);return buf.getvalue(),'application/dxf'
    if format=='step':
        from .step_adapter import export_step
        return export_step(proposed),'application/step'
    raise ValueError('Supported formats: json, csv, report, dxf, step (optional)')

def import_dxf(text,metadata):
    doc=ezdxf.read(io.StringIO(text))
    if doc.units!=6:raise ValueError('DXF must declare meter units; convert explicitly before import')
    model=metadata.model_copy(deep=True);found=set()
    for e in doc.modelspace().query('POLYLINE'):
        if not e.is_3d_polyline or e.dxf.layer.startswith('ORIGINAL_'):continue
        if e.is_closed or e.dxf.flags&6 or e.dxf.get('smooth_type',0) or any(v.dxf.flags&27 for v in e.vertices):
            raise ValueError('Only open, simple 3D polylines are supported; closed/fitted centerlines require an explicit network model')
        pipe=next((p for p in model.pipes if e.dxf.layer=='PIPE_'+quote(p.id,safe='-_.')),None)
        if pipe is None:raise ValueError('Every pipe layer requires explicit engineering metadata')
        if pipe.id in found:raise ValueError('Duplicate pipe layer')
        if e.has_xdata('PIPEFORGE'):
            tags=list(e.get_xdata('PIPEFORGE'));texts=[v for code,v in tags if code==1000];sizes=[v for code,v in tags if code==1040]
            if texts[:2]!=[pipe.id,pipe.system_id] or len(sizes)<2 or abs(sizes[0]-pipe.nominal_diameter)>1e-8 or abs(sizes[1]-pipe.outer_diameter)>1e-8:raise ValueError('DXF identity/diameter metadata contradicts supplied engineering model')
        pipe.points=[tuple(v.dxf.location) for v in e.vertices];found.add(pipe.id)
    if found!={p.id for p in model.pipes}:raise ValueError('Missing pipe geometry; only PIPE_<id> 3D POLYLINE supported')
    # If environment geometry is present, it must agree with its explicit metadata.
    faces=list(doc.modelspace().query('3DFACE'))
    if faces:
        expected={'ENV_'+quote(o.id,safe='-_.'):o for o in model.obstacles}
        groups={}
        for face in faces:
            if not face.dxf.layer.startswith('ENV_'):continue
            if face.dxf.layer not in expected:raise ValueError('Unknown environment geometry layer')
            groups.setdefault(face.dxf.layer,[]).append(face)
        if groups and set(groups)!=set(expected):raise ValueError('Missing environment geometry')
        for layer,items in groups.items():
            obstacle=expected[layer]
            vertices=[tuple(face.dxf.get('vtx'+str(i))) for face in items for i in range(4)]
            if len(items)!=6 or any(abs(min(v[j] for v in vertices)-obstacle.min[j])>1e-8 or abs(max(v[j] for v in vertices)-obstacle.max[j])>1e-8 for j in range(3)):raise ValueError('DXF obstacle dimensions contradict engineering metadata')
    return Model.model_validate(model.model_dump())
