# PipeForge Edge 5.1

PipeForge Edge calculates coordinated route changes across connected industrial assemblies and interacting piping systems. Version 5.1 adds bounded neighborhood optimization for 40-run examples, finite component envelopes, review-obstacle editing and industrial benchmarks. Independent whole-network checks gate every accepted proposal; the workspace shows physical components, original/proposed geometry, measured constraints, CAD instructions, global coordinates and Excel schedules. The existing `pipe_router` engine, AutoCAD 2027 plugin and Edge FastAPI/React workspace are retained.

Version 5.1 starts at `d2c4bad7204a0cd2a347a6a241cb14e42cd06437` on the sanitized `pipeforge-edge-v4` branch. Its original source snapshot includes team code from `227a61351f9d5a37a00c15ce457329709a01def9`; newer `main` history is not merged into this branch. Only invented synthetic JSON demonstration geometry is supplied. Drawing-derived artifacts, screenshots, CAD files, prebuilt binaries and the original confidential-asset history remain excluded. See [sharing scope](docs/SHARING.md), [the original snapshot manifest](SHARING_MANIFEST.json) and [5.1 engineering evidence and limits](docs/edge/V5_1.md).

## Clone

Repository access is required. Authenticate with your normal GitHub tooling; do not put credentials in clone URLs.

```sh
git clone --branch pipeforge-edge-v4 --single-branch https://github.com/InferniusS/maritime-dual-use-hackathon-2026.git pipeforge-edge-v4
cd pipeforge-edge-v4
```

## Requirements

- Standard CPython 3.12 or 3.14 and pip. The integrated implementation was verified on Linux CPython 3.12.14; the team setup scripts target CPython 3.14.x. Use the direct commands below when using 3.12.
- Git. Node.js 20 or later is also used by the full test suite’s JavaScript checks; Node.js and npm are needed for the optional React workspace.
- AutoCAD 2027 for Windows and .NET 10 SDK are needed only to build and run the native 2027 plugin. The browser workflow requires neither.

`requirements.txt` pins the full Python application, routing, Excel export, and test dependencies. `requirements-lock.txt` pins the smaller team-service environment; install the full requirements below to run both services and the complete core suite. Create a virtual environment on each machine instead of copying one.

## Install and run on Windows

From PowerShell in the cloned directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe scripts\run_web.py
```

These commands invoke the environment directly and do not require PowerShell activation. Keep the terminal running. Open `http://127.0.0.1:8767/connected` in your own browser. The original team workspace is at `/` on the same service.

## Install and run on macOS or Linux

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
.venv/bin/python scripts/run_web.py
```

Keep the terminal running and open `http://127.0.0.1:8767/connected` in your own browser. The service binds to loopback by default. No drawing, external service, GPU, model download, or hardware board is required for the supplied demonstrations.

## Run the industrial demonstration

Follow [the calculated industrial demonstration](docs/edge/DEMO_5_1.md).

1. In the connected workspace, load **E · Congested connected machinery assembly**.
2. Inspect its 20 connected cooling runs, fixed equipment ports, reducing tees, valves, flanges, hangers, surrounding structure and crossing firemain. Select the reviewed network or its machinery compartment.
3. Run optimization and inspect actual neighborhood progress, coordinated branch changes, preserved connections and complete-network validation. Compare measured geometry and remaining UNVERIFIED evidence.
4. Explicitly approve the current instruction export and download the JSON/Markdown instructions, XLSX/CSV schedules, and generated DXF review overlay. Generated exports stay local; no CAD export is bundled in this snapshot.
5. Load **D · 40 connected industrial runs** to exercise larger selection, **F · Connected assembly requiring a coordinated corridor exchange** for a two-system dependency, or **G · Connected assembly behind a sealed bulkhead** for an unsuccessful bounded search. Timeouts and exhausted search budgets have distinct statuses.

Seven generated industrial scenes in `examples/industrial/` include exactly 5, 10, 20 and 40 connected runs; the congested/coordination cases each contain a 20-run connected main plus an interacting second system. Every counted run is an actual component connection. The original six `examples/connected/` and ten `examples/multi-system/` scenes remain available, including the [5.0 ninety-second demo](docs/edge/DEMO_5.md). All supplied geometry is invented. Exports require engineering review and are not fabrication approval.

## Test and reproduce measurements

Windows:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts\benchmark_connected.py --repeats 3 --max-evaluations 8 --output connected-benchmark.json
.\.venv\Scripts\python.exe scripts\benchmark_v5.py --repeats 3 --max-evaluations 16 --output local-output\v5-benchmark.json
.\.venv\Scripts\python.exe scripts\benchmark_industrial.py --repeats 3 --max-evaluations 32 --max-seconds 60 --output local-output\industrial-benchmark.json
```

macOS/Linux:

```sh
.venv/bin/python -m pytest -q
.venv/bin/python scripts/benchmark_connected.py --repeats 3 --max-evaluations 8 --output connected-benchmark.json
.venv/bin/python scripts/benchmark_v5.py --repeats 3 --max-evaluations 16 --output local-output/v5-benchmark.json
.venv/bin/python scripts/benchmark_industrial.py --repeats 3 --max-evaluations 32 --max-seconds 60 --output local-output/industrial-benchmark.json
```

The industrial benchmark uses a fresh worker process per scenario. It records calculated length/bends/collisions, preserved graph connections, considered/changed runs, independent validation, actual search counters, cold/warm timing and a separate traced Python allocation pass plus RSS where available. Considered runs can include justified unchanged-straight reuse; this does not imply an A* search or reroute for every run. Use `--no-memory` to skip allocation tracing when rehearsing. Generated reports belong in ignored `local-output/` or outside the checkout. Optional STEP support requires `requirements-cad.txt`; browser automation requires `requirements-browser.txt` and Chromium/Playwright setup. Three tests requiring excluded drawing-derived artifacts remain omitted. See [4.0](docs/edge/V4_0.md), [5.0](docs/edge/V5_0.md), [5.1](docs/edge/V5_1.md) and [sharing notes](docs/SHARING.md).

## Optional preserved FastAPI and React workspace

In a separate terminal with the full Python dependencies installed:

```sh
.venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

On Windows use `.\.venv\Scripts\python.exe` in place of `.venv/bin/python`. This service also exposes `/connected`. For the preserved React/Three application, run the following in another terminal:

```sh
cd frontend
npm ci
npm run build
npm run dev
```

Open the local address printed by Vite. Its development proxy uses the FastAPI service on port 8000. See [the preserved Edge setup](docs/edge/LEGACY_README.md), [3.2 hardening record](docs/edge/V3_2.md), and [optional CPU worker](docs/edge/REMOTE.md). Remote execution is optional; the connected-network optimizer runs locally.

## Optional AutoCAD integration

No plugin DLL or Autodesk binary is distributed here. Build the supplied source with the .NET 10 SDK and load it in AutoCAD 2027 for Windows. Run `PIPEFORGEOPTIMIZE` for multiple traced CAD sources, selected systems or the entire reviewed graph, then review and explicitly approve the result. See [plugin build instructions](autocad-plugin/README.md), [the 5.1 native workflow](docs/edge/AUTOCAD_5_1.md) and [Windows acceptance checklist](docs/edge/AUTOCAD_5.md). The separate legacy connector targets AutoCAD 2025/2026; its instructions are under [connectors/autocad](connectors/autocad/README.md).

Native commands provide inspection, explicit connection decisions, optimization settings, progress, cancellation, revision-bound review, export and optional separate undoable centerline overlays. Source pipe solids are preserved. Native execution inside AutoCAD remains unverified in this Linux development environment; a successful compile and HTTP tests do not establish runtime integration. Perform the documented acceptance checks on synthetic geometry first.

## Engineering and data limits

The versioned [standards profile](standards/fincantieri-track2-user-transcription-v1.json) records the user-supplied challenge transcription, source references, hard constraints, and preferences. Original official PDFs and approved manufacturer catalogs are not included or independently verified. Conflicting penetration-unit wording remains unresolved. Missing physical, installation, support, or coverage evidence stays UNVERIFIED.

No confidential drawing is required to install, run, or demonstrate this branch. Process your own CAD only when the data owner authorizes the processing environment. Keep confidential drawings, source-derived coordinates, credentials, generated reports, and logs outside Git. Repository access alone does not confer permission to process or redistribute engineering data.

No new real CAD model was supplied with the 5.1 request. Historical confidential drawings were not used as a substitute. Real-model interpretation and engineering acceptance remain pending an authorized model and an approved processing environment.
