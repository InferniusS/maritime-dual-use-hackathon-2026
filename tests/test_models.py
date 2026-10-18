import pytest
from pydantic import ValidationError
from backend.models import Bounds, Model, Pipe


def test_nonfinite_coordinates_and_zero_segments_rejected():
    for points in [[(0,0,0),(float('nan'),0,0)],[(0,0,0),(0,0,0)]]:
        with pytest.raises(ValidationError):
            Pipe(id='p',system_id='s',points=points)


def test_impossible_bore_and_ambiguous_duplicate_ids_rejected():
    with pytest.raises(ValidationError):
        Pipe(id='p',system_id='s',points=[(0,0,0),(1,0,0)],inner_diameter=.5,outer_diameter=.1)
    p=Pipe(id='p',system_id='s',points=[(1,1,1),(2,1,1)])
    with pytest.raises(ValidationError):
        Model(id='m',name='Invalid',bounds=Bounds(min=(0,0,0),max=(3,3,3)),pipes=[p,p])
