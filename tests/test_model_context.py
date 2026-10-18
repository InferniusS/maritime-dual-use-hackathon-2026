"""Model context never guesses alignment, coverage or containment between points."""
from copy import deepcopy

import pytest

from pipe_router.model_context import (
    COVERAGE_CATEGORIES, apply_model_context, obstacles_from_import,
    segment_allowed, validate_allowed_spaces,
)


@pytest.fixture
def layout():
    return {"name": "Context test", "provenance": "synthetic", "units": "mm",
            "system_type": "pressurized", "bounds": {"min": [-100, -100, -100], "max": [1100, 100, 100]},
            "pipe": {"start": [0, 0, 0], "end": [1000, 0, 0], "diameter": 10,
                     "original_route": [[0, 0, 0], [1000, 0, 0]]},
            "obstacles": [], "rules": {"clearance": 50.8, "grid_step": 100, "bend_penalty": 0}}


@pytest.fixture
def context():
    return {"schema_version": 1, "units": "mm",
            "coordinate_frame": {"id": "ship-global", "shared_coordinates_confirmed": True},
            "ship_axes": {"longitudinal": "+X", "transverse": "+Y", "up": "+Z", "confirmed": True},
            "coverage": {name: {"included": True, "reviewed": True, "source_refs": [name + ".dwg"]}
                         for name in COVERAGE_CATEGORIES},
            "allowed_spaces": [{"id": "room", "min": [-10, -10, -10], "max": [1010, 10, 10], "source": "designer drawing"}]}


def compact_drawing(obstacles, **extra):
    return {"id": "structural", "units": "mm", "category": "structure", "coordinate_frame_id": "ship-global",
            "shared_coordinates_confirmed": True, "obstacles": obstacles, **extra}


def box(identifier="wall", minimum=(400, -20, -20), maximum=(500, 20, 20), **extra):
    return {"id": identifier, "min": list(minimum), "max": list(maximum), "source": "structure.dwg", **extra}


def test_apply_copies_geometry_and_preserves_uncertainty(layout, context):
    before = deepcopy((layout, context))
    layout["cad_context"] = {"connectivity_verified": False, "complete_ship_geometry": False}
    result = apply_model_context(layout, context)
    assert result["model_context"]["coverage_declared_complete"]
    assert result["model_context"]["requires_review"]
    assert not result["cad_context"]["connectivity_verified"]
    assert not result["cad_context"]["complete_ship_geometry"]
    assert context == before[1]
    result["model_context"]["allowed_spaces"][0]["min"][0] = -999
    assert context["allowed_spaces"][0]["min"][0] == -10


def test_layout_and_context_units_are_independently_scaled_once(layout, context):
    layout.update(units="m", bounds={"min": [-.1, -.1, -.1], "max": [1.1, .1, .1]})
    layout["pipe"] = {"start": [0, 0, 0], "end": [1, 0, 0], "diameter": .01, "original_route": [[0, 0, 0], [1, 0, 0]]}
    layout["rules"] = {"clearance": .0508, "grid_step": .1, "bend_penalty": 0}
    context.update(units="in", allowed_spaces=[box("room", (-1, -1, -1), (41, 1, 1))], boundary_clearance_mm=2)
    first = apply_model_context(layout, context)
    assert first["pipe"]["end"] == [1000, 0, 0]
    assert first["pipe"]["diameter"] == 10
    assert first["model_context"]["allowed_spaces"][0]["min"] == [-25.4] * 3
    assert first["model_context"]["boundary_clearance_mm"] == 2
    second = apply_model_context(first, first["model_context"])
    assert second == first


@pytest.mark.parametrize("confirmation", [False, None, "true", 1])
def test_alignment_must_be_explicit_boolean(layout, context, confirmation):
    context["coordinate_frame"]["shared_coordinates_confirmed"] = confirmation
    with pytest.raises(ValueError, match="alignment"):
        apply_model_context(layout, context)


def test_missing_units_rejected(layout, context):
    del context["units"]
    with pytest.raises(ValueError, match="explicit units"):
        apply_model_context(layout, context)


def test_missing_coverage_and_axes_are_review_not_pass(layout, context):
    context.pop("ship_axes")
    context["coverage"] = {"structure": {"included": True, "reviewed": True}}
    result = apply_model_context(layout, context)["model_context"]
    assert not result["coverage_declared_complete"]
    assert sum(f["rule_id"].startswith("coverage_") for f in result["findings"]) == 5
    assert all(f["status"] == "needs_review" for f in result["findings"])
    assert any(f["rule_id"] == "ship_axes" for f in result["findings"])


def test_invalid_axes_fail_before_using_directional_rules(layout, context):
    context["ship_axes"]["transverse"] = "-X"
    with pytest.raises(ValueError, match="different signed axes"):
        apply_model_context(layout, context)


def test_allowed_space_checks_pipe_radius_without_adding_shock_clearance(layout, context):
    result = apply_model_context(layout, context)
    assert segment_allowed(result, [0, 0, 0], [1000, 0, 0])
    assert not segment_allowed(result, [0, 6, 0], [1000, 6, 0])
    context["boundary_clearance_mm"] = 6
    result = apply_model_context(layout, context)
    assert not segment_allowed(result, [0, 0, 0], [1000, 0, 0])


def test_continuous_check_rejects_segment_whose_vertices_are_in_separate_spaces(layout, context):
    context["allowed_spaces"] = [box("left", (-10, -10, -10), (100, 10, 10)),
                                 box("right", (900, -10, -10), (1010, 10, 10))]
    result = apply_model_context(layout, context)
    findings = validate_allowed_spaces(result, result["pipe"]["original_route"])
    assert findings[0]["status"] == "fail"
    assert findings[0]["segment_index"] == 0


def test_overlapping_space_union_covers_whole_segment(layout, context):
    context["allowed_spaces"] = [box("left", (-10, -10, -10), (510, 10, 10)),
                                 box("right", (490, -10, -10), (1010, 10, 10))]
    result = apply_model_context(layout, context)
    assert segment_allowed(result, [0, 0, 0], [1000, 0, 0])
    assert segment_allowed(result, [1000, 0, 0], [0, 0, 0])


def test_tiny_true_space_gap_is_not_accepted_by_numeric_tolerance(layout, context):
    context["allowed_spaces"] = [box("left", (-10, -10, -10), (505, 10, 10)),
                                 box("right", (495.000001, -10, -10), (1010, 10, 10))]
    result = apply_model_context(layout, context)
    assert not segment_allowed(result, [0, 0, 0], [1000, 0, 0])


def test_required_space_is_visited_between_vertices(layout, context):
    context["allowed_spaces"].append(box("required", (400, -10, -10), (500, 10, 10)))
    context["required_space_ids"] = ["required"]
    result = apply_model_context(layout, context)
    findings = validate_allowed_spaces(result, result["pipe"]["original_route"])
    assert next(f for f in findings if f["rule_id"] == "required_space")["status"] == "pass"
    context["allowed_spaces"][-1] = box("required", (400, 50, -10), (500, 80, 10))
    result = apply_model_context(layout, context)
    assert next(f for f in validate_allowed_spaces(result, result["pipe"]["original_route"])
                if f["rule_id"] == "required_space")["status"] == "fail"


def test_grazing_required_space_corner_does_not_count_as_visiting(layout, context):
    context["allowed_spaces"].append(box("required", (400, -5, -10), (410, 5, 10)))
    context["required_space_ids"] = ["required"]
    result = apply_model_context(layout, context)
    assert next(f for f in validate_allowed_spaces(result, result["pipe"]["original_route"])
                if f["rule_id"] == "required_space")["status"] == "fail"


def test_unknown_required_space_rejected(layout, context):
    context["required_space_ids"] = ["missing"]
    with pytest.raises(ValueError, match="supplied allowed spaces"):
        apply_model_context(layout, context)


def test_missing_allowed_spaces_never_reports_pass(layout, context):
    context.pop("allowed_spaces")
    result = apply_model_context(layout, context)
    assert segment_allowed(result, [0, 0, 0], [1000, 0, 0])
    assert validate_allowed_spaces(result, [[0, 0, 0], [1000, 0, 0]])[0]["status"] == "needs_review"


def test_keep_outs_are_real_collision_obstacles_and_apply_is_idempotent(layout, context):
    context["keep_outs"] = [box("walkway", kind="walkway")]
    result = apply_model_context(layout, context)
    assert result["obstacles"][0]["id"] == "context:keepout:walkway"
    assert apply_model_context(result, result["model_context"]) == result
    from pipe_router.workflow import validate_layout_route
    assert not validate_layout_route(result, result["pipe"]["original_route"])["valid"]


def test_canonical_context_can_be_reused_on_a_fresh_layout_without_duplicate_provenance(layout, context):
    context["keep_outs"] = [box("walkway", kind="walkway")]
    result = apply_model_context(layout, context)
    fresh = deepcopy(layout)
    fresh["model_context"] = result["model_context"]
    reused = apply_model_context(fresh, fresh["model_context"])
    assert len(reused["obstacles"]) == 1
    assert len(reused["model_context"]["obstacle_sources"]) == 1


@pytest.mark.parametrize("extra", [{"capsules": [{"start": [450, 0, 0], "end": [451, 0, 0], "radius": 1}]},
                                  {"source_identity": {"drawing_sha256": "target", "source_handle": "A", "instance_path": []}}])
def test_keep_out_cannot_be_shrunk_or_excluded_as_a_selected_component(layout, context, extra):
    context["keep_outs"] = [box("walkway", kind="walkway", **extra)]
    with pytest.raises(ValueError, match="complete exclusion boxes"):
        apply_model_context(layout, context)


def test_supplemental_cannot_auto_align(layout, context):
    context["additional_drawings"] = [compact_drawing([box()], coordinate_frame_id="other")]
    with pytest.raises(ValueError, match="coordinate frame"):
        apply_model_context(layout, context)


def test_supplemental_units_capsules_and_provenance(layout, context):
    obstacle = box("pipe", (.4, -.01, -.01), (.6, .01, .01),
                   capsules=[{"start": [.41, 0, 0], "end": [.59, 0, 0], "radius": .01}])
    context["additional_drawings"] = [compact_drawing([obstacle], units="m")]
    result = apply_model_context(layout, context)
    obstacle = result["obstacles"][0]
    assert obstacle["min"] == [400, -10, -10]
    assert obstacle["capsules"][0]["radius"] == 10
    assert result["model_context"]["obstacle_sources"][0]["source"] == "structure.dwg"
    assert apply_model_context(result, result["model_context"]) == result


def test_supplemental_excludes_only_exact_target_source_instance(layout, context):
    layout["cad_context"] = {"source_sha256": "target-hash", "selected_source_instances": [{"source_handle": "A", "instance_path": []}]}
    def source(sha, instance):
        return {"drawing_sha256": sha, "source_handle": "A", "instance_path": instance}
    context["additional_drawings"] = [compact_drawing([
        box("selected", source_identity=source("target-hash", [])),
        box("other-instance", source_identity=source("target-hash", ["INSERT-1"])),
        box("other-drawing", source_identity=source("other-hash", [])),
    ])]
    result = apply_model_context(layout, context)
    assert len(result["obstacles"]) == 2
    assert all("selected" not in obstacle["id"] for obstacle in result["obstacles"])
    assert len(result["model_context"]["excluded_selected_source_instances"]) == 1


def test_only_truly_outside_obstacles_are_filtered(layout, context):
    context["additional_drawings"] = [compact_drawing([box(), box("far", (2000, 0, 0), (2010, 10, 10))])]
    result = apply_model_context(layout, context)
    assert len(result["obstacles"]) == 1
    assert len(result["model_context"]["additional_drawings"][0]["obstacles"]) == 2


def test_obstacle_limit_fails_instead_of_dropping_surroundings(layout, context):
    context["additional_drawings"] = [compact_drawing([box(str(i)) for i in range(257)])]
    with pytest.raises(ValueError, match="No geometry was dropped"):
        apply_model_context(layout, context)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 1e13, True])
def test_malformed_context_geometry_rejected(layout, context, bad):
    context["allowed_spaces"][0]["min"][0] = bad
    with pytest.raises(ValueError):
        apply_model_context(layout, context)


def test_same_ids_with_changed_geometry_cannot_silently_replace_context(layout, context):
    context["keep_outs"] = [box("walkway", kind="walkway")]
    result = apply_model_context(layout, context)
    changed = deepcopy(result["model_context"])
    changed["keep_outs"][0]["min"][0] -= 1
    with pytest.raises(ValueError, match="conflicts"):
        apply_model_context(result, changed)


def test_reapplying_a_context_cannot_remove_owned_obstacles(layout, context):
    context["keep_outs"] = [box("walkway", kind="walkway")]
    result = apply_model_context(layout, context)
    changed = deepcopy(result["model_context"])
    changed["keep_outs"] = []
    with pytest.raises(ValueError, match="context geometry changed"):
        apply_model_context(result, changed)


def test_malformed_source_identity_rejected(layout, context):
    context["additional_drawings"] = [compact_drawing([box(source_identity={
        "drawing_sha256": "target", "source_handle": "A", "instance_path": "not-a-list"})])]
    with pytest.raises(ValueError, match="instance_path"):
        apply_model_context(layout, context)


def test_compact_import_keeps_all_solid_instances_and_review_evidence():
    cad = {"provenance": "cad", "units": "mm", "source_sha256": "source-hash", "source": "structure.dxf",
           "unit_assumption": True, "counts": {"solid_instances": 3},
           "objects": [{"id": "one", "type": "3DSOLID", "source_handle": "A", "instance_path": [],
                        "geometry": {"kind": "mesh", "vertices": [[0, 0, 0], [10, 10, 10]], "faces": []}},
                       {"id": "two", "type": "3DSOLID", "source_handle": "A", "instance_path": ["INSERT-1"],
                        "geometry": {"kind": "mesh", "vertices": [[20, 0, 0], [30, 10, 10]], "faces": []}}]}
    with pytest.raises(ValueError, match="shared coordinate"):
        obstacles_from_import(cad, drawing_id="structure", category="structure", coordinate_frame_id="ship-global")
    record = obstacles_from_import(cad, drawing_id="structure", category="structure", coordinate_frame_id="ship-global", shared_coordinates_confirmed=True)
    assert len(record["obstacles"]) == 2
    assert record["coverage"]["unread_solids"] == 1
    assert record["source_unit_assumption"]
    assert record["obstacles"][1]["source_identity"]["instance_path"] == ["INSERT-1"]
