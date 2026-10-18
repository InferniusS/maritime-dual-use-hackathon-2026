"""Generated pressure straights obey horizontal/vertical routing in the stated frame."""
from copy import deepcopy
import math

import pytest

from pipe_router.assembly import plan_assembly, reconstruct_sampled_arcs
from pipe_router.workflow import run_workflow


def layout(points, axes=None):
    source = {"name": "pressure orientation fixture", "units": "mm", "provenance": "cad-derived",
              "system_type": "pressurized", "rule_profile": "fmg-v3",
              "bounds": {"min": [-5000] * 3, "max": [5000] * 3},
              "pipe": {"start": points[0], "end": points[-1], "diameter": 20,
                       "original_route": deepcopy(points)},
              "obstacles": [], "rules": {"clearance": 50.8, "grid_step": 250, "bend_penalty": 300},
              "proposal": {"mode": "rounded_pipe", "bend_radius_mm": 100}}
    if axes:
        source["engineering"] = {"units": "mm", "ship_axes": axes}
    return source


def finding(plan):
    return next(f for f in plan["manufacturing_assessment"]["findings"]
                if f["rule_id"] == "pressure_straight_orientation")


def z_up():
    return {"longitudinal": "+X", "transverse": "+Y", "up": "+Z", "confirmed": True}


def test_free_oblique_pressure_rise_is_blocked_even_with_missing_ship_axes():
    points = [[0, 0, 0], [1000, 500, 300]]
    plan = plan_assembly(layout(points), points, bend_radius_mm=100)
    assert plan["status"] == "blocked"
    assert plan["meshes"] == []
    check = finding(plan)
    assert check["status"] == "fail"
    assert check["component_ids"] == ["proposal-1"]
    assert check["measured"]["axis_confirmed"] is False
    assert check["measured"]["up_axis"] == "+Z"
    assert "provisional" in check["message"]


@pytest.mark.parametrize("angle", [0, 15, 37, 90, 137, 230])
def test_horizontal_pressure_run_allows_any_xy_direction(angle):
    angle = math.radians(angle)
    points = [[0, 0, 100], [1000 * math.cos(angle), 1000 * math.sin(angle), 100]]
    plan = plan_assembly(layout(points, z_up()), points)
    assert plan["status"] == "ready"
    assert finding(plan)["status"] == "pass"


@pytest.mark.parametrize("rise", [1000, -1000])
def test_vertical_pressure_run_can_rise_or_descend(rise):
    points = [[30, 20, 100], [30, 20, 100 + rise]]
    plan = plan_assembly(layout(points, z_up()), points)
    assert plan["status"] == "ready"
    assert finding(plan)["status"] == "pass"


def test_missing_frame_is_not_promoted_to_verified_orientation():
    points = [[0, 0, 0], [1000, 300, 0]]
    plan = plan_assembly(layout(points), points)
    assert plan["status"] == "ready"
    assert finding(plan)["status"] == "needs_review"
    assert not plan["manufacturing_assessment"]["mandatory_geometry_passed"]


def test_declared_y_up_frame_is_used_instead_of_guessing_z_up():
    points = [[0, 0, 0], [1000, 0, 1000]]
    source = layout(points)
    source["model_context"] = {"ship_axes": {"longitudinal": "+X", "transverse": "+Z", "up": "-Y", "confirmed": True}}
    plan = plan_assembly(source, points)
    assert plan["status"] == "ready"
    assert finding(plan)["status"] == "pass"
    assert finding(plan)["measured"]["axis_source"] == "model_context.ship_axes"
    assert finding(plan)["measured"]["up_axis"] == "-Y"


def test_engineering_frame_has_the_same_precedence_as_directional_rule_checks():
    points = [[0, 0, 0], [1000, 0, 1000]]
    source = layout(points, z_up())
    source["model_context"] = {"ship_axes": {"longitudinal": "+X", "transverse": "+Z", "up": "+Y", "confirmed": True}}
    plan = plan_assembly(source, points)
    assert plan["status"] == "blocked"
    assert finding(plan)["measured"]["axis_source"] == "engineering.ship_axes"


def test_unconfirmed_declared_up_direction_remains_review():
    points = [[0, 0, 0], [1000, 0, 1000]]
    axes = {"longitudinal": "+X", "transverse": "+Z", "up": "+Y", "confirmed": False}
    plan = plan_assembly(layout(points, axes), points)
    assert plan["status"] == "ready"
    assert finding(plan)["status"] == "needs_review"


def test_exact_original_oblique_stub_is_preserved_but_not_approved_as_compliant():
    vertex = [100., 0., 100.]
    trim = 100 * math.tan(math.pi / 8)
    tangent = [100 - trim / math.sqrt(2), 0., 100 - trim / math.sqrt(2)]
    points = [[0., 0., 0.], tangent, vertex, [1000., 0., 100.]]
    source = layout(points, z_up())
    source["protected_endpoints"] = [{"endpoint": "start", "points": deepcopy(points[:2]),
                                     "obstacle_ids": [], "max_distance": 110}]
    plan = plan_assembly(source, points, bend_radius_mm=100)
    assert plan["status"] == "ready"
    assert plan["centerline_points"][:2] == points[:2]
    assert finding(plan)["status"] == "needs_review"
    assert finding(plan)["measured"]["fixed_oblique_component_ids"] == ["proposal-1"]
    assert not plan["manufacturing_assessment"]["mandatory_geometry_passed"]


def test_collinear_free_extension_of_oblique_stub_is_not_exempted():
    points = [[0., 0., 0.], [50., 0., 50.], [1000., 0., 1000.], [2000., 0., 1000.]]
    source = layout(points, z_up())
    source["protected_endpoints"] = [{"endpoint": "start", "points": deepcopy(points[:2]),
                                     "obstacle_ids": [], "max_distance": 100}]
    plan = plan_assembly(source, points, bend_radius_mm=100)
    assert plan["status"] == "blocked"
    assert finding(plan)["status"] == "fail"
    records = finding(plan)["measured"]["oblique_segments"]
    assert any(record["unchanged_protected_stub"] for record in records)
    assert any(not record["unchanged_protected_stub"] for record in records)


def test_reconstructed_sampled_seed_cannot_introduce_free_oblique_pressure_straights(monkeypatch):
    # A quarter circle with tangent straights, tilted about Y. Recognition is
    # valid; accepting its two newly generated rising straights is not.
    points = [[0., 0., 0.], [400., 0., 0.]]
    points += [[400 + 100 * math.cos(-math.pi / 2 + math.pi / 2 * i / 12),
                100 + 100 * math.sin(-math.pi / 2 + math.pi / 2 * i / 12), 0.] for i in range(1, 13)]
    points[-1] = [500., 100., 0.]
    points.append([500., 1000., 0.])
    angle = .61
    points = [[x * math.cos(angle), y, -x * math.sin(angle)] for x, y, z in points]
    assert reconstruct_sampled_arcs(points)["points"]
    source = layout(points, z_up())
    source["proposal"]["bend_radius_mm"] = 200
    monkeypatch.setattr("pipe_router.proposal.MAX_ALTERNATIVES", 1)
    result = run_workflow(source)
    assert result["status"] == "no_route"
    assert result["candidate"] is None
    assert result["assembly"]["meshes"] == []
    assert any(f["code"] == "assembly_pressure_straight_orientation" for f in result["assembly"]["findings"])


def test_unverified_prototype_mode_keeps_its_explicit_nonchallenge_scope():
    points = [[0, 0, 0], [1000, 500, 300]]
    source = layout(points)
    source["rule_profile"] = "prototype-unverified"
    plan = plan_assembly(source, points)
    assert plan["status"] == "ready"
    assert not any(f["rule_id"] == "pressure_straight_orientation"
                   for f in plan["manufacturing_assessment"]["findings"])
