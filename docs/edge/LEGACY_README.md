# PIPEFORGE EDGE

Naval piping engineering copilot for the Maritime Dual-Use Hackathon. Edge adds revisioned CAD sessions, incremental change analysis, deterministic recommendations, reviewed routing proposals and optional CPU offloading to the existing PipeForge engine and Three.js viewer. **Designer approval does not establish construction approval.** No paid API, language model or second device is required.

The existing v2 engineering workbench, optimization, independent validation, hydraulics, eight scenarios and exports are preserved. See the [Edge demonstration](../../docs/edge/DEMO.md), [verified scope](../../docs/edge/STATUS.md), [AutoCAD connector](../../docs/edge/AUTOCAD.md) and [local/remote deployment](../../docs/edge/REMOTE.md). The original [v2 audit](../../docs/v2/AUDIT.md), [engineering assumptions](../../docs/ENGINEERING_ASSUMPTIONS.md) and [v2 implementation limits](../../docs/IMPLEMENTATION_STATUS.md) remain applicable.

**3.2 engineering hardening:** fitting occupancy, missing-data provenance, extraction coverage, bounded computation and restricted worker access are described in [V3_2.md](../../docs/edge/V3_2.md), including the preserved-session upgrade and native reconnection requirements.

## Install and run

Python 3.12+, Node 20+, and a normal CPU laptop are sufficient. In the cloud workspace:

```bash
cd /workspace/maritime-dual-use-hackathon-2026
./scripts/setup.sh
./scripts/start.sh
```

The single server serves the API and production React/Three.js workspace on port 8000. `PIPEFORGE_PORT` overrides it. Setup uses pinned Python requirements and `npm ci`, builds TypeScript/Vite and runs pytest. Dependencies live in `/workspace/pipeforge-venv`. Change this path in the short scripts for another local installation.

For a local Windows installation with Python 3.12 and Node.js 20 or 22, run these commands from the checkout root in PowerShell:

```powershell
.\scripts\setup-local.ps1
.\scripts\start-local.ps1
```

Open `http://127.0.0.1:8000` on the Windows laptop. These scripts are provided for Windows; execution on the actual laptop remains unverified. To run the optional Linux CPU worker separately:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-edge.txt
./scripts/start-edge.sh
```

The worker defaults to loopback on port 8010. Shared LAN use requires an explicit listening address and token or client allowlist; follow REMOTE.md before exposing it. Enter its configured URL in **CAD companion → Remote worker**, then select **Check worker**. The companion retains local processing and reports fallback when the remote request fails. See [REMOTE.md](../../docs/edge/REMOTE.md) for ARM64 wheel requirements, deployment, timing interpretation and measured comparisons.

Optional STEP uses a separate interpreter so CAD packages cannot disturb the main runtime:

```bash
./scripts/setup-cad.sh
/workspace/pipeforge-venv/bin/python -m pytest tests/test_step.py -q
```

`PIPEFORGE_CAD_PYTHON` may point to another interpreter with the pinned OpenCascade package. STEP has been installed and tested in this prepared instance. It is optional on fresh installations; without it the core application continues working and STEP tests are explicitly skipped. `GET /api/capabilities` reports availability. Native AutoCAD connector source is supplied separately; compilation and Autodesk execution remain unverified.

Frontend development: run `npm run dev` in `frontend/` while the backend serves port 8000. Rebuild via `npm run build` for the production server; restart the backend after the first build so static files register.

## CAD companion workflow

1. Select **CAD companion**. Load a fixture, or use **Import JSON / DXF**. DXF geometry requires explicit engineering JSON metadata for system, size, operating conditions and constraints. Select **Connect workspace drawing** to establish a revisioned source session.
2. To join a native connector session, enter its **Existing CAD session ID** and select **Attach CAD session**, or open `http://127.0.0.1:8000/?edge_session=SESSION_ID`. This loads the same authoritative session without creating another revision. A native session keeps source edits in AutoCAD; use **Create what-if session** for a separate workspace copy.
3. Select **Analyze revision**, then inspect a finding's measured condition, configured requirement, location and mapped CAD handles. Missing CAD identity or authoritative engineering metadata remains unverified.
4. Select **Propose route**. Preview the independently validated candidate, compare before/after metrics and review remaining `UNVERIFIED` checks. Previews do not modify the source model or drawing.
5. Enter the reviewing engineer and comment, then explicitly approve or reject. Current approved proposals enable JSON/DXF/CSV/report export with review evidence. Use these Edge export controls for the revisioned approval package; the preserved workbench exports retain their original unreviewed engineering-exchange behavior.
6. For workspace-owned sessions, use **Sync workspace edits** or optional **Live sync · 400 ms**, with **Live analyze after sync** when wanted. For a native session, edit in CAD; a lightweight status check observes revisions and new review events, with **Refresh CAD session** available manually. After a new revision arrives, **Load latest source view** explicitly updates the viewport. Older results and proposals become stale.

Sessions, proposals and review events persist in the ignored `.pipeforge-state/edge.sqlite3` database; `PIPEFORGE_DATA_DIR` overrides its directory. The companion shows/downloads the latest 100 audit events and labels that limit; the audit API supports pagination. Jobs are runtime state and must be resubmitted after a restart. No connector operation overwrites an original CAD file. The optional application API affects only the PipeForge session and requires a current approval plus explicit confirmation.

The C# connector targets **regular Windows AutoCAD 2025/2026 with .NET 8**. The actual installed release is unknown and must be checked with `ABOUT`. Plant 3D semantics, older AutoCAD releases and arbitrary solid-to-pipe inference are not supported. Follow [AUTOCAD.md](../../docs/edge/AUTOCAD.md) to build against the installed Autodesk assemblies, load with `NETLOAD`, bind real handles and perform the Windows smoke test. This Linux environment has not compiled or run the plugin. Actual Qualcomm IQ-9075 execution and Windows-to-device networking remain unverified; GPU and NPU acceleration are not implemented.

After native `PFCONNECT`, `PFCOMPANION` opens the browser at that session's URL. Browser proposals and decisions can be retrieved in AutoCAD with `PFREFRESH`, then selected using `PFPREVIEW` and exported with `PFEXPORT`. Refresh rejects pending native edits and stale replies; it never adopts browser geometry as CAD source. This source-level bridge still requires the documented Windows host test.

## Preserved engineering workbench

1. Load one of eight deterministic fixtures, or import a complete typed engineering JSON model. Scenario units and illustrative assumptions are visible.
2. Analyze the current geometry at the selected clearance. Inspect issue markers, coordinates and actual surface-separation evidence.
3. Choose an objective. Optionally compare five modes, coarse/fine grid exploration, and forward/reverse multi-system routing orders. Each attempt computes against the same source baseline; results are deduplicated geometrically and independently validated.
4. Compare alternative cards and the **approximate nondominated set among explored candidates**. This is not a complete Pareto frontier or a global optimum guarantee. Negative tradeoffs remain visible.
5. Inspect the network table, change explanations, validation coverage and catalog metadata. A PASS means an evaluated model requirement; official standards remain UNVERIFIED.
6. Use system isolation, original overlay, orthographic presets and route-vertex measurements. Hidden systems remain physical constraints.
7. Explore minimum pitch, imported catalog restrictions, editable non-equipment obstacles or new restricted boxes. A constraint edit explicitly establishes a new baseline and invalidates old results. Equipment and diameters stay fixed.
8. Export precise engineering JSON, CSV coordinates, a text change report, DXF, or optional STEP. Review the proposal in CAD and obtain qualified engineering approval.

The congested fixture deliberately increases length to remove a clash. The sealed-bulkhead fixture reports `no_route`; search failure/timeout does not prove continuous-space infeasibility. The gravity-detour and vent-rise fixtures route around a tank while preserving endpoint elevations and every segment's configured signed pitch. Already efficient routes can be retained unchanged.

## Algorithms and limits

Pressure routing retains direction-aware A* and capped incoming straight-run state. Edges use conservative finite pipe/elbow envelopes, bounding-sphere cKDTree broad phase and exact segment/box or segment/segment narrow phase. Original-route distance costs are cached per node; stale queue entries are discarded. Each search is bounded by eight seconds and 60,000 expansions.

Gravity routing includes horizontal travel in its search state, enforces a maximum travel budget derived from available signed fall/minimum pitch, and lifts candidate detours with uniform fall/rise between the original endpoint elevations. Every resulting 3D segment, connection and fitting is independently checked. Occupancy is conservatively projected through the endpoint elevation slab; over/under detours, variable-pitch optimization, arbitrary gravity branches and exact continuous-space completeness are not implemented. Drainage must fall; vents support explicitly configured upward/downward pitch. Geometric pitch does not establish hydraulic capacity.

Coarse/fine exploration performs independent graph searches, followed by bounded, independently checked local vertex shortcuts; it is not an adaptive corridor solver. Forward/reverse sequential routing can improve explored network choices but cannot guarantee globally coordinated feasibility. The selected objective combines normalized length, bends, elevation, available pressure-loss estimates and approximate modification distance.

Fitting metadata includes angle/tolerance, centerline radius, applicable nominal sizes/specifications, connection length, minimum tangent, K and provenance. Illustrative tolerances are explicit input assumptions, not Fincantieri standards or supplier approval. Elbow envelopes are conservative spheres, not manufactured swept solids. No supports, nozzle orientation, junction graph, stress/thermal design or certification is implemented.

Hydraulics retains Darcy-Weisbach, laminar 64/Re and turbulent Swamee-Jain friction with the compatible catalog K. Unknown inputs/transitional flow/gravity capacity remain unverified. Polyline length approximates elbow arc length; static head and network balancing are excluded. A sum of known pipe losses is labeled partial and is not a network pressure solution.

## CAD exchange

- **JSON:** semantic source of truth, SI coordinates, stable identifiers, original/proposed routes, added/removed segment IDs, dimensions, connection coordinates, full catalog data, validation and remaining concerns.
- **DXF R2013:** meter 3D polylines on encoded `PIPE_<id>` layers with XDATA, changed original routes on `ORIGINAL_<id>`, and obstacle box faces on `ENV_<id>`. Import requires explicit semantic metadata; checks reject unit, identity, dimension and supplied environment-face contradictions. Legacy centerline-only input remains supported using the provided environment metadata. This is not manufactured solid geometry.
- **STEP AP214 (optional OpenCascade):** named pipe components and obstacle boxes, true external radii and known bores, source meter coordinates verified by kernel reimport and reference volume/bounds. Straight segment solids meet at polyline corners; manufactured elbow/tee solids, fabrication assemblies and parametric histories are not represented. Unknown bores are exterior solid proxies. Export JSON alongside STEP to retain complete semantics.
- **CSV/text:** precise original/proposed coordinates and designer modification instructions, including numerical checks.

Edge DXF exchange additionally converts declared meters, millimeters, centimeters, inches, international feet or kilometers into the canonical meter model and extracts actual file entity handles. Unitless drawings require an explicit unit selection; unknown units are rejected. The legacy meter-only DXF contract remains unchanged. Source-coordinate scale is retained with the session and reviewed JSON package; all engineering metadata dimensions remain meters. The native connector derives its scale from supported `INSUNITS` and scales transient previews back into source units.

Standalone native DWG parsing, NWD/NWC, STEP import, STL and Navisworks integration are unavailable. The native AutoCAD adapter source is uncompiled and untested in its host. No official vessel CAD, standard, approved catalog or real operating specification was supplied; bundled data is illustrative.

## Verification and demonstration

```bash
/workspace/pipeforge-venv/bin/python -m pytest -q
cd frontend && npm run build
```

Optional real-browser regressions, with the server running and Chromium installed:

```bash
/workspace/pipeforge-venv/bin/pip install -r requirements-browser.txt
/workspace/pipeforge-venv/bin/python tests/browser_workflow.py
/workspace/pipeforge-venv/bin/python tests/browser_v2_workflow.py
/workspace/pipeforge-venv/bin/python tests/browser_edge_workflow.py
```

The preserved smoke and v2 browser workflows both pass against Edge with no JavaScript exceptions. The v2 test exercises all eight fixtures, candidate selection, isolation, projection, measurement, what-if recomputation and STEP download/reimport. Artifacts are written under `/workspace/pipeforge-artifacts/v2`. It requires the optional CAD kernel.

The 3.1 baseline Python suite passed **152 tests**, including the 87-test preserved baseline, 32 session/revision/approval/cache tests, remote-worker cases and six additional CAD unit/identity exchange tests. The production build and all three Chromium workflows pass without JavaScript exceptions. The Edge workflow verifies reviewed export, stale-result protection, live deltas, remote/local fallback, millimeter DXF exchange and shared-session monitoring. Its AutoCAD-marked attachment fixture is created through the API; no native host execution is implied. See [Edge status](../../docs/edge/STATUS.md) for detailed evidence and remaining native/hardware boundaries.

Run `/workspace/pipeforge-venv/bin/python scripts/benchmark.py` for repeatable optimization, validation and Python traced-memory measurements. Actual baseline and v2 results are in [docs/v2](../../docs/v2/benchmark.json). Edge transport measurements and reproduction commands are in [REMOTE.md](../../docs/edge/REMOTE.md); same-host offloading was slower for the measured cases and does not establish Qualcomm performance. See the [Edge 90-second demo](../../docs/edge/DEMO.md), [original workbench demo](../../docs/DEMO.md), [architecture](../../docs/ARCHITECTURE.md), [assumptions](../../docs/ENGINEERING_ASSUMPTIONS.md) and [v2 implementation status](../../docs/IMPLEMENTATION_STATUS.md).
