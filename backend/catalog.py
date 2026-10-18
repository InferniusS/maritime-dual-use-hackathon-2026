"""Deterministic fitting selection. Catalog tolerances are explicit input, not standards."""
from math import tan,radians

def compatible(pipe,fitting,theta):
    return (abs(fitting.angle-theta)<=fitting.angle_tolerance+1e-8
            and (not fitting.nominal_diameters or any(abs(x-pipe.nominal_diameter)<1e-8 for x in fitting.nominal_diameters))
            and (not fitting.pipe_specifications or pipe.specification in fitting.pipe_specifications))

def select_fitting(pipe,theta):
    return next((f for f in pipe.fittings if compatible(pipe,f,theta)),None)

def takeout(fitting,theta):
    return fitting.radius*tan(radians(theta)/2)+fitting.connection_length+fitting.minimum_tangent
