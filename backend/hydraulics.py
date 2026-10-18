from math import pi, log10
from .catalog import select_fitting
from .geometry import length,bends,angle

def hydraulic(pipe):
    missing=[key for key in ('inner_diameter','flow_rate','density','viscosity','roughness') if getattr(pipe,key) is None]
    if 'inner_diameter' in pipe.dimension_metadata()['missing'] and 'inner_diameter' not in missing:missing.append('inner_diameter')
    if missing:
        return {'status':'UNVERIFIED','reason':'Requires supplied inner diameter, flow rate, density, viscosity and roughness','missing_fields':missing}
    if pipe.category!='pressure':return {'status':'UNVERIFIED','reason':'Full-pipe pressure model does not establish gravity drainage capacity'}
    if bends(pipe.points) and not pipe.fittings:return {'status':'UNVERIFIED','reason':'Missing fitting coefficients'}
    K=0.;coefficients=[]
    for a,b,c in zip(pipe.points,pipe.points[1:],pipe.points[2:]):
        theta=angle(a,b,c)
        if theta<1e-5:continue
        fitting=select_fitting(pipe,theta)
        if fitting is None:return {'status':'UNVERIFIED','reason':'Missing loss coefficient for actual fitting angle'}
        status=fitting.metadata_status(('k',))
        if status['status']!='PASS':
            return {'status':'UNVERIFIED','reason':'Actual bend requires a supplied fitting loss coefficient; illustrative defaults are not measured catalog data','missing_fields':[f'fittings[{fitting.id}].k'],'coefficient_metadata':status}
        K+=fitting.k
        coefficients.append(dict(fitting_id=fitting.id,k=fitting.k,input_origin=status['origins']['k'],catalog_approved=fitting.approved,provenance=fitting.provenance))
    D=pipe.inner_diameter; v=pipe.flow_rate/(pi*D*D/4); Re=pipe.density*v*D/pipe.viscosity
    if Re==0:return {'status':'PASS','reynolds':0,'pressure_loss_pa':0,'velocity':0,'friction_factor':0,'fitting_coefficients':coefficients}
    if 2300<=Re<=4000:return {'status':'UNVERIFIED','reason':'Transitional Reynolds number'}
    f=64/Re if Re<2300 else .25/(log10(pipe.roughness/(3.7*D)+5.74/Re**.9)**2)
    dynamic=pipe.density*v*v/2
    return {'status':'PASS','method':'Darcy-Weisbach; laminar or Swamee-Jain; straight centerline length, supplied catalog K','velocity':v,'reynolds':Re,'friction_factor':f,'pressure_loss_pa':(f*length(pipe.points)/D+K)*dynamic,'fitting_coefficients':coefficients}
