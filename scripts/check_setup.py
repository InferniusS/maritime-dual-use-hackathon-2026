#!/usr/bin/env python3
"""Check pinned packages and a small synthetic 3D DXF round trip.

This validates the development environment, not maritime design compliance.
No input models are read or modified; temporary files are automatically removed.
"""
from __future__ import annotations

import importlib
from importlib import metadata
import json
import math
from pathlib import Path
import platform
import sys
import sysconfig
import tempfile


def main() -> int:
    if platform.python_implementation() != "CPython" or sys.version_info[:2] != (3, 14) or sysconfig.get_config_var("Py_GIL_DISABLED"):
        raise RuntimeError("Use standard CPython 3.14.x and this project's .venv. Other Python versions and free-threaded builds have not been validated by these setup scripts.")

    print(f"Python {platform.python_version()} | {platform.system()} {platform.machine()}")
    for name in ("numpy", "scipy", "networkx", "ezdxf", "plotly", "pytest"):
        try:
            importlib.import_module(name)
        except ImportError as exc:
            raise RuntimeError(f"Could not import {name}. Run the appropriate setup script. Original error: {exc}") from exc
        print(f"  {name} {metadata.version(name)}")

    from packaging.requirements import Requirement

    lock_path = Path(__file__).resolve().parent.parent / "requirements-lock.txt"
    for line in lock_path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        requirement = Requirement(line)
        if requirement.marker and not requirement.marker.evaluate():
            continue
        installed = metadata.version(requirement.name)
        if installed not in requirement.specifier:
            raise RuntimeError(f"Version mismatch: {requirement.name} {installed}; expected {requirement.specifier}. Rerun setup.")

    import ezdxf
    import networkx as nx
    import numpy as np
    import plotly.graph_objects as go
    from scipy.spatial import cKDTree

    points = np.array([(0.0, 0.0, 0.0), (1000.0, 0.0, 0.0), (1000.0, 1000.0, 250.0)])
    expected_lengths = np.linalg.norm(np.diff(points, axis=0), axis=1)
    with tempfile.TemporaryDirectory(prefix="piping-setup-") as temp_dir:
        dxf_path = Path(temp_dir) / "synthetic-pipe.dxf"
        drawing = ezdxf.new("R2010")
        drawing.header["$INSUNITS"] = 4  # Millimeters.
        drawing.layers.new("PIPES")
        for start, end in zip(points[:-1], points[1:]):
            drawing.modelspace().add_line(tuple(start), tuple(end), dxfattribs={"layer": "PIPES"})
        drawing.saveas(dxf_path)

        restored = ezdxf.readfile(dxf_path)
        lines = list(restored.modelspace().query('LINE[layer=="PIPES"]'))
        if restored.header["$INSUNITS"] != 4 or len(lines) != 2:
            raise RuntimeError("DXF round trip changed units, layer, or entity count.")
        for index, line in enumerate(lines):
            if not np.allclose(line.dxf.start, points[index]) or not np.allclose(line.dxf.end, points[index + 1]):
                raise RuntimeError("DXF round trip changed 3D endpoints.")
        if restored.audit().has_errors:
            raise RuntimeError("The saved synthetic DXF contains structural errors.")

    graph = nx.Graph()
    for index, length in enumerate(expected_lengths):
        graph.add_edge(index, index + 1, weight=float(length))
    route = nx.shortest_path(graph, 0, 2, weight="weight")
    length = nx.shortest_path_length(graph, 0, 2, weight="weight")
    if route != [0, 1, 2] or not math.isclose(length, float(expected_lengths.sum())):
        raise RuntimeError("NetworkX geometry path check failed.")
    distance, index = cKDTree(points).query(points[2])
    if index != 2 or not math.isclose(float(distance), 0.0):
        raise RuntimeError("SciPy spatial index check failed.")

    figure = go.Figure(go.Scatter3d(x=points[:, 0].tolist(), y=points[:, 1].tolist(), z=points[:, 2].tolist(), mode="lines+markers"))
    if json.loads(figure.to_json())["data"][0]["type"] != "scatter3d":
        raise RuntimeError("Plotly 3D figure serialization failed.")

    print(f"PASS: pinned imports; 3D DXF units/layer/endpoints; graph length {length:.3f} mm; spatial index; 3D chart serialization.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"SETUP CHECK FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
