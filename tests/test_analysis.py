from backend.scenarios import get_scenario
from backend.validation import analyze
from backend.models import Model
from backend.optimizer import optimize

def test_inefficiency_requires_computed_feasible_reference():
    original=get_scenario('inefficient')
    issues=analyze(original)['issues']
    reference=next(i for i in issues if i['type']=='inefficiency')
    assert reference['measured']==19
    assert reference['required']==9
    assert reference['verification_status']=='verified'
    result=optimize(original)
    assert not any(i['type']=='inefficiency' for i in analyze(Model.model_validate(result['model']))['issues'])

def test_obstructed_shortcut_is_not_reported_as_feasible_reference():
    assert not any(i['type']=='inefficiency' for i in analyze(get_scenario('congested'))['issues'])
