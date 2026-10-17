"""Run known synthetic routing cases and create an offline comparison viewer."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import html
import json
import math
from pathlib import Path
import platform
import re
import time

from run_demo import ROOT, make_figure, write_dxf, write_html
from pipe_router.core import solve_layout


def check_expectations(result, expected):
    candidate = result["candidate"]
    actual = {
        "status": result["status"],
        "original_valid": result["original"]["valid"],
        "candidate_valid": None if candidate is None else candidate["valid"],
        "length_mm": None if candidate is None else candidate["length"],
        "bends": None if candidate is None else candidate["bends"],
        "vertical_detour": candidate is not None and any(
            point[2] != candidate["points"][0][2] for point in candidate["points"]
        ),
    }
    checks = []
    for field, name in (
        ("status", "Expected outcome"),
        ("original_valid", "Original route checks"),
        ("candidate_valid", "Suggested route checks"),
        ("length_mm", "Expected route length"),
        ("bends", "Expected turn count"),
        ("vertical_detour", "Expected height change"),
    ):
        wanted, found = expected[field], actual[field]
        if field == "length_mm" and wanted is not None and found is not None:
            passed = math.isclose(wanted, found, rel_tol=1e-9, abs_tol=1e-6)
        else:
            passed = wanted == found
        checks.append({"name": name, "passed": passed, "expected": wanted, "actual": found})
    return checks


def outcome_label(result):
    if result["status"] == "no_route":
        return "No route found"
    if result["status"] == "unchanged":
        return "Original kept"
    if not result["original"]["valid"]:
        return "Collision repaired"
    return "Route improved"


def route_label(route):
    if route is None:
        return "No route found"
    label = f"{route['length'] / 1000:g} m / {route['bends']} turns"
    if not route["valid"]:
        label += " / fails checks"
    return label


def write_gallery(records, summary, destination):
    escape = html.escape
    passed = sum(record["expected_passed"] for record in summary["scenarios"])
    options = "".join(
        f'<option value="{index}">{index + 1}. {escape(record["scenario"]["title"])}</option>'
        for index, record in enumerate(records)
    )
    payload = []
    for record, saved in zip(records, summary["scenarios"]):
        scenario, layout, result = record["scenario"], record["layout"], record["result"]
        figure = make_figure(layout, result)
        figure.update_layout(height=500)
        payload.append({
            "title": scenario["title"], "description": scenario["description"],
            "explanation": scenario["explanation"], "figure": json.loads(figure.to_json()),
            "original": route_label(result["original"]), "suggested": route_label(result["candidate"]),
            "outcome": outcome_label(result),
            "test": "Matches expected result" if saved["expected_passed"] else "Unexpected result. Review needed",
            "diameter": f"{layout['pipe']['diameter']:g} mm outer diameter",
            "clearance": f"{layout['rules']['clearance']:g} mm sample clearance",
            "href": f"{scenario['id']}/index.html",
        })
    first = payload[0]
    plot = make_figure(records[0]["layout"], records[0]["result"])
    plot.update_layout(height=500)
    plot_html = plot.to_html(
        full_html=False, include_plotlyjs=True, div_id="scenario-plot",
        config={"responsive": True, "displaylogo": False, "displayModeBar": False, "scrollZoom": False,
                "modeBarButtonsToRemove": ["sendChartToCloud"]},
    )
    rows = "".join(
        f'<tr><td><a href="{record["id"]}/index.html">{escape(record["title"])}</a></td>'
        f'<td>{escape(route_label(record["original"]))}</td>'
        f'<td>{escape(route_label(record["candidate"]))}</td>'
        f'<td>{"As expected" if record["expected_passed"] else "Review needed"}</td></tr>'
        for record in summary["scenarios"]
    )
    serialized = json.dumps(payload, allow_nan=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pipe routing scenarios</title>
<style>
* {{ box-sizing: border-box }}
body {{ margin: 0; background: #091421; color: #e6eef7; font: 15px/1.5 system-ui, sans-serif }}
main {{ max-width: 1180px; margin: auto; padding: 26px 22px 60px }}
h1 {{ margin: 10px 0; font-size: clamp(28px, 4vw, 38px); line-height: 1.2 }}
h2 {{ font-size: 22px; margin: 0 0 8px }}
p {{ margin: 8px 0 14px; max-width: 920px }}
.tag {{ color: #ffd493; font-size: 12px; font-weight: 700; letter-spacing: .08em }}
.muted {{ color: #afbed0 }}
.status {{ display: inline-block; color: #78e2c8; background: #14332f; padding: 6px 12px; border-radius: 7px }}
.panel {{ background: #102035; border: 1px solid #263b53; border-radius: 12px; margin-top: 20px; padding: 20px }}
select {{ width: 100%; padding: 12px; color: #e6eef7; background: #1c3049; border: 1px solid #536a84; border-radius: 8px; font: inherit; margin: 6px 0 18px }}
label {{ font-weight: 650 }}
.stats {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin: 16px 0 12px }}
.stat {{ background: #182b42; padding: 12px 16px; border-radius: 8px }}
.stat span {{ color: #afbed0; font-size: 12px; display: block; margin-bottom: 4px }}
.stat strong {{ font-size: 17px }}
.plot {{ overflow: hidden; margin: 0 -12px }}
a {{ color: #70e5d1 }}
table {{ width: 100%; border-collapse: collapse; text-align: left }}
td,th {{ padding: 13px 9px; border-bottom: 1px solid #2b415a }}
th {{ color: #afbed0 }}
.table-scroll {{ overflow-x: auto }}
@media(max-width: 650px) {{ main {{ padding: 20px 12px }} .panel {{ padding: 14px }} .stats {{ grid-template-columns: 1fr }} }}
</style></head><body><main>
<div class="tag">TRACK 2 / SYNTHETIC TESTS</div>
<h1>Try different pipe layouts</h1>
<p>These saved results were calculated on this computer. Each case tests one pressurized pipe using invented geometry and sample rules.</p>
<div class="status">{passed} of {len(records)} scenarios behaved as expected</div>
<section class="panel">
<label for="scenario-select">Choose a scenario</label>
<select id="scenario-select">{options}</select>
<h2 id="case-title">{escape(first['title'])}</h2>
<p id="case-description">{escape(first['description'])}</p>
<p class="muted"><span id="diameter">{first['diameter']}</span> · <span id="clearance">{first['clearance']}</span></p>
<div class="stats">
<div class="stat"><span>Original route</span><strong id="original">{escape(first['original'])}</strong></div>
<div class="stat"><span>Suggested route</span><strong id="suggested">{escape(first['suggested'])}</strong></div>
<div class="stat"><span>Outcome</span><strong id="outcome">{escape(first['outcome'])}</strong></div>
</div>
<div class="plot">{plot_html}</div>
<p class="muted">Orange is the original route. Green is the suggested route. Drag to rotate. Click a legend label to hide or show it. Lines show pipe centers. Checks include pipe thickness and sample clearance.</p>
<p id="explanation">{escape(first['explanation'])}</p>
<p><strong id="test-status">{escape(first['test'])}</strong> · <a id="details" href="{first['href']}">Full report and CAD export</a></p>
</section>
<section class="panel"><h2>All scenario results</h2>
<p class="muted">A blocked route is an expected outcome in the two blocked cases. The tool should report the problem instead of drawing a pipe through it.</p>
<div class="table-scroll"><table><thead><tr><th>Scenario</th><th>Original</th><th>Suggested</th><th>Test result</th></tr></thead><tbody>{rows}</tbody></table></div></section>
<section class="panel"><h2>What this proves so far</h2>
<p>The checks handle these seven known cases. This is not verification of the supplied ship drawings or official design standards. Each case contains one pipe. Drain slopes and standard fitting dimensions are still unimplemented.</p>
<p>Selecting a case displays a saved result. To test edited layouts we run the scenario script again. The same files can be used to compare results on the event laptop and on IQ9 after its software is set up.</p>
<p><a href="summary.json">Download the full test summary</a></p></section>
</main><script>
const cases = {serialized}
const selector = document.getElementById('scenario-select')
selector.addEventListener('change', () => {{
  const item = cases[Number(selector.value)]
  const textFields = {{'case-title': 'title', 'case-description': 'description', 'original': 'original', 'suggested': 'suggested', 'outcome': 'outcome', 'explanation': 'explanation', 'test-status': 'test', 'diameter': 'diameter', 'clearance': 'clearance'}}
  for (const [id, field] of Object.entries(textFields)) {{
    document.getElementById(id).textContent = item[field]
  }}
  document.getElementById('details').href = item.href
  Plotly.react('scenario-plot', item.figure.data, item.figure.layout, {{responsive: true, displaylogo: false, displayModeBar: false, scrollZoom: false, modeBarButtonsToRemove: ['sendChartToCloud']}})
}})
</script></body></html>"""
    destination.write_text(page, encoding="utf-8")


def run(manifest_path, output):
    manifest_path = manifest_path.resolve()
    output = output.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    scenarios = manifest["scenarios"]
    if not isinstance(scenarios, list) or not 1 <= len(scenarios) <= 32:
        raise ValueError("Provide between 1 and 32 scenarios")
    identifiers = [scenario["id"] for scenario in scenarios]
    if len(set(identifiers)) != len(identifiers) or not all(
        isinstance(identifier, str) and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", identifier)
        for identifier in identifiers
    ):
        raise ValueError("Scenario ids must be unique lowercase words separated by hyphens")
    sources = [(manifest_path.parent / scenario["layout"]).resolve() for scenario in scenarios]
    targets = [output / "index.html", output / "summary.json"]
    targets.extend(output / identifier / name for identifier in identifiers for name in (
        "index.html", "result.json", "route-centerlines.dxf"
    ))
    if {path.resolve() for path in targets} & {manifest_path, *sources}:
        raise ValueError("Choose an output folder that will not overwrite any input files")

    records, summaries = [], []
    runtime = {"os": platform.system(), "architecture": platform.machine(), "python": platform.python_version()}
    for scenario, source in zip(scenarios, sources):
        raw = source.read_bytes()
        layout = json.loads(raw)
        start = time.perf_counter()
        result = solve_layout(layout)
        elapsed = time.perf_counter() - start
        checks = check_expectations(result, scenario["expected"])
        folder = output / scenario["id"]
        folder.mkdir(parents=True, exist_ok=True)
        report = {"generated_at": datetime.now(timezone.utc).isoformat(),
                  "input_sha256": hashlib.sha256(raw).hexdigest(), "layout": layout,
                  "result": result, "runtime_seconds": elapsed, "runtime": runtime,
                  "scenario": scenario, "expectation_checks": checks}
        (folder / "result.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        write_dxf(layout, result, folder / "route-centerlines.dxf")
        write_html(layout, result, elapsed, folder / "index.html")
        records.append({"scenario": scenario, "layout": layout, "result": result})
        summaries.append({
            "id": scenario["id"], "title": scenario["title"], "description": scenario["description"],
            "status": result["status"], "expected_passed": all(check["passed"] for check in checks),
            "checks": checks, "runtime_seconds": elapsed,
            **{key: None if result[key] is None else {metric: result[key][metric] for metric in ("length", "bends", "valid")}
               for key in ("original", "candidate")},
        })
    summary = {"generated_at": datetime.now(timezone.utc).isoformat(), "runtime": runtime,
               "scenarios": summaries, "all_expectations_passed": all(record["expected_passed"] for record in summaries)}
    (output / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    write_gallery(records, summary, output / "index.html")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / "examples" / "scenarios.json")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "generated" / "scenario-suite")
    args = parser.parse_args()
    try:
        summary = run(args.manifest, args.output)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        parser.exit(2, f"Cannot generate scenarios: {exc}\n")
    for record in summary["scenarios"]:
        outcome = "PASS" if record["expected_passed"] else "REVIEW"
        print(f"{outcome}: {record['title']} / {record['status']}")
    print(f"Viewer: {args.output.resolve() / 'index.html'}")
    print("Synthetic cases only. Official rule compliance and real CAD import remain unverified.")
    return 0 if summary["all_expectations_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
