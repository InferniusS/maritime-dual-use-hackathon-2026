from math import dist, hypot
from collections import Counter
from .geometry import length,bends,angle,segment_box_distance,segment_distance,closest_on_segment_to_box,closest_on_segment_to_segment
from .hydraulics import hydraulic
from .catalog import select_fitting,takeout
from .evidence import clearance_evidence
from .fitting_geometry import fitting_envelopes,fitting_extent,geometry_metadata_missing
from .work_limits import work_scope,checkpoint,preflight_model,append_issue,check_issue_count,check_result

def violations(model,pipe,clearance=.1):
    with work_scope():
        preflight_model(model,'validation')
        return _violations(model,pipe,clearance)

def _violations(model,pipe,clearance=.1):
    issues=[]; r=pipe.outer_diameter/2
    surrounding_fittings={other.id:fitting_envelopes(other) for other in model.pipes if other.id!=pipe.id}
    missing_geometry={other.id:geometry_metadata_missing(other) for other in model.pipes}
    def issue(kind,where,measured,required,objects,explanation,severity='error',definite=False):
        missing=[]
        if kind in {'collision','pipe_collision','clearance','self_collision','containment'}:
            # Straight capsules depend on diameters, not an unrelated bend's
            # radius elsewhere in the same route.
            missing=[*(pipe.id+'.'+name for name in missing_geometry[pipe.id] if name=='outer_diameter'),*(id+'.'+name for id in objects if id in missing_geometry for name in missing_geometry[id] if name=='outer_diameter')]
        elif kind=='fitting_envelope':
            missing=[*(pipe.id+'.'+name for name in missing_geometry[pipe.id]),*(id+'.'+name for id in objects if id in missing_geometry for name in missing_geometry[id])]
        elif kind=='fitting' and explanation!='No compatible catalog fitting for bend':
            missing=[pipe.id+'.'+name for name in missing_geometry[pipe.id] if name!='outer_diameter']
        if missing and not definite:severity='warning'
        append_issue(issues,dict(id=f'{pipe.id}-{kind}-{len(issues)}',pipe_id=pipe.id,object_ids=[pipe.id,*objects],type=kind,severity=severity,location=where,measured=measured,actual=measured,required=required,explanation=explanation,suggested_correction='Reroute or resolve engineering metadata before acceptance',verification_status='verified' if severity=='error' else 'unverified',metadata_missing=missing,dimension_independent_failure=definite))
    for a,b in zip(pipe.points,pipe.points[1:]):
        checkpoint('straight-segment validation')
        if any(p[j]-r<model.bounds.min[j]-1e-8 or p[j]+r>model.bounds.max[j]+1e-8 for p in (a,b) for j in range(3)):
            outside=any(p[j]<model.bounds.min[j]-1e-8 or p[j]>model.bounds.max[j]+1e-8 for p in (a,b) for j in range(3))
            issue('containment',a,'Envelope outside bounds','Entire envelope within permitted space',[],'Pipe envelope crosses compartment boundary',definite=outside)
        for o in model.obstacles:
            checkpoint('obstacle validation')
            center_gap=segment_box_distance(a,b,o.min,o.max);d=center_gap-r
            if d<clearance-1e-8:issue('collision' if d<0 else 'clearance',closest_on_segment_to_box(a,b,o.min,o.max),round(d,6),clearance,[o.id],f'Insufficient separation from {o.kind}',definite=center_gap<1e-12)
        for other in model.pipes:
            if other.id==pipe.id:continue
            for index,(c,d) in enumerate(zip(other.points,other.points[1:])):
                if index%256==0:checkpoint('surrounding-segment validation')
                center_gap=segment_distance(a,b,c,d);gap=center_gap-r-other.outer_diameter/2
                if gap<clearance-1e-8:issue('pipe_collision' if gap<0 else 'clearance',closest_on_segment_to_segment(a,b,c,d),round(gap,6),clearance,[other.id],'Other system occupies finite physical volume',definite=center_gap<1e-12)
            for fitting in surrounding_fittings[other.id]:
                checkpoint('surrounding-fitting validation')
                gap=segment_distance(a,b,fitting.center,fitting.center)-r-fitting.radius
                if gap<clearance-1e-8:issue('fitting_envelope',closest_on_segment_to_segment(a,b,fitting.center,fitting.center),round(gap,6),clearance,[other.id],'Route overlaps the conservative fitting enclosure of a surrounding pipe')
        if pipe.category in ('drainage','vent') and pipe.min_slope is not None:
            horizontal=hypot(b[0]-a[0],b[1]-a[1]); drop=(a[2]-b[2])*(1 if pipe.slope_direction=='downward' else -1)
            if drop+1e-8<pipe.min_slope*horizontal:issue('slope',a,drop/max(horizontal,1e-9),pipe.min_slope,[],'Reverse slope or insufficient fall in listed flow direction')
    # Nonadjacent self intersections are physical clashes too.
    segs=list(zip(pipe.points,pipe.points[1:]))
    for i,(a,b) in enumerate(segs):
        checkpoint('self-intersection validation')
        for index,(c,d) in enumerate(segs[i+2:]):
            if index%256==0:checkpoint('self-intersection pairs')
            center_gap=segment_distance(a,b,c,d)
            if center_gap<2*r+clearance-1e-8:issue('self_collision',a,'Overlapping swept envelopes',clearance,[],'Route intersects itself',definite=center_gap<1e-12)
    for i,(a,b,c) in enumerate(zip(pipe.points,pipe.points[1:],pipe.points[2:])):
        checkpoint('fitting validation')
        theta=angle(a,b,c)
        if theta<1e-5:continue
        fit=select_fitting(pipe,theta)
        if fit is None:issue('fitting',b,theta,'Catalog-supported angle',[],'No compatible catalog fitting for bend');continue
        import math
        required_tangent=takeout(fit,theta)
        if min(dist(a,b),dist(b,c))+1e-8<required_tangent*2:issue('fitting',b,min(dist(a,b),dist(b,c)),2*required_tangent,[],'Insufficient straight length for conservative adjacent elbow takeouts')
        # The tangent-point extent encloses the circular fillet at every angle.
        rr=r+fitting_extent(fit,theta)+clearance
        for o in model.obstacles:
            if segment_box_distance(b,b,o.min,o.max)<rr-1e-8:issue('fitting_envelope',b,'Elbow enclosure overlaps obstacle',rr,[o.id],'Conservative elbow sphere clearance failed')
        for other in model.pipes:
            if other.id!=pipe.id and any(segment_distance(b,b,x,y)<rr+other.outer_diameter/2-1e-8 for x,y in zip(other.points,other.points[1:])):issue('fitting_envelope',b,'Elbow enclosure overlaps other pipe',rr,[other.id],'Conservative elbow sphere clearance failed')
            if other.id!=pipe.id:
                for fitting in surrounding_fittings[other.id]:
                    gap=dist(b,fitting.center)-(rr-clearance)-fitting.radius
                    if gap<clearance-1e-8:issue('fitting_envelope',b,round(gap,6),clearance,[other.id],'Conservative fitting enclosures overlap')
        if any(b[j]-rr<model.bounds.min[j]-1e-8 or b[j]+rr>model.bounds.max[j]+1e-8 for j in range(3)):issue('fitting_envelope',b,'Elbow enclosure outside bounds',rr,[],'Conservative elbow containment failed')
    return issues

def validate(original,proposed,pipe_id,clearance=.1):
    with work_scope():
        preflight_model(proposed,'independent validation')
        return _validate(original,proposed,pipe_id,clearance)

def _validate(original,proposed,pipe_id,clearance=.1):
    old=next((p for p in original.pipes if p.id==pipe_id),None); new=next((p for p in proposed.pipes if p.id==pipe_id),None)
    rows=[]
    def row(name,ok,detail):rows.append(dict(requirement=name,status='PASS' if ok else 'FAIL',detail=detail))
    if old is None or new is None:return [dict(requirement='identity',status='FAIL',status_code='FAIL',legacy_status='FAIL',detail='Missing pipe',object_ids=[pipe_id],location=None,measured=None,required='Pipe present in original and proposed model',severity='error',suggested_correction='Restore the source pipe identity',evidence=[])]
    row('endpoints',dist(old.points[0],new.points[0])<1e-8 and dist(old.points[-1],new.points[-1])<1e-8,'Fixed connection coordinates')
    row('pipe_membership',{p.id for p in original.pipes}=={p.id for p in proposed.pipes} and len(original.pipes)==len(proposed.pipes),'No added or removed pipe systems')
    row('centerline_connectivity',all(dist(a,b)>1e-9 for a,b in zip(new.points,new.points[1:])),'Continuous ordered polyline with nonzero segments; nozzle topology remains unverified')
    row('size_preservation',old.nominal_diameter==new.nominal_diameter and old.outer_diameter==new.outer_diameter and old.inner_diameter==new.inner_diameter,'Nominal, outer and inner diameters unchanged')
    row('permissions',old.editable or old==new,'Only explicitly editable routes can change')
    row('fixed_environment',original.bounds==proposed.bounds and original.obstacles==proposed.obstacles,'Structure and equipment remain fixed')
    row('other_systems_preserved',all(any(q==p for q in proposed.pipes) for p in original.pipes if p.id!=pipe_id),'Other systems unchanged in this single-route proposal')
    row('system_identity',old.model_dump(exclude={'points'})==new.model_dump(exclude={'points'}),'System, sizes, operating conditions, permissions, rules and catalog unchanged')
    row('coordinate_reference',original.id==proposed.id and original.units==proposed.units and original.coordinate_system==proposed.coordinate_system,'Source identity, units and coordinate frame preserved')
    issues=violations(proposed,new,clearance)
    for name,kinds in [('collision_clearance',{'collision','pipe_collision','clearance','self_collision','fitting_envelope'}),('containment',{'containment'}),('fitting_geometry',{'fitting'}),('functional_slope',{'slope'})]:
        if name=='functional_slope' and new.category=='pressure':rows.append(dict(requirement=name,status='NOT_APPLICABLE',detail='Pressure system'))
        elif name=='functional_slope' and new.min_slope is None:rows.append(dict(requirement=name,status='UNVERIFIED',detail='No supplied pitch requirement'))
        else:row(name,not any(x['type'] in kinds for x in issues),'; '.join(x['explanation'] for x in issues if x['type'] in kinds) or 'Evaluated against explicit model constraints')
    rows.extend([dict(requirement='organizer_standards',status='UNVERIFIED',detail='No official standards supplied; not certified'),dict(requirement='commercial_fittings',status='PASS' if new.fittings and all(f.approved for f in new.fittings) else 'UNVERIFIED',detail='Catalog approval metadata; demonstration fittings are unverified'),dict(requirement='nozzle_orientation',status='UNVERIFIED',detail='Endpoint positions checked; nozzle orientation and joint connectivity metadata absent')])
    clearances=clearance_evidence(proposed,new,clearance)
    # A second, symmetric pair calculation checks surrounding fitting occupancy
    # independently of the issue-collection loops above. Unchanged neighbors
    # must not acquire a clash just because only this pipe was selected.
    if any(check['status']=='FAIL' for check in clearances):
        next(entry for entry in rows if entry['requirement']=='collision_clearance')['status']='FAIL'
    rows.append(dict(requirement='catalog_size_metadata',status='PASS' if all(f.nominal_diameters and f.pipe_specifications for f in new.fittings) and new.fittings else 'UNVERIFIED',detail='Diameter/specification applicability must be declared by the supplied catalog'))
    rows.append(dict(requirement='cad_export_consistency',status='UNVERIFIED',detail='Evaluated only by an explicit export/reimport check, not by route validation'))
    dimensions=new.dimension_metadata()
    rows.append(dict(requirement='source_dimensions',status=dimensions['status'],detail='Source dimension presence; default preview values do not establish engineering dimensions',metadata=dimensions))
    if dimensions['missing'] or old.dimension_metadata()['missing']:
        entry=next(r for r in rows if r['requirement']=='size_preservation')
        if entry['status']=='PASS':entry.update(status='UNVERIFIED',detail='Numeric values retained; one or more source dimensions are missing or illustrative defaults')
    own_missing=geometry_metadata_missing(new)
    geometry_kinds={'collision_clearance':{'collision','pipe_collision','clearance','self_collision','fitting_envelope'},'containment':{'containment'},'fitting_geometry':{'fitting'}}
    for requirement in ('collision_clearance','containment','fitting_geometry'):
        entry=next(r for r in rows if r['requirement']==requirement)
        missing=own_missing[:]
        if requirement=='collision_clearance':
            missing.extend(name for check in clearances for name in check['metadata_missing'])
        if requirement=='fitting_geometry':
            missing=[name for name in missing if name!='outer_diameter']
            # Nominal size is required when a supplied fitting limits sizes.
            if bends(new.points) and any(f.nominal_diameters for f in new.fittings) and 'nominal_diameter' in dimensions['missing']:missing.append('nominal_diameter')
        known_failure=any(issue['type'] in geometry_kinds[requirement] and (not issue.get('metadata_missing') or issue.get('dimension_independent_failure')) for issue in issues)
        if requirement=='collision_clearance':known_failure=known_failure or any(check['status']=='FAIL' for check in clearances)
        if missing and not known_failure:
            entry.update(status='UNVERIFIED',detail='Dependent engineering inputs missing: '+', '.join(sorted(set(missing))),metadata_missing=sorted(set(missing)))
    for entry in rows:
        entry['status_code']=entry['status'].replace(' ','_')
        entry['legacy_status']=entry['status'].replace('_',' ')
        entry['severity']='error' if entry['status']=='FAIL' else 'warning' if entry['status']=='UNVERIFIED' else 'info'
        entry['suggested_correction']='Resolve the measured constraint and rerun independent validation' if entry['status']=='FAIL' else 'Obtain authoritative engineering inputs' if entry['status']=='UNVERIFIED' else None
        entry['object_ids']=[pipe_id]
        entry['evidence']=[x for x in issues if (entry['requirement']=='functional_slope' and x['type']=='slope') or (entry['requirement']=='fitting_geometry' and x['type']=='fitting') or (entry['requirement']=='collision_clearance' and x['type'] in {'collision','pipe_collision','clearance','fitting_envelope','self_collision'}) or (entry['requirement']=='containment' and x['type']=='containment')]
        entry['location']=entry['evidence'][0]['location'] if entry['evidence'] else None
        entry['object_ids']=sorted(set([pipe_id,*[id for issue in entry['evidence'] for id in issue['object_ids']]]))
        entry['measured']=len(entry['evidence']) if entry['requirement'] in {'collision_clearance','fitting_geometry','containment','functional_slope'} else None
        entry['required']=0 if entry['measured'] is not None else None
        if entry['requirement']=='endpoints':
            entry['measured']=max(dist(old.points[0],new.points[0]),dist(old.points[-1],new.points[-1]));entry['required']=1e-8;entry['units']='m'
        if entry['requirement']=='functional_slope':
            entry['measured']=metrics(new)['minimum_segment_slope'] if new.category!='pressure' else None;entry['required']=new.min_slope;entry['units']='m/m (configured flow direction)'
        if entry['requirement']=='collision_clearance':
            entry['clearance_evidence']=clearances
            entry['measured']=min((c['measured'] for c in clearances),default=None)
            entry['required']=clearance
            entry['units']='m'
    return rows

def metrics(pipe):
    angles=[angle(a,b,c) for a,b,c in zip(pipe.points,pipe.points[1:],pipe.points[2:]) if angle(a,b,c)>1e-5]
    slopes=[]
    for a,b in zip(pipe.points,pipe.points[1:]):
        h=hypot(b[0]-a[0],b[1]-a[1])
        if h>1e-9:slopes.append((a[2]-b[2])*(1 if pipe.slope_direction=='downward' else -1)/h)
    return dict(length=length(pipe.points),bends=bends(pipe.points),hydraulics=hydraulic(pipe),fitting_count=len(angles),bend_angle_distribution=dict(Counter(str(round(x,3)) for x in angles)),elevation_change=sum(abs(b[2]-a[2]) for a,b in zip(pipe.points,pipe.points[1:])),minimum_segment_slope=min(slopes) if slopes else None)

def analyze_pipe(model,pipe,clearance=.1):
    """One-pipe calculation with complete surrounding model occupancy."""
    checkpoint('pipe analysis')
    issues=violations(model,pipe,clearance)
    if pipe.editable and len(pipe.points)>2:
        reference=model.model_copy(deep=True)
        direct=next(p for p in reference.pipes if p.id==pipe.id)
        direct.points=[pipe.points[0],pipe.points[-1]]
        checks=validate(model,reference,pipe.id,clearance)
        geometry_checks=[r for r in checks if r['requirement'] in {'collision_clearance','containment','fitting_geometry'}]
        if length(pipe.points)>length(direct.points)+1e-6 and not any(r['status']=='FAIL' for r in checks):
            verified=all(r['status']=='PASS' for r in geometry_checks)
            append_issue(issues,dict(id=pipe.id+'-feasible-reference',pipe_id=pipe.id,object_ids=[pipe.id],type='inefficiency',severity='warning',location=pipe.points[0],measured=length(pipe.points),required=length(direct.points),explanation='A calculated, independently checked direct connector is shorter under implemented constraints' if verified else 'A shorter direct connector was calculated, but missing source dimensions prevent verified geometric clearance',suggested_correction='Evaluate the direct connector proposal and review unverified engineering requirements',verification_status='verified' if verified else 'unverified',reference_coordinates=direct.points))
    return dict(issues=issues,metrics=metrics(pipe))

def analyze(model,clearance=.1):
    with work_scope():
        preflight_model(model,'analysis')
        result=_analyze(model,clearance)
        check_result(result)
        return result

def _analyze(model,clearance=.1):
    records={};issue_count=0
    for pipe in model.pipes:
        records[pipe.id]=analyze_pipe(model,pipe,clearance)
        issue_count+=len(records[pipe.id]['issues']);check_issue_count(issue_count)
    # Preserve the original public issue ordering: geometry, then feasible-reference warnings.
    all_issues=[i for p in model.pipes for i in records[p.id]['issues']]
    issues=[i for i in all_issues if i['type']!='inefficiency']+[i for i in all_issues if i['type']=='inefficiency']
    check_issue_count(len(issues))
    return dict(issues=issues,metrics={p.id:records[p.id]['metrics'] for p in model.pipes})


def assurance(rows):
    geometry={'collision_clearance','containment','fitting_geometry'}
    inspected={r['requirement'] for r in rows}
    implemented=geometry|{'endpoints','pipe_membership','centerline_connectivity','size_preservation','permissions','fixed_environment','other_systems_preserved','system_identity','coordinate_reference','functional_slope'}
    return dict(geometrically_valid=geometry.issubset(inspected) and all(r['status']=='PASS' for r in rows if r['requirement'] in geometry),implemented_constraints_valid=bool(rows) and not any(r['status']=='FAIL' or (r['requirement'] in implemented and r['status']=='UNVERIFIED') for r in rows),supplied_standards='UNVERIFIED',requires_engineering_review=True,scope='Analytic straight envelopes and angle-bounded conservative fitting occupancy; not certified')
