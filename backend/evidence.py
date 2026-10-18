"""Measured conservative clearances for engineering review (meters)."""
from .geometry import segment_box_distance
from .fitting_geometry import fitting_envelopes,pipe_pair_clearance,geometry_metadata_missing
from .work_limits import checkpoint

def clearance_evidence(model,pipe,clearance):
    checks=[];r=pipe.outer_diameter/2
    for obstacle in model.obstacles:
        checkpoint('clearance evidence')
        measured=min(segment_box_distance(a,b,obstacle.min,obstacle.max)-r for a,b in zip(pipe.points,pipe.points[1:]))
        for fitting in fitting_envelopes(pipe):
            measured=min(measured,segment_box_distance(fitting.center,fitting.center,obstacle.min,obstacle.max)-fitting.radius)
        missing=geometry_metadata_missing(pipe)
        checks.append(dict(object_ids=[pipe.id,obstacle.id],kind=obstacle.kind,measured=measured,required=clearance,status='UNVERIFIED' if missing else 'PASS' if measured+1e-8>=clearance else 'FAIL',metadata_missing=missing,units='m',method='Capsule/box distance with angle-bounded fittings'))
    for other in model.pipes:
        checkpoint('neighbor clearance evidence')
        if other.id==pipe.id:continue
        measured=pipe_pair_clearance(pipe,other)
        missing=[*(pipe.id+'.'+name for name in geometry_metadata_missing(pipe)),*(other.id+'.'+name for name in geometry_metadata_missing(other))]
        checks.append(dict(object_ids=[pipe.id,other.id],kind='pipe',measured=measured,required=clearance,status='UNVERIFIED' if missing else 'PASS' if measured+1e-8>=clearance else 'FAIL',metadata_missing=missing,units='m',method='Symmetric capsule/sphere separation with angle-bounded fittings'))
    return checks
