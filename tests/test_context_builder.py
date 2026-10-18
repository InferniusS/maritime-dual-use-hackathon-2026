"""Local context building requires explicit frame and units and never edits CAD."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path

import ezdxf
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec_module = importlib.util.spec_from_file_location("context_builder", ROOT / "scripts/build_model_context.py")
builder = importlib.util.module_from_spec(spec_module)
spec_module.loader.exec_module(builder)


@pytest.fixture
def manifest():
    return {"model_context": {
        "schema_version": 1, "units": "m",
        "coordinate_frame": {"id": "ship-global", "shared_coordinates_confirmed": True},
        "ship_axes": {"longitudinal": None, "transverse": None, "up": None, "confirmed": False},
        "coverage": {"structure": {"included": False, "reviewed": False, "source_refs": []}},
        "allowed_spaces": [{"id": "room", "min": [0, 0, 0], "max": [10, 5, 3], "source": "review drawing"}],
        "keep_outs": [{"id": "walkway", "kind": "walkway", "min": [1, 1, 0], "max": [2, 2, 1.96], "source": "walkway plan"}],
        "drawing_files": [{"id": "structure", "category": "structure", "path": "source.dxf", "source_units": "mm",
                           "shared_coordinates_confirmed": True}]}}


def write_spec(tmp_path, value):
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def source_dxf(tmp_path):
    # A simple line is intentionally unsupported as a solid obstacle. It must
    # never cause the builder to claim that complete structure was imported.
    path = tmp_path / "source.dxf"
    doc = ezdxf.new("R2010")
    doc.modelspace().add_line((0, 0, 0), (100, 0, 0))
    doc.saveas(path)
    return path


def test_build_reads_real_dxf_and_preserves_unknown_coverage(tmp_path, manifest):
    source = source_dxf(tmp_path)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    spec = write_spec(tmp_path, manifest)
    output = tmp_path / "generated/context.json"
    result = builder.build_context(spec, output)
    assert output.is_file()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
    assert result["model_context"]["units"] == "mm"
    assert result["model_context"]["allowed_spaces"][0]["max"] == [10000, 5000, 3000]
    assert result["model_context"]["keep_outs"][0]["max"] == [2000, 2000, 1960]
    assert result["model_context"]["ship_axes"] is None
    assert result["model_context"]["coverage"]["structure"] == manifest["model_context"]["coverage"]["structure"]
    drawing = result["model_context"]["additional_drawings"][0]
    assert drawing["source_sha256"] == digest
    assert drawing["obstacles"] == []
    assert drawing["coverage"]["explicit_curve_objects_unchecked"] == 1
    assert not result["build_info"]["coverage_approved"]
    assert "drawing_files" not in result["model_context"]


def test_compact_import_output_uses_declared_per_drawing_units(tmp_path, manifest, monkeypatch):
    source_dxf(tmp_path)
    manifest["model_context"]["drawing_files"][0]["source_units"] = "in"
    seen = []
    def fake_import(path, source_units):
        seen.append((path, source_units))
        return {"provenance": "cad", "units": "mm", "source": str(path), "source_sha256": "test-sha",
                "counts": {"solid_instances": 1}, "objects": [{"type": "3DSOLID", "source_handle": "1A", "instance_path": [],
                "geometry": {"kind": "mesh", "vertices": [[0, 0, 0], [25.4, 25.4, 25.4]], "faces": []}}]}
    monkeypatch.setattr(builder, "inspect_cad", fake_import)
    result = builder.build_context(write_spec(tmp_path, manifest), tmp_path / "out.json")
    assert seen[0][1] == "in"
    assert seen[0][0] == (tmp_path / "source.dxf").resolve()
    assert result["model_context"]["additional_drawings"][0]["obstacles"][0]["max"] == [25.4] * 3


def test_real_solid_dxf_builds_traceable_capsule_envelope(tmp_path, manifest):
    from test_cad_import import make_cylinder_dxf
    source = make_cylinder_dxf(tmp_path / "source.dxf")
    original = source.read_bytes()
    result = builder.build_context(write_spec(tmp_path, manifest), tmp_path / "out.json")
    drawing = result["model_context"]["additional_drawings"][0]
    assert drawing["coverage"]["extracted_solids"] == 1
    assert drawing["coverage"]["unread_solids"] == 0
    assert drawing["obstacles"][0]["capsules"][0]["radius"] == pytest.approx(2)
    assert drawing["obstacles"][0]["source_identity"]["drawing_sha256"] == hashlib.sha256(original).hexdigest()
    assert source.read_bytes() == original


@pytest.mark.parametrize("units", [None, "feet", "MM"])
def test_source_units_are_never_guessed(tmp_path, manifest, units):
    source_dxf(tmp_path)
    manifest["model_context"]["drawing_files"][0]["source_units"] = units
    with pytest.raises(ValueError, match="source_units"):
        builder.build_context(write_spec(tmp_path, manifest), tmp_path / "out.json")
    assert not (tmp_path / "out.json").exists()


def test_unconfirmed_frame_template_cannot_accidentally_be_used(tmp_path):
    spec = ROOT / "examples/model-context-template.json"
    with pytest.raises(ValueError, match="common coordinate frame"):
        builder.build_context(spec, tmp_path / "out.json")


def test_each_drawing_needs_alignment_confirmation(tmp_path, manifest):
    source_dxf(tmp_path)
    manifest["model_context"]["drawing_files"][0]["shared_coordinates_confirmed"] = False
    with pytest.raises(ValueError, match="Every drawing file"):
        builder.build_context(write_spec(tmp_path, manifest), tmp_path / "out.json")


def test_build_never_overwrites_spec_drawing_or_existing_output(tmp_path, manifest):
    source = source_dxf(tmp_path)
    spec = write_spec(tmp_path, manifest)
    for destination in (source, spec):
        before = destination.read_bytes()
        with pytest.raises(ValueError, match="new JSON|already exists"):
            builder.build_context(spec, destination)
        assert destination.read_bytes() == before
    output = tmp_path / "existing.json"
    output.write_text("prior result")
    with pytest.raises(ValueError, match="already exists"):
        builder.build_context(spec, output)
    assert output.read_text() == "prior result"


def test_engineering_evidence_preserved_without_inference(tmp_path, manifest):
    source_dxf(tmp_path)
    evidence = {"units": "mm", "flow_direction": "start_to_end", "nominal_size_in": 4}
    manifest["engineering"] = deepcopy(evidence)
    result = builder.build_context(write_spec(tmp_path, manifest), tmp_path / "out.json")
    assert result["engineering"] == evidence
    evidence["units"] = "m"
    manifest["engineering"] = evidence
    with pytest.raises(ValueError, match="Engineering evidence"):
        builder.build_context(write_spec(tmp_path, manifest), tmp_path / "out-2.json")


def test_same_drawing_id_cannot_overwrite_another_source(tmp_path, manifest):
    source_dxf(tmp_path)
    manifest["model_context"]["drawing_files"] *= 2
    with pytest.raises(ValueError, match="Drawing IDs"):
        builder.build_context(write_spec(tmp_path, manifest), tmp_path / "out.json")


def test_valid_axes_remain_explicit(tmp_path, manifest):
    source_dxf(tmp_path)
    axes = {"longitudinal": "+Y", "transverse": "-X", "up": "+Z", "confirmed": True}
    manifest["model_context"]["ship_axes"] = axes
    result = builder.build_context(write_spec(tmp_path, manifest), tmp_path / "out.json")
    assert result["model_context"]["ship_axes"] == axes


def test_invalid_coverage_boolean_is_rejected(tmp_path, manifest):
    manifest["model_context"]["coverage"]["structure"]["reviewed"] = "yes"
    with pytest.raises(ValueError, match="reviewed"):
        builder.build_context(write_spec(tmp_path, manifest), tmp_path / "out.json")
