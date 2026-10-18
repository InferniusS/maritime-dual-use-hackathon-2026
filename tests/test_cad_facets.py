"""Invented DXF facets remain physical unknowns, never inferred pipe semantics."""
from copy import deepcopy
import hashlib

import ezdxf
import pytest

from pipe_router.cad_import import inspect_cad
from pipe_router.cad_layout import _solid_boxes, prepare_cad_layout
from pipe_router.network_model import reconstruct_network
from test_cad_import import cylinder_sat


VERTICES = [(0, 0, 0), (10, 0, 0), (10, 20, 30), (0, 20, 30)]


def add_facet(space, kind, vertices=VERTICES):
    if kind == "MESH":
        entity = space.add_mesh(dxfattribs={"layer": "REVIEW-EQUIPMENT"})
        with entity.edit_data() as data:
            data.vertices = vertices
            data.faces = [(0, 1, 2, 3)]
        return entity
    if kind == "3DFACE":
        return space.add_3dface(vertices)
    if kind == "SOLID":
        return space.add_solid(vertices)
    if kind == "TRACE":
        return space.add_trace(vertices)
    if kind == "POLYFACE":
        entity = space.add_polyface()
        entity.append_faces([vertices])
        return entity
    entity = space.add_polymesh((2, 2))
    for key, point in zip(((0, 0), (0, 1), (1, 1), (1, 0)), vertices):
        entity.set_mesh_vertex(key, point)
    return entity


@pytest.mark.parametrize("kind", ["MESH", "3DFACE", "SOLID", "TRACE", "POLYFACE", "POLYMESH"])
def test_faceted_sources_keep_true_identity_and_complete_obstacle_bounds(tmp_path, kind):
    document = ezdxf.new("R2010")
    entity = add_facet(document.modelspace(), kind)
    source = tmp_path / "invented-facets.dxf"
    document.saveas(source)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    cad = inspect_cad(source)
    assert cad["counts"]["retained_faceted_instances"] == 1
    assert cad["route_candidates"] == [], "An arbitrary faceted object is not a valve, reducer, or pipe"
    assert cad["axis_candidates"] == []
    assert {obj["source_handle"] for obj in cad["objects"]} == {entity.dxf.handle}
    assert cad["objects"][0]["instance_path"] == []
    assert not cad["objects"][0]["semantic_identity_verified"]
    boxes, counts = _solid_boxes(cad)
    assert len(boxes) == 1
    assert boxes[0]["source_handle"] == entity.dxf.handle
    assert boxes[0]["min"] == pytest.approx([0, 0, 0])
    assert boxes[0]["max"] == pytest.approx([10, 20, 30])
    assert boxes[0]["obstacle_method"] == "sample_box"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest


def test_nested_facets_keep_instance_nonuniform_transform_and_units(tmp_path):
    document = ezdxf.new("R2010")
    block = document.blocks.new("SYNTHETIC-EQUIPMENT")
    entity = add_facet(block, "MESH")
    placement = document.modelspace().add_blockref("SYNTHETIC-EQUIPMENT", (100, 200, 300),
        dxfattribs={"rotation": 90, "xscale": 2, "yscale": 3, "zscale": 4})
    source = tmp_path / "transformed-facets.dxf"
    document.saveas(source)
    cad = inspect_cad(source, "in")
    box = _solid_boxes(cad)[0][0]
    assert box["source_handle"] == entity.dxf.handle
    assert box["instance_path"] == [placement.dxf.handle + "[0]"]
    assert box["min"] == pytest.approx([40*25.4, 200*25.4, 300*25.4])
    assert box["max"] == pytest.approx([100*25.4, 220*25.4, 420*25.4])
    assert not cad["route_candidates"]


def test_faceted_display_limit_preserves_whole_vertex_bounds(tmp_path, monkeypatch):
    import pipe_router.cad_import as importer
    monkeypatch.setattr(importer, "MAX_FACETED_PREVIEW_VERTICES", 3)
    document = ezdxf.new("R2010")
    add_facet(document.modelspace(), "MESH")
    source = tmp_path / "large-display.dxf"
    document.saveas(source)
    cad = inspect_cad(source)
    assert cad["counts"]["faceted_bounds_only_instances"] == 1
    obj = cad["objects"][0]
    assert obj["source_vertex_count"] == 4
    assert len(obj["geometry"]["vertices"]) == 8
    assert obj["display_bounds_only"]
    box = _solid_boxes(cad)[0][0]
    assert box["min"] == [0, 0, 0]
    assert box["max"] == [10, 20, 30]


def test_closed_polygon_mesh_keeps_seams_and_original_vertex_count(tmp_path):
    document = ezdxf.new("R2010")
    entity = add_facet(document.modelspace(), "POLYMESH")
    entity.close(m_close=True, n_close=True)
    source = tmp_path / "closed-polygon-mesh.dxf"
    document.saveas(source)
    obj = inspect_cad(source)["objects"][0]
    assert obj["source_vertex_count"] == 4
    assert obj["source_face_count"] == 4
    assert len(obj["geometry"]["faces"]) == 8


def test_faceted_wall_survives_both_route_and_network_translation(tmp_path):
    document = ezdxf.new("R2010")
    pipe = document.modelspace().add_3dsolid(dxfattribs={"layer": "REVIEW-COOLING"})
    pipe.sat = cylinder_sat()
    wall = add_facet(document.modelspace(), "3DFACE", [(10.125,-50,0),(10.125,100,0),(10.125,100,100),(10.125,-50,100)])
    source = tmp_path / "pipe-and-wall.dxf"
    document.saveas(source)
    cad = inspect_cad(source)
    assert cad["route_candidates"][0]["source_layers"] == ["REVIEW-COOLING"]
    unchanged = deepcopy(cad)
    layout = prepare_cad_layout(cad, cad["route_candidates"][0]["id"], padding=200)
    assert any(item["source_handle"] == wall.dxf.handle for item in layout["cad_context"]["obstacle_sources"])
    declared = deepcopy(cad)
    declared["bounds"] = {"min": [-500,-500,-500], "max": [1000,1000,1000]}
    network = reconstruct_network(declared)
    assert any(item["source_handle"] == wall.dxf.handle for item in network["obstacles"])
    assert network["coverage"]["unknown_count"] > 0
    assert not network["coverage"]["complete"]
    incomplete = deepcopy(cad)
    incomplete["counts"]["solid_instances"] += 1
    preparation = prepare_cad_layout(incomplete, cad["route_candidates"][0]["id"], padding=200)
    counts = preparation["cad_context"]["counts"]
    assert counts["solid_instances_with_extracted_geometry"] == 1
    assert counts["physical_instances_with_extracted_geometry"] == 2
    assert counts["solid_instances_without_extracted_geometry"] == 1, "A retained facet cannot conceal an unread ACIS solid"
    assert cad == unchanged


@pytest.mark.parametrize("mode", ["missing", "recursive", "xref"])
def test_skipped_insert_geometry_is_explicitly_counted(tmp_path, mode):
    document = ezdxf.new("R2010")
    if mode != "missing":
        block = document.blocks.new("UNRESOLVED")
        if mode == "recursive":
            block.add_blockref("UNRESOLVED", (0,0,0))
        else:
            block.block.dxf.flags = 4
            block.block.dxf.xref_path = "synthetic-unavailable.dwg"
    document.modelspace().add_blockref("UNRESOLVED", (0,0,0))
    source = tmp_path / "unresolved-insert.dxf"
    document.saveas(source)
    cad = inspect_cad(source)
    assert sum(value for key, value in cad["unsupported_geometry"].items() if key.startswith("INSERT:")) == 1
    assert cad["route_candidates"] == []
    assert not cad["compliance_verified"]
