import math
import pytest
from backend.models import Model,Pipe,Obstacle,Bounds
from backend.step_adapter import available,export_step,inspect_step
from backend.exports import export_data

pytestmark=pytest.mark.skipif(not available(),reason='Optional isolated OpenCascade kernel not installed')

def test_step_named_solids_roundtrip_preserves_meters_bore_volume_and_obstacles():
    pipe=Pipe(id='pipe-one',system_id='water',outer_diameter=.2,inner_diameter=.16,points=[(1,1,1),(5,1,1)])
    model=Model(id='step-test',name='Unit verification',bounds=Bounds(min=(0,0,0),max=(8,4,4)),pipes=[pipe],obstacles=[Obstacle(id='equipment',kind='equipment',min=(6,1,1),max=(7,2,2))])
    text,mime=export_data(model,model,'step')
    assert mime=='application/step'
    assert 'PIPE_pipe-one' in text and 'ENV_equipment' in text
    assert '.METRE.' in text
    result=inspect_step(text)
    assert result['solids']==2
    assert result['bounds_m']==pytest.approx([1,.9,.9,7,2,2],abs=1e-6)
    assert result['volume_m3']==pytest.approx(1+math.pi*(.1**2-.08**2)*4,rel=1e-6)


def test_step_rejects_invalid_size_modification_before_kernel_execution():
    from backend.scenarios import get_scenario
    model=get_scenario('efficient');proposed=model.model_copy(deep=True);proposed.pipes[0].outer_diameter=.9
    with pytest.raises(ValueError,match='invalid modification'):export_data(model,proposed,'step')
