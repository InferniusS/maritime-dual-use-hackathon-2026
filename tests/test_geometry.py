import math
import pytest
from backend.geometry import length, segment_box_distance, segment_distance


def test_length_uses_actual_three_dimensional_route():
    assert length([(0, 0, 0), (3, 4, 0), (3, 4, 12)]) == pytest.approx(17)


@pytest.mark.parametrize('a,b,expected', [
    ((-2, .5, .5), (2, .5, .5), 0),
    ((-2, 2, .5), (2, 2, .5), 1),
    ((2, 2, 2), (3, 3, 3), math.sqrt(3)),
    ((.5, .5, .5), (.5, .5, .5), 0),
])
def test_segment_box_distance_including_interior_and_degenerate(a,b,expected):
    assert segment_box_distance(a,b,(0,0,0),(1,1,1)) == pytest.approx(expected)


@pytest.mark.parametrize('a,b,c,d,expected', [
    ((0,0,0),(2,0,0),(1,-1,0),(1,1,0),0),
    ((0,0,0),(2,0,0),(0,.15,0),(2,.15,0),.15),
    ((0,0,0),(1,0,0),(2,1,0),(2,2,0),math.sqrt(2)),
    ((0,0,0),(0,0,0),(1,0,0),(2,0,0),1),
])
def test_closed_segment_distance_is_symmetric(a,b,c,d,expected):
    assert segment_distance(a,b,c,d) == pytest.approx(expected)
    assert segment_distance(c,d,a,b) == pytest.approx(expected)
