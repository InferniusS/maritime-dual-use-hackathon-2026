#!/usr/bin/env python3
"""Extract a local DXF into a read-only geometry preview JSON."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipe_router.cad_import import inspect_cad


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--source-units", choices=("mm", "in", "m"), default="mm")
    parser.add_argument("--output", "-o", type=Path, required=True)
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        parser.error("Output must be different from the source file")
    try:
        result = inspect_cad(args.input, args.source_units)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    except Exception as error:
        parser.exit(1, f"Could not import CAD: {error}\n")
    print(f"Saved {len(result['objects'])} preview objects to {args.output}")
    print(json.dumps(result["counts"]))


if __name__ == "__main__":
    main()
