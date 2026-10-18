from .models import Model,Pipe,Obstacle,Bounds,Fitting

def scenarios():
    bounds=Bounds(min=(0,0,0),max=(12,8,5))
    assumptions=['Procedural development geometry; no official CAD or organizer standards supplied.','SI meters; Z up. Pipe envelopes are capsules, obstacles are axis-aligned boxes.','Illustrative fittings are unverified, not commercially approved.','Connections are fixed route endpoints; no equipment nozzle orientation metadata.']
    def pipe(points,**kw):return Pipe(id='P-101',system_id='Cooling water',editable=True,points=points,nominal_diameter=.1,outer_diameter=.114,fittings=[Fitting(angle=90,radius=.15,k=.9)],inner_diameter=.1,flow_rate=.004,density=998,viscosity=.001,roughness=.000045,**kw)
    other=Pipe(id='P-202',system_id='Fire main',nominal_diameter=.1,points=[(9.5,.5,4),(9.5,7.5,4)],outer_diameter=.3,editable=False)
    def model(id,name,p,obs=[],others=[]):return Model(id=id,name=name,bounds=bounds,pipes=[p,*others],obstacles=obs,assumptions=assumptions)
    base=[
      model('inefficient','A · Inefficient routing',pipe([(1,1,1),(1,6,1),(4,6,1),(4,3,1),(10,3,1),(10,1,1)]),[Obstacle(id='Pump skid',kind='equipment',min=(4,4,.2),max=(6,6,2))],[other]),
      model('congested','B · Congested machinery',pipe([(1,1,1),(10,1,1)]),[Obstacle(id='Engine',kind='equipment',min=(4,.3,.2),max=(6,3.5,3)),Obstacle(id='HVAC',kind='hvac',min=(3,4,2.7),max=(9,5,4.2)),Obstacle(id='Column',min=(8,2,0),max=(8.8,4,5)),Obstacle(id='Service exclusion',kind='restricted',editable=True,min=(2,6,.5),max=(3,7,2))],[other]),
      model('drainage','C · Gravity drainage',pipe([(1,1,3),(1,4,3),(10,4,2),(10,1,2)],category='drainage',min_slope=.02),[Obstacle(id='Tank',kind='equipment',min=(4,3,0),max=(7,6,3))],[other]),
      model('impossible','D · Sealed bulkhead',pipe([(1,1,1),(10,1,1)]),[Obstacle(id='Sealed bulkhead',min=(5,0,0),max=(6,8,5))]),
      model('efficient','E · Already efficient',pipe([(1,1,1),(10,1,1)]),[],[other])]
    for category,direction,start_z,end_z,id,name in [('drainage','downward',3.,2.4,'gravity-detour','F · Obstructed gravity drain'),('vent','upward',1.,1.6,'vent-rise','G · Rising vent detour')]:
        p=Pipe(id='G-101',system_id=category.title(),nominal_diameter=.1,outer_diameter=.114,editable=True,category=category,min_slope=.02,slope_direction=direction,points=[(1,3,start_z),(9,3,end_z)],fittings=[Fitting(id='ILLUSTRATIVE-90-TOL',angle=90,radius=.15,k=.9,angle_tolerance=.5,nominal_diameters=[.1],pipe_specifications=['DEMO'],provenance='Illustrative angle tolerance 0.5 degrees; not an approved commercial product')],specification='DEMO')
        base.append(Model(id=id,name=name,bounds=Bounds(min=(0,0,0),max=(10,8,5)),pipes=[p,Pipe(id='OVERHEAD',system_id='Fixed service',nominal_diameter=.1,points=[(1,7,4.4),(9,7,4.4)],outer_diameter=.2)],obstacles=[Obstacle(id='Tank',kind='equipment',min=(4,2,0),max=(6,4,3.8))],assumptions=[*assumptions,'Illustrative minimum pitch 0.02 m/m; gravity elbows permit explicitly declared 0.5 degree angle tolerance. No official catalog approval.']))
    base.append(Model(id='multi-system',name='H · Coordinated services',bounds=bounds,pipes=[pipe([(1,2,1),(10,2,1)]),Pipe(id='P-303',system_id='Auxiliary water',nominal_diameter=.1,outer_diameter=.114,inner_diameter=.1,fittings=[Fitting(angle=90,radius=.15,k=.9)],editable=True,points=[(6,1,1),(6,7,1)])],obstacles=[Obstacle(id='Machinery',kind='equipment',min=(4,3,.2),max=(5.5,5,3))],assumptions=assumptions))
    return base

def get_scenario(id):
    return next((m for m in scenarios() if m.id==id),None)
