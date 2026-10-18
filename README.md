# PipeForge Edge 4.0

PipeForge Edge checks connected piping networks, proposes coordinated route changes, and produces CAD-ready review instructions, coordinates, and an Excel component schedule. The application includes the team’s `pipe_router` engine and AutoCAD 2027 plugin source alongside the preserved Edge 3.2 FastAPI/React workspace.

This is a fresh-history, source-only collaboration snapshot of `a267421136c32effe103f8d96e2706d632c24126`, including team code from `227a61351f9d5a37a00c15ce457329709a01def9`. Only synthetic JSON demonstration geometry is supplied. Drawing-derived artifacts, screenshots, CAD files, prebuilt binaries, and the original Git history are excluded. See [sharing scope](docs/SHARING.md) and [the manifest](SHARING_MANIFEST.json).

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

## Run the connected-network demonstration

Follow [the 90-second synthetic demo](docs/edge/DEMO_4.md).

1. In the connected workspace, load **C · Connected branch** from the supplied examples.
2. Review the equipment ports, valve movement limits, tee, systems, and explicit connections. Run optimization and compare the original/proposed networks.
3. Inspect numeric constraints and UNVERIFIED findings. The synthetic C case is expected to change two coordinated spans; use the current calculation as the result.
4. Explicitly approve the current instruction export and download the JSON/Markdown instructions, XLSX/CSV schedules, and generated DXF review overlay. Generated exports stay local; no CAD export is bundled in this snapshot.
5. Load **A · Already good** to demonstrate **NO IMPROVEMENT FOUND**, then **D · Blocked** to inspect an unsuccessful search without an invalid replacement.

The six cases in `examples/connected/` cover a valid unchanged route, collision repair, connected branches, blockage, multiple systems, and directional drainage. All are invented geometry. Exported schedules and centerlines are designer review material, not fabrication approval.

## Test and reproduce measurements

Windows:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts\benchmark_connected.py --repeats 3 --max-evaluations 8 --output connected-benchmark.json
```

macOS/Linux:

```sh
.venv/bin/python -m pytest -q
.venv/bin/python scripts/benchmark_connected.py --repeats 3 --max-evaluations 8 --output connected-benchmark.json
```

Optional STEP import/export requires `requirements-cad.txt`; optional browser automation requires `requirements-browser.txt` and Chromium/Playwright setup. Core tests skip unavailable optional CAD support. Three tests requiring excluded drawing-derived artifacts are omitted from this snapshot. All application runtime source is preserved unchanged. See [the original implementation record](docs/edge/V4_0.md) for development evidence and limits; snapshot verification is recorded in [sharing notes](docs/SHARING.md).

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

No plugin DLL or Autodesk binary is distributed here. Build the supplied source with the .NET 10 SDK and load it in AutoCAD 2027 for Windows. See [plugin build instructions](autocad-plugin/README.md) and [the connected command checklist](docs/edge/AUTOCAD_4.md). The separate legacy connector targets AutoCAD 2025/2026; its instructions are under [connectors/autocad](connectors/autocad/README.md).

Native commands provide inspection, explicit connection decisions, revision-bound review, and optional separate centerline overlays. They do not automatically replace the original pipe solids. A successful compile or HTTP test does not establish successful execution inside AutoCAD; perform the documented acceptance checks on synthetic geometry first.

## Engineering and data limits

The versioned [standards profile](standards/fincantieri-track2-user-transcription-v1.json) records the user-supplied challenge transcription, source references, hard constraints, and preferences. Original official PDFs and approved manufacturer catalogs are not included or independently verified. Conflicting penetration-unit wording remains unresolved. Missing physical, installation, support, or coverage evidence stays UNVERIFIED.

No confidential drawing is required to install, run, or demonstrate this branch. Process your own CAD only when the data owner authorizes the processing environment. Keep confidential drawings, source-derived coordinates, credentials, generated reports, and logs outside Git. Repository access alone does not confer permission to process or redistribute engineering data.
