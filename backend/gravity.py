"""Pitch-aware gravity detours with conservative slab-projected occupancy.

Search includes traveled horizontal length in state. A path is lifted with uniform
signed fall into its original endpoint elevations, then validated in 3D. Projection
conservatively blocks over/under-passes: absence of a route is not proof of infeasibility.
"""
import heapq,itertools,math,time
import numpy as np
from .geometry import segment_box_distance,segment_distance,simplify
from .catalog import compatible,takeout
from .validation import validate
from .fitting_geometry import conservative_pipe_margin,fitting_envelopes
from .work_limits import work_scope,checkpoint,preflight_model,preflight_grid,check_expanded

def gravity_route(model,pipe,mode,resolution,clearance,time_limit=8,cancel=None,progress=None):
    with work_scope(cancel=cancel):
        preflight_model(model,'gravity routing')
        return _gravity_route(model,pipe,mode,resolution,clearance,time_limit,cancel,progress)

def _gravity_route(model,pipe,mode,resolution,clearance,time_limit=8,cancel=None,progress=None):
    started=time.perf_counter(); a=pipe.points[0]; b=pipe.points[-1]
    sign=1 if pipe.slope_direction=='downward' else -1
    fall=(a[2]-b[2])*sign
    hmin=math.hypot(a[0]-b[0],a[1]-b[1])
    if fall< -1e-8 or fall+1e-8<(pipe.min_slope or 0)*hmin:
        return None,0,'Endpoint elevations cannot satisfy the configured signed minimum pitch',time.perf_counter()-started
    if not pipe.fittings:return None,0,'No fitting catalog is available for a gravity detour',time.perf_counter()-started
    if hmin<1e-9:
        return None,0,'No valid vertical connector; lateral gravity detour requires horizontal displacement',time.perf_counter()-started
    margin=conservative_pipe_margin(pipe,clearance)
    loz=min(a[2],b[2])-margin; hiz=max(a[2],b[2])+margin
    boxes=[((o.min[0],o.min[1],-1),(o.max[0],o.max[1],1)) for o in model.obstacles if o.max[2]>=loz and o.min[2]<=hiz]
    other=[((c[0],c[1],0),(d[0],d[1],0),p.outer_diameter/2) for p in model.pipes if p.id!=pipe.id for c,d in zip(p.points,p.points[1:]) if max(c[2],d[2])+p.outer_diameter/2>=loz and min(c[2],d[2])-p.outer_diameter/2<=hiz]
    other.extend(((f.center[0],f.center[1],0),(f.center[0],f.center[1],0),f.radius) for p in model.pipes if p.id!=pipe.id for f in fitting_envelopes(p) if f.center[2]+f.radius>=loz and f.center[2]-f.radius<=hiz)
    preflight_grid(model,resolution,margin,dimensions=2)
    axes=[sorted(set([a[j],b[j],*[round(float(x),9) for x in np.arange(model.bounds.min[j]+margin,model.bounds.max[j]-margin+1e-8,resolution)]])) for j in range(2)]
    start=tuple(axes[j].index(a[j]) for j in range(2));end=tuple(axes[j].index(b[j]) for j in range(2))
    def xy(idx):return (axes[0][idx[0]],axes[1][idx[1]],0.)
    def clear(x,y):
        checkpoint('gravity edge occupancy')
        if any(min(x[j],y[j])-margin<model.bounds.min[j]-1e-8 or max(x[j],y[j])+margin>model.bounds.max[j]+1e-8 for j in range(2)):return False
        if any(segment_box_distance(x,y,lo,hi)<margin-1e-8 for lo,hi in boxes):return False
        return not any(segment_distance(x,y,c,d)<margin+r-1e-8 for c,d,r in other)
    max_length=fall/pipe.min_slope if pipe.min_slope else math.dist(model.bounds.min,model.bounds.max)*4
    # Rounded length is part of state: pitch budget cannot be collapsed into XY alone.
    initial=(start,-1,0.,0.);scores={initial:0.};parents={};heap=[(0.,0,initial)];serial=itertools.count(1);edges={};expanded=0
    tangent=2*max((takeout(f,90) for f in pipe.fittings if abs(f.angle-90)<=f.angle_tolerance),default=0.)
    dirs=[(0,-1),(0,1),(1,-1),(1,1)]
    while heap:
        check_expanded(expanded)
        if cancel and cancel.is_set():return None,expanded,'Cancelled by designer',time.perf_counter()-started
        if expanded>=60000 or time.perf_counter()-started>time_limit:return None,expanded,'Gravity search limit reached; feasibility unresolved',time.perf_counter()-started
        priority,_,state=heapq.heappop(heap);idx,incoming,run,traveled=state;expanded+=1
        if progress and expanded%100==0:progress(expanded)
        if idx==end and traveled>0:
            path=[];cur=state
            while True:
                pos,_,_,distance=cur
                path.append((axes[0][pos[0]],axes[1][pos[1]],a[2]-sign*fall*distance/traveled))
                if cur not in parents:break
                cur=parents[cur]
            path=simplify(path[::-1]);path[0]=a;path[-1]=b
            candidate=model.model_copy(deep=True);q=next(p for p in candidate.pipes if p.id==pipe.id);q.points=path
            if not any(v['status']=='FAIL' for v in validate(model,candidate,pipe.id,clearance)):
                return path,expanded,'Pitch-budget A*: conservative horizontal slab search; every lifted segment independently validated',time.perf_counter()-started
        for direction,(axis,step) in enumerate(dirs):
            if incoming>=0 and direction==(incoming^1):continue
            turn=incoming>=0 and direction!=incoming
            if turn and run+1e-8<tangent:continue
            nxt=list(idx);nxt[axis]+=step
            if not 0<=nxt[axis]<len(axes[axis]):continue
            nxt=tuple(nxt);x=xy(idx);y=xy(nxt);distance=math.dist(x,y);newlength=round(traveled+distance,7)
            remaining=abs(y[0]-b[0])+abs(y[1]-b[1])
            if newlength+remaining>max_length+1e-7:continue
            key=tuple(sorted((idx,nxt)))
            if key not in edges:edges[key]=clear(x,y)
            if not edges[key]:continue
            newrun=round(min(tangent,distance if turn else run+distance),7)
            if nxt==end and turn and newrun<tangent-1e-8:continue
            ns=(nxt,direction,newrun,newlength)
            bend_weight=2 if mode=='bends' else .35
            cost=scores[state]+distance/max(hmin,1)+bend_weight*int(turn)
            if cost<scores.get(ns,float('inf'))-1e-10:
                scores[ns]=cost;parents[ns]=state;heapq.heappush(heap,(cost+remaining/max(hmin,1),next(serial),ns))
    return None,expanded,'No pitch-safe catalog-compatible detour found in the conservative projected graph',time.perf_counter()-started
