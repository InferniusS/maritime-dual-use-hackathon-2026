import heapq,itertools,time,math
import numpy as np
from .geometry import length,segment_box_distance,segment_distance,simplify
from .validation import validate,analyze,metrics,assurance
from .catalog import compatible,takeout as fitting_takeout
from .spatial import SpatialCandidates
from .fitting_geometry import conservative_pipe_margin,fitting_envelopes
from .work_limits import work_scope,checkpoint,preflight_model,preflight_grid,check_expanded,check_result

def point_segment_distance(point,a,b):
    v=np.subtract(b,a); w=np.subtract(point,a); t=max(0.,min(1.,float(w@v/(v@v))))
    return float(np.linalg.norm(w-t*v))

MODES={'shortest':(1,.01,0,0,0),'bends':(1,2,.1,0,0),'balanced':(1,.35,.15,0,.1),'hydraulic':(1,.2,.05,1,0),'modification':(1,.2,.1,0,2)}

def objective(pipe,reference,mode,model):
    w=MODES[mode];L=max(length(reference.points),1e-9);diag=math.dist(model.bounds.min,model.bounds.max)
    m=metrics(pipe);ref=metrics(reference);segments=list(zip(reference.points,reference.points[1:]))
    modification=0.
    for a,b in zip(pipe.points,pipe.points[1:]):
        checkpoint('route objective quadrature')
        # Midpoint quadrature approximates distance from the retained route.
        for t in (.125,.375,.625,.875):
            p=tuple(a[j]+t*(b[j]-a[j]) for j in range(3))
            modification+=math.dist(a,b)/4*min(segment_distance(p,p,x,y) for x,y in segments)/max(pipe.outer_diameter,.01)/L
    hydraulic_term=None
    if m['hydraulics']['status']=='PASS' and ref['hydraulics']['status']=='PASS':hydraulic_term=m['hydraulics']['pressure_loss_pa']/max(ref['hydraulics']['pressure_loss_pa'],1e-9)
    terms=dict(length=m['length']/L,bends=m['bends']/max(1,ref['bends']),elevation=sum(abs(a[2]-b[2]) for a,b in zip(pipe.points,pipe.points[1:]))/diag,hydraulic=hydraulic_term,modification=modification)
    value=sum(weight*(terms[key] or 0) for weight,key in zip(w,terms))
    return {'value':value,'terms':terms,'weights':dict(zip(terms,w)),'hydraulic_term_available':hydraulic_term is not None}

def route(model,pipe,mode,resolution,clearance,time_limit=8,cancel=None,progress=None):
    with work_scope(cancel=cancel):
        preflight_model(model,'routing')
        return _route(model,pipe,mode,resolution,clearance,time_limit,cancel,progress)

def _route(model,pipe,mode,resolution,clearance,time_limit=8,cancel=None,progress=None):
    start=time.perf_counter(); weights=MODES[mode]; L=max(length(pipe.points),resolution); diag=math.dist(model.bounds.min,model.bounds.max)
    # Every edge uses an enlarged capsule, conservatively covering elbow sweeps.
    margin=conservative_pipe_margin(pipe,clearance)
    boxes=[(np.array(o.min),np.array(o.max)) for o in model.obstacles]
    others=[(np.array(a),np.array(b),p.outer_diameter/2) for p in model.pipes if p.id!=pipe.id for a,b in zip(p.points,p.points[1:])]
    # Existing fittings occupy volume even when the routed pipe is straight.
    others.extend((np.array(f.center),np.array(f.center),f.radius) for p in model.pipes if p.id!=pipe.id for f in fitting_envelopes(p))
    box_index=SpatialCandidates(boxes)
    pipe_index=SpatialCandidates([(a-r,b+r) if np.all(a<=b) else (np.minimum(a,b)-r,np.maximum(a,b)+r) for a,b,r in others])
    def clear(a,b):
        checkpoint('routing edge occupancy')
        a=np.asarray(a);b=np.asarray(b)
        if any(min(a[j],b[j])-margin<model.bounds.min[j]-1e-9 or max(a[j],b[j])+margin>model.bounds.max[j]+1e-9 for j in range(3)):return False
        lo=np.minimum(a,b)-margin;hi=np.maximum(a,b)+margin
        for i in box_index.query(a,b,margin):
            x,y=boxes[i]
            if np.any(x>hi) or np.any(y<lo):continue
            if segment_box_distance(a,b,x,y)<margin-1e-8:return False
        for i in pipe_index.query(a,b,margin):
            x,y,r=others[i]
            if np.any(np.minimum(x,y)-r>hi) or np.any(np.maximum(x,y)+r<lo):continue
            if segment_distance(a,b,x,y)<margin+r-1e-8:return False
        return True
    source=np.array(pipe.points[0]); target=np.array(pipe.points[-1])
    def candidate(points):
        checkpoint('routing candidate validation')
        m=model.model_copy(deep=True); q=next(p for p in m.pipes if p.id==pipe.id);q.points=simplify(points)
        rows=validate(model,m,pipe.id,clearance)
        return q.points if not any(r['status']=='FAIL' for r in rows) else None
    # Direct connector checked with true envelopes by independent validator.
    direct=candidate([source,target])
    if direct is not None:return direct,0,'Direct feasible connector',time.perf_counter()-start
    if pipe.category in ('drainage','vent') and pipe.min_slope is not None:
        from .gravity import gravity_route
        return gravity_route(model,pipe,mode,resolution,clearance,time_limit,cancel,progress)
    # Axes include exact endpoints, avoiding rounded/moved nozzle coordinates.
    preflight_grid(model,resolution,margin)
    axes=[sorted(set([float(source[j]),float(target[j]),*[round(float(x),9) for x in np.arange(model.bounds.min[j]+margin,model.bounds.max[j]-margin+1e-8,resolution)]])) for j in range(3)]
    origin=tuple(axes[j].index(float(source[j])) for j in range(3)); goal=tuple(axes[j].index(float(target[j])) for j in range(3))
    def point(idx):return tuple(axes[j][idx[j]] for j in range(3))
    directions=[(j,s) for j in range(3) for s in (-1,1)]
    # Include capped incoming straight run in state: future fitting feasibility depends on it.
    takeout=2*max((fitting_takeout(f,90) for f in pipe.fittings if compatible(pipe,f,90)),default=.15)
    initial=(origin,-1,0.0); scores={initial:0.}; parents={}; queue=[(0.,0,initial,0.)]; serial=itertools.count(1); expanded=0; edges={}
    modification_cache={}
    baseline_segments=list(zip(pipe.points,pipe.points[1:])); hydraulic=metrics(pipe)['hydraulics']; hydraulic_active=hydraulic['status']=='PASS'
    while queue:
        check_expanded(expanded)
        if cancel and cancel.is_set():return None,expanded,'Cancelled by designer',time.perf_counter()-start
        if progress and expanded%100==0:progress(expanded)
        if expanded>=60000 or time.perf_counter()-start>time_limit:return None,expanded,'Search limit reached; feasibility is unresolved (not proof of impossibility)',time.perf_counter()-start
        _,_,state,queued_cost=heapq.heappop(queue)
        if queued_cost>scores[state]+1e-12:continue
        idx,incoming,run=state; cost=scores[state]; a=point(idx);expanded+=1
        if idx==goal:
            path=[a]; cur=state
            while cur in parents:cur=parents[cur];path.append(point(cur[0]))
            accepted=candidate(path[::-1])
            if accepted is not None:return accepted,expanded,'Direction-aware A*; finite search graph, no global optimality claim',time.perf_counter()-start
        for direction,(axis,sign) in enumerate(directions):
            if incoming>=0 and direction==(incoming^1):continue
            turn=incoming>=0 and incoming!=direction
            if turn and (run<takeout-1e-8 or not any(compatible(pipe,f,90) for f in pipe.fittings)):continue
            nxt=list(idx);nxt[axis]+=sign
            if not 0<=nxt[axis]<len(axes[axis]):continue
            nxt=tuple(nxt);b=point(nxt);distance=math.dist(a,b)
            key=tuple(sorted((idx,nxt)))
            if key not in edges:edges[key]=clear(a,b)
            if not edges[key]:continue
            newrun=round(min(takeout,distance if turn else run+distance),8)
            if nxt==goal and newrun<takeout-1e-8 and incoming>=0:continue
            # Normalize length by original L, bend by original potential turns,
            # elevation by compartment diagonal, hydraulic by original loss,
            # modification by L. Hydraulic ratio uses actual friction and elbow K.
            if weights[4] and nxt not in modification_cache:
                modification_cache[nxt]=min(point_segment_distance(b,x,y) for x,y in baseline_segments)/max(pipe.outer_diameter,.01)
            modification=modification_cache.get(nxt,0)
            hcost=0
            if weights[3] and hydraulic_active:
                dyn=pipe.density*hydraulic['velocity']**2/2
                hcost=(hydraulic['friction_factor']*distance/pipe.inner_diameter+(next((f.k for f in pipe.fittings if compatible(pipe,f,90)),0) if turn else 0))*dyn/max(hydraulic['pressure_loss_pa'],1e-9)
            delta=weights[0]*distance/L+weights[1]*int(turn)/max(1,len(pipe.points)-2)+weights[2]*abs(b[2]-a[2])/diag+weights[3]*hcost+weights[4]*modification*distance/L
            ns=(nxt,direction,newrun);nc=cost+delta
            if nc+1e-12<scores.get(ns,float('inf')):
                scores[ns]=nc;parents[ns]=state
                heuristic=weights[0]*sum(abs(b[j]-target[j]) for j in range(3))/L
                heapq.heappush(queue,(nc+heuristic,next(serial),ns,nc))
    return None,expanded,'No route on this conservative grid; this does not prove continuous-space infeasibility',time.perf_counter()-start

def optimize(model,pipe_ids=None,mode='balanced',resolution=.5,clearance=.1,cancel=None,progress=None):
    with work_scope(cancel=cancel):
        preflight_model(model,'optimization')
        result=_optimize(model,pipe_ids,mode,resolution,clearance,cancel,progress)
        check_result(result)
        return result

def _optimize(model,pipe_ids=None,mode='balanced',resolution=.5,clearance=.1,cancel=None,progress=None):
    if mode not in MODES:raise ValueError('Unknown optimization mode')
    if not .2<=resolution<=2:raise ValueError('Resolution must be between .2 and 2 m')
    if not 0<=clearance<=1:raise ValueError('Clearance must be between 0 and 1 m')
    selected=pipe_ids if pipe_ids is not None else [p.id for p in model.pipes if p.editable]
    if any(id not in [p.id for p in model.pipes] for id in selected):raise ValueError('Unknown pipe ID')
    current=model.model_copy(deep=True);candidates=[];expanded_total=0
    for id in selected:
        checkpoint('selected route optimization')
        old=next(p for p in current.pipes if p.id==id)
        if not old.editable:
            candidates.append(dict(pipe_id=id,status='rejected',reason='Pipe is fixed',runtime_s=0,before=metrics(old),after=metrics(old),validation=[]));continue
        pts,expanded,reason,runtime=route(current,old,mode,resolution,clearance,cancel=cancel,progress=(lambda n:progress(expanded_total+n)) if progress else None)
        expanded_total+=expanded
        if progress:progress(expanded_total)
        proposed=current.model_copy(deep=True);q=next(p for p in proposed.pipes if p.id==id)
        if pts:q.points=pts
        existing_valid=not any(row['status']=='FAIL' for row in validate(current,current,id,clearance))
        if pts and existing_valid and objective(old,old,mode,current)['value']<=objective(q,old,mode,current)['value']+1e-9:
            q.points=old.points.copy();reason+='; retained original with equal or lower evaluated objective'
        rows=validate(current,proposed,id,clearance)
        status='cancelled' if cancel and cancel.is_set() else 'accepted' if pts and not any(r['status']=='FAIL' for r in rows) else 'no_route'
        candidates.append(dict(pipe_id=id,status=status,reason=reason,runtime_s=runtime,expanded=expanded,before=metrics(old),after=metrics(q),validation=rows,assurance=assurance(rows),objective_before=objective(old,old,mode,current),objective_after=objective(q,old,mode,current),hydraulic_objective='active' if metrics(old)['hydraulics']['status']=='PASS' else 'unverified; hydraulic term unavailable'))
        if status=='accepted':current=proposed
    return dict(model=current.model_dump(),candidates=candidates,issues=analyze(current,clearance)['issues'])
