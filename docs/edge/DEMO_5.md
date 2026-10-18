# PipeForge Edge 5.0: reproducible 90-second demonstration

Use only the supplied invented examples. Scenario 09 computes a genuine two-system coordinated proposal; scenario 10 is an invented representative compartment. No confidential drawing or external AI service is needed. The displayed geometry and metrics come from the router and independent validator on each run.

## Install and prepare

From PowerShell on Windows:

```powershell
git clone --branch pipeforge-edge-v4 --single-branch https://github.com/InferniusS/maritime-dual-use-hackathon-2026.git pipeforge-edge-v4
cd pipeforge-edge-v4
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe scripts\benchmark_v5.py --scenario 09-coordinated-improvement --repeats 1 --output local-output\demo-check.json
.\.venv\Scripts\python.exe scripts\run_web.py
```

Keep the terminal running and open `http://127.0.0.1:8767/connected` in the browser on your own computer. On macOS/Linux use `python3 -m venv .venv` and `.venv/bin/python` instead of the Windows environment path. Do not copy a virtual environment between operating systems.

Before the pitch, load scenario 09 and calculate it once. Inspect actual timing, source revision, changed spans, whole-network hard failures and remaining UNVERIFIED findings. If the local calculation does not establish a valid improvement, present the actual failure rather than a stored result. Rehearse at the laptop's normal screen resolution with its local CPU. Browser testing is not native AutoCAD execution.

## Timed browser demonstration

| Time | Action | Calculated evidence |
| --- | --- | --- |
| 0–12 s | Load **09 · Coordinated corridor exchange**. Inspect the two system colors, fixed ports and declared fitting dimensions. | Both original systems are valid. The cooling line has a 600 mm bend radius; the firemain has 200 mm. Source total length is computed from its global coordinates. |
| 12–25 s | Select span scope and select only cooling `supply`. Run **Optimize connected network**. | **NO IMPROVEMENT FOUND** for this selected search. The unselected firemain remains active and unchanged; an invalid short cooling elbow is not accepted. |
| 25–42 s | Choose all reviewed spans, Balanced and run optimization. | Both `supply` and `firemain` change together. The measured reference run calculates 16,000 → 11,000 mm and four → two direction changes; use this run's values. Show actual elapsed time and independent hard-failure count. |
| 42–60 s | Switch original/proposed overlay to side-by-side. Isolate each system, use a camera preset and inspect a span. | Cooling now uses the direct corridor, while firemain moves to a valid underpass. Fixed ports, diameters and fitting sizes are preserved. Coordinate inspection and the measurement tool show real WCS millimeters. |
| 60–76 s | Expand engineering evidence and candidate tradeoffs. | Accepted geometry has zero implemented hard failures, while provisional catalog, fabrication, support and installation evidence remain explicitly UNVERIFIED. Explain why temporary route release requires complete-network revalidation. |
| 76–90 s | Approve the current revision's instruction export and download the bundle. | Open the Excel coordinate/change schedules, exact JSON instructions and static before/after comparison. Export contains original handles, systems, dimensions, topology, validation and unresolved notes. |

The numerical example is a reproducible measured run, not an outcome stored in the example file. A hard constraint cannot be traded for length or score. Four geometric bends do not establish four approved commercial elbows. The export supports engineering implementation review, not fabrication release.

## Additional evidence when time permits

- **01 · Already good** preserves its source and reports no improvement.
- **04 · Three-pipe shared corridor** calculates changes to three finite-diameter runs around shared equipment; all are checked together. Total length increases because clearance repair takes priority.
- **06 · Connected drainage** preserves correct pitch on three connected spans, using rotated ship axes and actual flow direction. Reverse one span's flow only in a local scratch copy to inspect independent rejection; do not overwrite the demonstration source.
- **07 · Unavailable fitting** refuses to invent missing fitting dimensions. **08 · Sealed compartment** retains the original and reports blocking constraints/search limitations.
- **10 · Representative machinery compartment** shows cooling, retained firemain/drainage, equipment, duct, wireway, walkway and ladder access. It is invented geometry, not a model of an actual vessel. A measured run shortens two connected cooling spans by 3,000 mm while preserving the other systems.

## Optional Windows AutoCAD demonstration

Follow [AUTOCAD_5.md](AUTOCAD_5.md) to build the plugin against the installed AutoCAD 2027 assemblies, load it with `NETLOAD` and complete synthetic runtime acceptance first. The `.NET 10` source project compiles, but native AutoCAD execution was not available for verification on Linux.

Use `PIPEFORGENETWORK` to inspect synthetic supported solids/blocks and confirm their topology and engineering metadata. Run `PIPEFORGEOPTIMIZE`, choose the reviewed network and Multiple, Systems or All. Inspect progress/cancellation, whole-network evidence, exact coordinates and overlays; explicitly approve the reviewed export. Optional persistent review centerlines require a separate Keep decision and leave original solids unchanged. Deliberately edit a source object or obstacle and verify that an old handoff is rejected as stale. Do not imply automatic replacement solids, commercial-fitting insertion or native runtime success until those checks pass on the installed Windows host.
