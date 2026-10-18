# PipeForge Edge 5.1: calculated industrial demonstration

Use the invented industrial examples. The main demonstration is a 20-run **connected** cooling assembly plus a crossing firemain, finite valves/tees/reducers/flanges/hangers and surrounding structure/access volumes. Source geometry is synthetic; no real drawing, external AI service, hardware board or confidential asset is required.

## Install and prepare on Windows

From PowerShell:

```powershell
git clone --branch pipeforge-edge-v4 --single-branch https://github.com/InferniusS/maritime-dual-use-hackathon-2026.git pipeforge-edge-v4
cd pipeforge-edge-v4
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe scripts\benchmark_industrial.py --scenario e-congested-assembly --repeats 1 --max-evaluations 32 --max-seconds 60 --no-memory --output local-output\industrial-demo-check.json
.\.venv\Scripts\python.exe scripts\run_web.py
```

Keep the terminal running and open `http://127.0.0.1:8767/connected` in your own browser. On macOS/Linux create the environment with `python3 -m venv .venv` and substitute `.venv/bin/python` for the Windows executable. The optional React workspace has its separate commands in the [README](../../README.md).

Before the pitch, compute E once on the actual laptop and inspect the returned validation, actual elapsed time, changed spans, complete-network hard failures and remaining UNVERIFIED findings. Keep the result bound to its current source/environment revision. A prior run, export or screenshot is not the result of a later edited scene. Rehearse the full workflow at the presentation screen size.

## Ninety-second primary demonstration

| Time | Action | Evidence |
| --- | --- | --- |
| 0–15 s | Load **E · Congested connected machinery assembly**. Select the cooling network or declared machinery compartment. | The cooling graph has 20 real connected runs, four branched ties, fixed ports, valves, reducers, flanges and hangers. A second system and surrounding finite obstacles remain context. |
| 15–28 s | Inspect original violations and select a branch/system. | Branch feeds cross the retained firemain. Show actual component IDs, source handles, diameter variants and explicit reducer permissions; missing commercial approval remains visible. |
| 28–48 s | Run Balanced optimization with the rehearsed candidate/time budget. | Actual phases, neighborhood/evaluation counts and active IDs are measured. Local groups preserve fixed boundaries; the complete network, including the locked firemain, gates acceptance. |
| 48–65 s | Compare original/proposed geometry and select changed feeds. | Multiple connected branches change together. Show actual calculated length/bends, fixed component coordinates, unchanged other-system geometry and final hard-failure count. Do not substitute remembered numbers. |
| 65–78 s | Inspect topology, component bodies and engineering evidence. | Connections, DN/OD and physical dimensions are retained. Provisional fitting/catalog, mating-flange, support-anchorage and installation evidence stay UNVERIFIED. |
| 78–90 s | Explicitly approve the current reviewed export and download the eight-file bundle. | Exact instructions, parts/fittings, global coordinates, updated topology, validation, dimensioned comparison and DXF retain CAD identity. These are engineering review aids, not a fabrication release. |

If a calculation has not finished, show its real progress; do not describe a pending result as accepted. A timeout or exhausted budget is distinct from a completed bounded unsuccessful search. If final hard checks fail, preserve the original and show the actual reason rather than selecting a simpler scene without explanation.

The final measured reference run for E repaired four pipe crossings and changed `branch-01-feed` through `branch-04-feed`, retaining all 21 connections and the locked firemain. Polyline centerline length was **55,800 → 55,000 mm**, bends **16 → 8**, and hard failures **2 → 0**, with 48 checks still UNVERIFIED. Supplied-radius developed centerline length was **54,426.548 → 54,313.274 mm**; its saving differs from the polyline saving. Show the values calculated on the current laptop and preserve their length basis. The final host measured roughly 3.40 seconds cold and 3.14 seconds warm; rehearse rather than promising this latency on other hardware.

## Additional proof beyond the primary pitch

1. Load **D · 40 connected industrial runs** and select all reviewed spans. Run optimization and inspect the 40-span selection, bounded neighborhood coverage and actual changed IDs. The final reference run considered all 40 spans, changed nine feeds and calculated 89,800 → 79,000 mm polyline length with zero hard failures. Considered spans can include justified unchanged-straight reuse; this is not forty independent A* searches or a claim that all forty must change.
2. Load **F · Connected assembly requiring a coordinated corridor exchange**. First select only `main-01` and calculate with the crossing firemain retained. Then select both systems. Compare the independent failure/no-improvement with the coordinated proposal and its complete-network validation. The final reference run changed both interacting runs and calculated 50,000 → 45,000 mm polyline length. The 600 mm/200 mm supplied bend radii are preserved.
3. Load **G · Connected assembly behind a sealed bulkhead**. Show the blocking upstream connection, retained failed original, no invented replacement and explicit bounded/timeout termination. Search failure is not a general mathematical infeasibility proof.
4. In a local review scene, add a hypothetical blocking wall, inspect affected finite-envelope runs and reoptimize. Move/remove only explicitly editable designer-review obstacles and observe fresh analysis and invalidated approval. Existing ship structure, CAD-source obstacles and protected equipment remain fixed. Source drawings are unchanged.
5. Use the original 5.0 drainage and already-good cases to demonstrate preserved local flow-direction checks and no fabricated benefit. See [DEMO_5.md](DEMO_5.md).

## Optional native AutoCAD demonstration

Follow [AUTOCAD_5_1.md](AUTOCAD_5_1.md) for exact .NET 10/AutoCAD 2027 build and `NETLOAD` instructions, then complete the synthetic Windows acceptance checklist. The plugin is additive and uses the same Python service. `PIPEFORGENETWORK` inspects supported sources and context; connections and engineering metadata require review. `PIPEFORGEOPTIMIZE` supports Multiple, Systems and All reviewed spans, actual industrial progress, cancellation, preview and explicit approval. Optional Keep creates separate undoable review centerlines and preserves source solids.

Native AutoCAD execution was not available on Linux. A build, console helper test, translated synthetic model or HTTP job is separate evidence. No actual CAD model was attached to the latest request, so real-model interpretation and acceptance remain pending authorized input. Keep original industrial models and generated real-CAD outputs outside Git.
