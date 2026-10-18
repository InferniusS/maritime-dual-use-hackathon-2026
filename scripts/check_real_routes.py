"""Replay selected real CAD routes and save separate review reports.

Endpoint contact assumptions require an explicit command line flag. This is a
local regression tool. It does not edit the source drawing or verify fittings.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipe_router.cad_import import inspect_cad
from pipe_router.cad_layout import prepare_cad_layout
from pipe_router.benchmarks import make_detour_case
from pipe_router.workflow import run_workflow
from run_demo import write_dxf, write_html
from pipe_router.review_export import write_review_xlsx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("drawing", type=Path)
    parser.add_argument("--candidate", action="append", required=True, help="Candidate ID from the drawing inspection. Repeat for several routes")
    parser.add_argument("--source-units", choices=("mm", "m", "in"), default="mm")
    parser.add_argument("--clearance", type=float, default=50)
    parser.add_argument("--padding", type=float, default=500)
    parser.add_argument("--max-stub", type=float, default=250)
    parser.add_argument("--assume-end-contacts", action="store_true", help="Explicit test assumption that listed end contacts are connections. Review these in CAD")
    parser.add_argument("--detour-mm", type=float, help="Also try an explicitly artificial detour if the baseline is valid")
    parser.add_argument("--output", type=Path, default=ROOT / "data/generated/real-route-checks")
    parser.add_argument("--rule-profile", choices=("prototype-unverified", "fmg-v3"), default="prototype-unverified")
    parser.add_argument("--context", type=Path, help="JSON model context and optional engineering evidence")
    args = parser.parse_args()
    context_record = json.loads(args.context.read_text()) if args.context else {}
    extra = {"rule_profile": args.rule_profile}
    if context_record:
        extra["model_context"] = context_record.get("model_context", context_record)
        if "engineering" in context_record:
            extra["engineering"] = context_record["engineering"]

    if args.drawing.resolve() == (args.output / "summary.json").resolve() or args.drawing.resolve().is_relative_to(args.output.resolve()):
        parser.error("Keep the source drawing outside the output folder")
    started = time.perf_counter()
    cad = inspect_cad(args.drawing, source_units=args.source_units)
    import_seconds = time.perf_counter() - started
    args.output.mkdir(parents=True, exist_ok=True)
    created = datetime.now(timezone.utc)
    run_folder = args.output / (created.strftime("run-%Y%m%dT%H%M%S.%fZ-") + uuid.uuid4().hex[:10])
    run_folder.mkdir()
    summary = {"created_utc": created.isoformat().replace("+00:00", "Z"),
               "run_folder": str(run_folder.resolve()),
               "source_sha256": cad["source_sha256"], "source_units": args.source_units,
               "clearance_mm": args.clearance, "contact_assumptions": args.assume_end_contacts,
               "import_seconds": import_seconds, "cases": []}

    def check(case, label, folder):
        tick = time.perf_counter()
        result = run_workflow(case)
        result["runtime_seconds"] = time.perf_counter() - tick
        normalized = result["normalized_layout"]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "result.json").write_text(json.dumps({"input_layout": case, "layout": normalized, "result": result}, indent=2, allow_nan=False))
        write_dxf(normalized, result, folder / "route-centerlines.dxf")
        write_review_xlsx(normalized, result, folder / "route-review.xlsx")
        write_html(normalized, result, result["runtime_seconds"], folder / "report.html")
        entry = {"candidate": label, "case_folder": str(folder.relative_to(run_folder)), "status": result["status"], "original_length_mm": result["original"]["length"],
                 "original_valid": result["original"]["valid"], "candidate_length_mm": result["candidate"]["length"] if result["candidate"] else None,
                 "search": result["search"], "checks": result["checks"], "routing_seconds": result["runtime_seconds"]}
        summary["cases"].append(entry)
        print(json.dumps(entry), flush=True)
        return result

    for index, identifier in enumerate(args.candidate, 1):
        folder = run_folder / f"case-{index}"
        try:
            case = prepare_cad_layout(cad, identifier, clearance=args.clearance, padding=args.padding, **extra)
            if args.assume_end_contacts:
                contacts = case["cad_context"]["endpoint_contacts"]
                assumptions = {side: [c["obstacle_id"] for c in contacts if c["endpoint"] == side] for side in ("start", "end")}
                case = prepare_cad_layout(cad, identifier, clearance=args.clearance, padding=args.padding,
                                          endpoint_connections=assumptions, max_stub_length=args.max_stub, **extra)
            result = check(case, identifier, folder)
            if args.detour_mm is not None and result["original"]["valid"]:
                detour = make_detour_case(case, offset_mm=args.detour_mm)
                check(detour, identifier + " artificial detour", folder / "artificial-detour")
        except (ValueError, RuntimeError, KeyError, TypeError) as error:
            summary["cases"].append({"candidate": identifier, "case_folder": str(folder.relative_to(run_folder)), "error": str(error)})
            print(identifier + ": " + str(error), flush=True)
    (run_folder / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False))
    print(f"Reports saved in {run_folder.resolve()}")


if __name__ == "__main__":
    main()
