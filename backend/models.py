from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
import math
Point = tuple[float,float,float]
PIPE_DIMENSION_FIELDS = ('nominal_diameter','outer_diameter','inner_diameter')
FITTING_METADATA_FIELDS = ('angle','radius','k')

def _capture_metadata(model_type,data,fields):
    """Retain missing input provenance even after defaults enter a serialized model.

    Legacy numbers remain useful for illustrative geometry. They do not become
    supplied engineering data merely because model_dump includes their values.
    A caller replacing an assumption must supply its value and deliberately
    remove that field from metadata_missing in the new validated input.
    """
    if not isinstance(data,dict):return data
    previous=data.get('metadata_missing',[])
    if not isinstance(previous,list):return data  # Let Pydantic report the invalid field.
    result=dict(data);missing=list(previous)
    for name in fields:
        if name not in data or data[name] is None:
            if name not in missing:missing.append(name)
            default=model_type.model_fields[name].default
            if name in data and data[name] is None and default is not None:
                result[name]=default
    result['metadata_missing']=missing
    return result

def _metadata_status(model,fields,tracked):
    if isinstance(fields,str):fields=(fields,)
    fields=tuple(fields)
    if any(name not in tracked for name in fields):
        raise ValueError('Unknown engineering metadata field')
    missing=[name for name in fields if name in model.metadata_missing or getattr(model,name) is None]
    origins={}
    for name in fields:
        value=getattr(model,name);default=type(model).model_fields[name].default
        origins[name]=('supplied' if name not in missing else 'missing' if value is None
                       else 'legacy_default' if default is not None and value==default else 'unverified')
    return dict(status='UNVERIFIED' if missing else 'PASS',missing=missing,origins=origins)

class CheckedModel(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra='forbid')
class Bounds(CheckedModel):
    min: Point
    max: Point
    @model_validator(mode='after')
    def valid(self):
        if any(not math.isfinite(v) for v in (*self.min,*self.max)) or any(a>=b for a,b in zip(self.min,self.max)): raise ValueError('Invalid bounds')
        return self
class Obstacle(Bounds):
    id: str
    kind: str = 'structure'
    editable: bool = False
class Fitting(CheckedModel):
    id: str = 'DEMO-90'
    angle: float = Field(default=90,gt=0,lt=180)
    radius: float = Field(default=.15,gt=0)
    approved: bool = False
    k: float = Field(default=.9,ge=0)
    angle_tolerance: float = Field(default=.01,ge=0,le=2)
    nominal_diameters: list[float] = Field(default_factory=list,max_length=100)
    pipe_specifications: list[str] = Field(default_factory=list,max_length=100)
    connection_length: float = Field(default=0,ge=0)
    minimum_tangent: float = Field(default=0,ge=0)
    provenance: str = 'Illustrative development catalog; approval unverified'
    metadata_missing: list[Literal['angle','radius','k']] = Field(default_factory=list,max_length=3)
    @model_validator(mode='before')
    @classmethod
    def capture_metadata(cls,data):
        return _capture_metadata(cls,data,FITTING_METADATA_FIELDS)
    def metadata_status(self,fields=FITTING_METADATA_FIELDS):
        """Input presence only; a supplied value does not establish catalog approval."""
        return _metadata_status(self,fields,FITTING_METADATA_FIELDS)
    @model_validator(mode='after')
    def sizes(self):
        if any(x<=0 or not math.isfinite(x) for x in self.nominal_diameters):raise ValueError('Catalog diameters must be positive and finite')
        return self
class Pipe(CheckedModel):
    id: str
    system_id: str
    nominal_diameter: float = Field(default=.1,gt=0)
    outer_diameter: float = Field(default=.114,gt=0)
    inner_diameter: float | None = Field(default=None,gt=0)
    points: list[Point] = Field(min_length=2,max_length=1000)
    editable: bool = False
    category: Literal['pressure','drainage','vent'] = 'pressure'
    min_slope: float | None = Field(default=None,ge=0)
    slope_direction: Literal['downward','upward'] = 'downward'
    specification: str | None = None
    flow_rate: float | None = Field(default=None,ge=0)
    density: float | None = Field(default=None,gt=0)
    viscosity: float | None = Field(default=None,gt=0)
    roughness: float | None = Field(default=None,ge=0)
    fittings: list[Fitting] = Field(default_factory=lambda:[Fitting()],max_length=100)
    metadata_missing: list[Literal['nominal_diameter','outer_diameter','inner_diameter']] = Field(default_factory=list,max_length=3)
    @model_validator(mode='before')
    @classmethod
    def capture_metadata(cls,data):
        return _capture_metadata(cls,data,PIPE_DIMENSION_FIELDS)
    def dimension_metadata(self):
        return _metadata_status(self,PIPE_DIMENSION_FIELDS,PIPE_DIMENSION_FIELDS)
    @model_validator(mode='after')
    def finite(self):
        if self.category=='drainage' and self.slope_direction!='downward':raise ValueError('Gravity drainage must fall in listed flow direction; upward pitch is available for explicitly configured vent systems')
        if any(not math.isfinite(x) for p in self.points for x in p): raise ValueError('Coordinates must be finite')
        if any(math.dist(a,b)<1e-9 for a,b in zip(self.points,self.points[1:])): raise ValueError('Zero length segment')
        if self.inner_diameter and self.inner_diameter>self.outer_diameter: raise ValueError('Inner diameter exceeds outer diameter')
        return self
class Model(CheckedModel):
    id: str
    name: str
    units: Literal['m'] = 'm'
    coordinate_system: str = 'Right-handed, Z up; source coordinates retained'
    bounds: Bounds
    pipes: list[Pipe] = Field(max_length=100)
    obstacles: list[Obstacle] = Field(default_factory=list,max_length=1000)
    assumptions: list[str] = Field(default_factory=list)
    @model_validator(mode='after')
    def ids(self):
        ids=[p.id for p in self.pipes]+[o.id for o in self.obstacles]
        if len(ids)!=len(set(ids)): raise ValueError('Object IDs must be unique')
        return self
