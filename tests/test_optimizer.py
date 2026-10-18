import math
import pytest
from backend.models import Model
from backend.optimizer import optimize
from backend.scenarios import get_scenario
from backend.validation import validate
from backend.geometry import length


def test_inefficient_route_improves_without_mutating_source():
    original=get_scenario('inefficient'); frozen=original.model_dump()
    result=optimize(original)
    assert original.model_dump()==frozen
    assert result['candidates'][0]['status']=='accepted'
    proposed=Model.model_validate(result['model'])
    assert length(proposed.pipes[0].points)<length(original.pipes[0].points)
    assert not any(r['status']=='FAIL' for r in validate(original,proposed,'P-101'))
    assert proposed.pipes[1:]==original.pipes[1:]


def test_repeated_searches_have_identical_engineering_results():
    a=optimize(get_scenario('inefficient')); b=optimize(get_scenario('inefficient'))
    assert a['model']==b['model']
    assert a['issues']==b['issues']
    assert a['candidates'][0]['after']==b['candidates'][0]['after']


def test_impossible_bulkhead_never_returns_accepted_crossing():
    original=get_scenario('impossible')
    result=optimize(original,resolution=1)
    assert result['candidates'][0]['status']=='no_route'
    assert result['model']==original.model_dump()


def test_drainage_proposal_preserves_pitch_on_every_segment():
    original=get_scenario('drainage')
    result=optimize(original)
    assert result['candidates'][0]['status']=='accepted'
    proposed=Model.model_validate(result['model']); pipe=proposed.pipes[0]
    for a,b in zip(pipe.points,pipe.points[1:]):
        assert a[2]-b[2]+1e-8>=pipe.min_slope*math.hypot(b[0]-a[0],b[1]-a[1])
    assert not any(r['status']=='FAIL' for r in validate(original,proposed,pipe.id))


def test_already_efficient_route_is_not_made_longer():
    original=get_scenario('efficient')
    result=optimize(original)
    proposed=Model.model_validate(result['model'])
    assert length(proposed.pipes[0].points)==pytest.approx(length(original.pipes[0].points))


def test_fixed_system_cannot_be_selected_for_modification():
    original=get_scenario('inefficient')
    result=optimize(original,pipe_ids=['P-202'])
    assert result['candidates'][0]['status']=='rejected'
    assert result['model']==original.model_dump()


@pytest.mark.parametrize('kwargs',[{'resolution':0},{'clearance':-1},{'mode':'fictional'},{'pipe_ids':['absent']}])
def test_invalid_search_requests_rejected(kwargs):
    with pytest.raises(ValueError):
        optimize(get_scenario('efficient'),**kwargs)


def test_congested_search_detours_around_all_systems_and_reports_tradeoff():
    original=get_scenario('congested')
    result=optimize(original,resolution=1)
    candidate=result['candidates'][0]
    assert candidate['status']=='accepted'
    assert candidate['expanded']>0
    proposed=Model.model_validate(result['model'])
    assert not any(r['status']=='FAIL' for r in validate(original,proposed,'P-101'))
    assert proposed.pipes[1:]==original.pipes[1:]
    # The original crosses equipment; safety requires an honest length increase.
    assert candidate['after']['length']>candidate['before']['length']
