import numpy as np
from scipy.optimize import minimize_scalar
from math import dist, acos, degrees

def length(points): return sum(dist(a,b) for a,b in zip(points,points[1:]))
def bends(points):
    return sum(abs(angle(a,b,c))>1e-5 for a,b,c in zip(points,points[1:],points[2:]))
def angle(a,b,c):
    u=np.subtract(b,a); v=np.subtract(c,b)
    cosine=float(np.clip(np.dot(u,v)/(np.linalg.norm(u)*np.linalg.norm(v)),-1,1))
    if cosine>1-1e-12:return 0.
    return degrees(acos(cosine))
def segment_box_distance(a,b,lo,hi):
    # Squared distance to a convex box is convex and piecewise quadratic.
    a=np.asarray(a,float); d=np.subtract(b,a); lo=np.asarray(lo); hi=np.asarray(hi)
    cuts=[0.,1.]
    for j in range(3):
        if abs(d[j])>1e-14:
            cuts.extend(t for t in ((lo[j]-a[j])/d[j],(hi[j]-a[j])/d[j]) if 0<t<1)
    cuts=sorted(set(cuts)); best=float('inf')
    for l,r in zip(cuts,cuts[1:]):
        mid=a+d*((l+r)/2); mask=(mid<lo)|(mid>hi); target=np.where(mid<lo,lo,hi)
        den=float(np.dot(d[mask],d[mask])); t=l if den==0 else float(np.clip(np.dot(d[mask],(target-a)[mask])/den,l,r))
        p=a+d*t; delta=np.maximum(np.maximum(lo-p,p-hi),0); best=min(best,float(np.dot(delta,delta)))
    return best**.5

def segment_distance(a,b,c,d):
    # Closest points on two closed segments, including parallel/degenerate cases.
    a,b,c,d=map(lambda x:np.asarray(x,float),(a,b,c,d)); u=b-a; v=d-c; w=a-c
    A=float(u@u); B=float(u@v); C=float(v@v); D=float(u@w); E=float(v@w)
    candidates=[]
    if A>0:
        for t in (0.,1.): candidates.append((np.clip((B*t-D)/A,0,1),t))
    if C>0:
        for s in (0.,1.): candidates.append((s,np.clip((B*s+E)/C,0,1)))
    den=A*C-B*B
    if den>1e-14:
        s=(B*E-C*D)/den; t=(A*E-B*D)/den
        if 0<=s<=1 and 0<=t<=1:candidates.append((s,t))
    if not candidates:return float(np.linalg.norm(a-c))
    return min(float(np.linalg.norm(w+s*u-t*v)) for s,t in candidates)

def simplify(points):
    result=[]
    for p in points:
        p=tuple(float(x) for x in p)
        if result and dist(result[-1],p)<1e-8:continue
        if len(result)>1 and angle(result[-2],result[-1],p)<1e-6:result[-1]=p
        else:result.append(p)
    return result


def closest_on_segment_to_box(a,b,lo,hi):
    """Exact centerline witness for a box separation, including flat minima."""
    a=np.asarray(a,float);delta=np.subtract(b,a);lo=np.asarray(lo);hi=np.asarray(hi)
    cuts=[0.,1.]
    for j in range(3):
        if abs(delta[j])>1e-14:cuts.extend(t for t in ((lo[j]-a[j])/delta[j],(hi[j]-a[j])/delta[j]) if 0<t<1)
    cuts=sorted(set(cuts));best=None
    for left,right in zip(cuts,cuts[1:]):
        middle=a+delta*((left+right)/2);mask=(middle<lo)|(middle>hi);target=np.where(middle<lo,lo,hi);den=float(delta[mask]@delta[mask])
        t=left if not den else float(np.clip(delta[mask]@(target-a)[mask]/den,left,right))
        point=a+t*delta;gap=np.maximum(np.maximum(lo-point,point-hi),0);value=float(gap@gap)
        if best is None or value<best[0]:best=(value,point)
    return tuple(float(x) for x in best[1])


def closest_on_segment_to_segment(a,b,c,d):
    """Centerline witness on first segment; uses the same bounded quadratic as distance."""
    a,b,c,d=map(lambda x:np.asarray(x,float),(a,b,c,d));u=b-a;v=d-c;w=a-c
    A=float(u@u);B=float(u@v);C=float(v@v);D=float(u@w);E=float(v@w);pairs=[]
    if A>0:
        for t in (0.,1.):pairs.append((float(np.clip((B*t-D)/A,0,1)),t))
    if C>0:
        for s in (0.,1.):pairs.append((s,float(np.clip((B*s+E)/C,0,1))))
    den=A*C-B*B
    if den>1e-14:
        s=(B*E-C*D)/den;t=(A*E-B*D)/den
        if 0<=s<=1 and 0<=t<=1:pairs.append((s,t))
    if not pairs:return tuple(float(x) for x in a)
    s,t=min(pairs,key=lambda st:float(np.linalg.norm(w+st[0]*u-st[1]*v)))
    return tuple(float(x) for x in a+s*u)
