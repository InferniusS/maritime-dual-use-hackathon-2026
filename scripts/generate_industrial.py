#!/usr/bin/env python3
"""Reproduce the source-only industrial A–G inputs; never store result geometry."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipe_router.industrial_examples import industrial_scenarios  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT/"examples"/"industrial")
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)
    for identifier, network in industrial_scenarios().items():
        target = args.output / f"{identifier}.json"
        target.write_text(json.dumps(network, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"{identifier}: {len(network['spans'])} source runs, {len(network['nodes'])} connected components")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
