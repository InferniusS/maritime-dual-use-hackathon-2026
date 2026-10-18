"""Synthetic fabrication regressions; no production CAD artifacts are used."""
from copy import deepcopy
import math

import pytest

from pipe_router.network_manufacturing import manufacture_network


def span(points, **extra):
    return {"id":"run-a", "system_id":"cooling", "points":points,
            "nominal_diameter_mm":50, "outer_diameter_mm":60,
            "fitting_catalog":{"status":"provisional","source":"synthetic fixture only","radius_mm":200,"body_radius_mm":35,"angles_deg":[45,90]}, **extra}


def assessment(item):
    return manufacture_network({"units":"mm","spans":[item]})


def check(result, identifier):
    return next(item for item in result["checks"] if item["id"] == identifier)


def test_long_span_is_partitioned_into_real_pieces_without_mutation():
    item = span([[100,200,300],[15100,200,300]])
    before = deepcopy(item)
    result = assessment(item)
    pieces = result["pieces"]
    assert item == before
    assert len(pieces) == 3
    assert [piece["length_mm"] for piece in pieces] == pytest.approx([6096,6096,2808])
    assert pieces[0]["points"][0] == item["points"][0]
    assert pieces[-1]["points"][-1] == item["points"][-1]
    assert all(a["points"][-1] == b["points"][0] for a,b in zip(pieces,pieces[1:]))
    assert check(result,"manufacturing.length")["status"] == "PASS"
    assert check(result,"manufacturing.weld_spacing")["status"] == "UNVERIFIED"


def test_network_is_never_treated_as_one_piece():
    first = span([[0,0,0],[4000,0,0]])
    second = span([[4000,0,0],[8000,0,0]])
    second["id"] = "run-b"
    result = manufacture_network({"units":"mm","spans":[first,second]})
    assert len(result["pieces"]) == 2
    assert all(piece["length_mm"] == 4000 for piece in result["pieces"])
    assert not any(item["status"] == "FAIL" for item in result["checks"])


def test_developed_length_uses_true_arc_not_sharp_corner_or_chord():
    item = span([[0,0,0],[1000,0,0],[1000,1000,0]])
    result = assessment(item)
    piece = result["pieces"][0]
    assert piece["length_mm"] == pytest.approx(1600+100*math.pi)
    assert piece["arcs"][0]["radius_mm"] == 200
    assert piece["points"][0] == [0,0,0]
    assert piece["points"][-1] == [1000,1000,0]
    assert [1000,0,0] not in piece["points"]
    assert piece["bend_count"] == 1
    assert result["fittings"][0]["verification_status"] == "UNVERIFIED"


def test_more_than_three_bends_is_partitioned_on_straight_sections():
    result = assessment(span([[0,0,0],[1000,0,0],[1000,1000,0],[2000,1000,0],[2000,2000,0],[3000,2000,0]]))
    assert len(result["pieces"]) == 2
    assert sum(piece["bend_count"] for piece in result["pieces"]) == 4
    assert max(piece["bend_count"] for piece in result["pieces"]) == 3
    assert check(result,"manufacturing.bends")["status"] == "PASS"
    first, second = result["pieces"]
    assert first["points"][-1] == second["points"][0]


@pytest.mark.parametrize("stations,expected", [([0,24],"FAIL"),([0,25],"PASS"),([0,25,100],"PASS"),([0,1001],"FAIL"),([100,100],"FAIL")])
def test_explicit_weld_spacing_is_measured(stations,expected):
    result = assessment(span([[0,0,0],[1000,0,0]], manufacturing={"welds":[{"station_mm":station} for station in stations]}))
    assert check(result,"manufacturing.weld_spacing")["status"] == expected


def test_supplied_long_piece_is_reported_instead_of_silently_replaced():
    item = span([[0,0,0],[10000,0,0]], manufacturing={"pieces":[{"id":"existing-spool","points":[[0,0,0],[10000,0,0]]}]})
    result = assessment(item)
    assert len(result["pieces"]) == 1
    assert result["pieces"][0]["id"] == "existing-spool"
    assert check(result,"manufacturing.length")["status"] == "FAIL"
    assert check(result,"manufacturing.coverage")["status"] == "PASS"


def test_unknown_fitting_radius_and_unsupported_cutback_are_never_verified():
    angle = math.radians(22)
    item = span([[0,0,0],[1000,0,0],[1000+1000*math.cos(angle),1000*math.sin(angle),0]])
    item.pop("fitting_catalog")
    result = assessment(item)
    assert check(result,"manufacturing.length")["status"] == "UNVERIFIED"
    assert check(result,"manufacturing.cutback")["status"] == "FAIL"
    assert result["fittings"][0]["radius_mm"] is None
    assert result["fittings"][0]["verification_status"] == "UNVERIFIED"


def test_planarity_applies_per_piece_and_missing_tolerance_stays_unknown():
    points = [[0,0,0],[1000,0,0],[1000,1000,0],[1000,1000,1000]]
    result = assessment(span(points, manufacturing={"planarity_required":True,"planarity_tolerance_mm":1}))
    assert check(result,"manufacturing.planarity")["status"] == "FAIL"
    result = assessment(span(points, manufacturing={"planarity_required":True}))
    assert check(result,"manufacturing.planarity")["status"] == "UNVERIFIED"


def test_missing_catalog_still_retains_required_bend_count_and_location():
    item=span([[0,0,0],[1000,0,0],[1000,1000,0]])
    item.pop("fitting_catalog")
    result=assessment(item)
    assert result["pieces"][0]["bend_count"] == 1
    assert result["fittings"][0]["intersection"] == [1000,0,0]
    assert check(result,"manufacturing.bends")["status"] == "UNVERIFIED"


def test_new_cuts_respect_declared_existing_welds():
    item=span([[0,0,0],[15000,0,0]],manufacturing={"welds":[{"station_mm":6090},{"station_mm":12185}]})
    result=assessment(item)
    assert result["pieces"][0]["length_mm"] == 6090
    weld_check=check(result,"manufacturing.weld_spacing")
    assert weld_check["status"] == "PASS"
    assert all(value >=25 for value in weld_check["evidence"]["spacings_mm"])


@pytest.mark.parametrize("metadata",[
    {"welds":"invalid"}, {"welds":[{"station_mm":True}]}, {"welds":[{"station_mm":-1}]},
    {"welds":[{"station_mm":float("nan")}]}, {"pieces":[{"points":[[0,0,0],[float("inf"),0,0]]}]},
    {"pieces":[{"points":[[0,0,0],[1000,0,0]],"welds":[{"station_mm":-1}]}]},
    {"pieces":[{"points":[[0,0,0],[1000,0,0]],"start_station_mm":True}]},
    {"planarity_required":"yes"}, {"process_verified":"yes"},
])
def test_untrusted_nested_manufacturing_metadata_is_rejected(metadata):
    with pytest.raises(ValueError):
        assessment(span([[0,0,0],[1000,0,0]],manufacturing=metadata))


def test_supplied_length_and_bend_count_are_recomputed_from_geometry():
    result=assessment(span([[0,0,0],[1000,0,0]],manufacturing={"pieces":[{"id":"supplied","points":[[0,0,0],[1000,0,0]],"length_mm":-100,"bend_count":900}]}))
    assert result["pieces"][0]["length_mm"] == 1000
    assert result["pieces"][0]["bend_count"] == 0
    assert check(result,"manufacturing.weld_spacing")["status"] == "UNVERIFIED"


def test_weld_spacing_is_checked_across_adjacent_pieces():
    pieces=[{"points":[[0,0,0],[1000,0,0]],"welds":[{"station_mm":990}]},
            {"points":[[1000,0,0],[2000,0,0]],"welds":[{"station_mm":10}]}]
    result=assessment(span([[0,0,0],[2000,0,0]],manufacturing={"pieces":pieces}))
    weld_check=check(result,"manufacturing.weld_spacing")
    assert weld_check["status"] == "FAIL"
    assert weld_check["evidence"]["spacings_mm"] == [20]
    assert check(result,"manufacturing.coverage")["status"] == "PASS"


def test_gaps_overlaps_and_false_station_metadata_cannot_pass():
    pieces=[{"points":[[0,0,0],[500,0,0]],"start_station_mm":30},
            {"points":[[600,0,0],[1000,0,0]]}]
    result=assessment(span([[0,0,0],[1000,0,0]],manufacturing={"pieces":pieces}))
    assert check(result,"manufacturing.coverage")["status"] == "FAIL"


def test_extremely_long_span_fails_before_generating_cut_geometry(monkeypatch):
    from pipe_router import network_manufacturing as module
    def never_generate(*args,**kwargs):
        pytest.fail("An over-budget takeoff must be rejected before any piece geometry is generated")
    monkeypatch.setattr(module,"_path_slice",never_generate)
    with pytest.raises(module.ManufacturingBudgetExceeded,match="resource budget"):
        assessment(span([[-1e9,0,0],[1e9,0,0]]))


def test_network_piece_budget_is_enforced_without_partial_results():
    from pipe_router.network_manufacturing import ManufacturingBudgetExceeded
    spans=[]
    for index in range(5):
        spans.append(span([[0,index*1000,0],[6096*256,index*1000,0]],id=f"span-{index}"))
    with pytest.raises(ManufacturingBudgetExceeded,match="network exceeds 1024 pieces"):
        manufacture_network({"units":"mm","spans":spans})


def test_supplied_piece_lists_have_the_same_per_span_budget():
    from pipe_router.network_manufacturing import ManufacturingBudgetExceeded
    pieces=[{"points":[[0,0,0],[1,0,0]]} for _ in range(257)]
    with pytest.raises(ManufacturingBudgetExceeded,match="256 pieces"):
        assessment(span([[0,0,0],[1000,0,0]],manufacturing={"pieces":pieces}))


def test_nested_weld_arrays_have_one_aggregate_span_budget():
    from pipe_router.network_manufacturing import ManufacturingBudgetExceeded
    pieces=[{"points":[[0,0,0],[1000,0,0]],"welds":[{"station_mm":1}]*2049} for _ in range(2)]
    with pytest.raises(ManufacturingBudgetExceeded,match="weld metadata"):
        assessment(span([[0,0,0],[1000,0,0]],manufacturing={"pieces":pieces}))
