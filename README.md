# Maritime Dual Use Hackathon 2026

We picked Track 2. We are building a tool that checks existing ship piping and suggests better routes while keeping pipe sizes and connection points fixed.

**Current direction on October 17:** use the tool inside AutoCAD. The plugin connects the drawing to our existing Python routing code. The browser app is still available for testing and as a demo fallback. The plugin still needs a live check in AutoCAD on the event laptop.

## Start with AutoCAD

The [AutoCAD setup guide](autocad-plugin/README.md) has the full steps. The short version is:

1. Download or clone this repo on the AutoCAD laptop.
2. Install the Python environment and start the local service.
3. Load `autocad-plugin/prebuilt/Maritime.AutoCAD.dll` through `NETLOAD` in AutoCAD 2027 for Windows. Rebuilding from source is also supported.
4. Run `MARITIMECAD` to select a pipe solid or block from the drawing. Choose a detected route and review the result inside AutoCAD.

The Python service runs on the same laptop. You do not need to open the website or upload the drawing through a browser. AutoCAD sends a temporary DXF snapshot to the local service.

`MARITIMEPREVIEW` and `MARITIMEOPTIMIZE` remain available for simple line and polyline tests. The new drawing workflow can also save a suggestion as a separate review centerline. It does not replace the original pipe solids.

## What this iteration adds

- Starts a real drawing test from a selected object in AutoCAD
- Reuses the existing CAD importer and route search
- Shows possible routes associated with that selected object
- Sends a chosen route through the same checks used by the browser app
- Returns a result for review in the drawing
- Keeps the browser app and saved example available

This builds on the previous iteration. That version could join compatible component ends into longer routes and test an artificial detour. The plugin changes how we use that work. It does not turn the search into a trained model or add whole ship optimization.

## See the saved result without installing anything

![Current piping test](docs/images/current-model.jpg)

For Murad and Krishna at the venue:

1. Download this repo with **Code → Download ZIP** or clone it.
2. Extract it and open [report.html](examples/real-cad-demo/report.html) in your browser.
3. Rotate the saved 3D result and compare the original route with the suggestion. This works offline and does not need Python or CAD software.

The screenshot shows the full CAD background. The saved HTML shows one selected route and its 36 obstacle boxes. It contains a saved calculation. Use the plugin or browser app to run a new search.

| Route | Length |
| --- | ---: |
| Untouched CAD baseline | 3679.25 mm |
| Baseline with an artificial detour | 4179.25 mm |
| Suggested replacement | 3728.30 mm |

The search removes about 451 mm of the added detour. The untouched CAD baseline is still shorter. This is a recovery test rather than evidence that we improved the supplied design. It uses an assumed 50 mm clearance.

The Python code passed 373 automated tests on the Mac. The new snapshot path also read the real cooling drawing and found the expected joined route. The units and rules remain editable prototype assumptions. Passing the implemented checks does not establish official engineering compliance.

## Start the Python service

Use standard CPython 3.14.x. On a Snapdragon Windows laptop use the ARM64 build for the Python service. The setup scripts create a separate environment for this project. Run these commands from the extracted repo folder.

### Windows

```powershell
.\scripts\setup_windows.ps1
.\.venv\Scripts\python.exe scripts\run_web.py
```

If PowerShell blocks the setup script use these commands instead:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --only-binary=:all: -r requirements-lock.txt
.\.venv\Scripts\python.exe scripts\check_setup.py
.\.venv\Scripts\python.exe scripts\run_web.py
```

### Mac

```sh
bash scripts/setup_mac.sh
.venv/bin/python scripts/run_web.py
```

Efe's Mac is already set up and only needs the second command. If Python is not found you can give the setup script its full path.

Keep the terminal running. The plugin uses [http://127.0.0.1:8767/](http://127.0.0.1:8767/) on the same computer. That address will not connect to Efe's Mac from the venue. A browser is optional when using the plugin.

The Windows packages were checked for ARM64 availability. The actual installation and AutoCAD workflow still need verification on the event laptop. Recreate the environment there instead of copying the Mac's `.venv`.

## Browser fallback

Open [http://127.0.0.1:8767/](http://127.0.0.1:8767/) while the Python service is running.

In **Check a route** choose **Open a layout file** and load [baseline.json](examples/real-cad-demo/baseline.json). Click **Check and improve route**. Repeat with [detour.json](examples/real-cad-demo/detour.json) to test the artificial detour. These inputs include the route and local obstacles. The full organizer drawing is not required.

In **Explore real CAD** open a local DXF. Convert DWG files to DXF first. The browser app can display supported geometry and find possible pipe runs. It can prepare a local route test and export coordinates with a centerline DXF and an offline report. Eight example layouts also cover pressure pipes and gravity drains.

The browser app does not need AutoCAD or FreeCAD to run. The plugin needs AutoCAD 2027 for Windows. It cannot run as a managed plugin in AutoCAD for Mac.

Original organizer files stay outside Git. Get those from [the organizer Drive folder](https://drive.google.com/drive/folders/18ESskQZ73e8eus2aQ8Cm-h57o3OF8fop) to inspect the full drawing. See [the CAD guide](docs/cad-workflow.md) for conversion and import details.

## What comes next

1. Load the plugin on the event laptop and check a real drawing through AutoCAD.
2. Confirm the result appears at the right scale and position. Try keeping a review centerline and undoing it.
3. Preserve branch connections and required equipment through longer routes.
4. Include more surrounding geometry and check actual fitting shapes.
5. Demonstrate an improvement on a reviewed real problem and flag anything that still needs a designer.

The Mac is our development machine. The Snapdragon laptop is the AutoCAD demo target. The algorithm searches possible routes and does not learn from uploaded drawings. No IQ9 service or AI processor integration is included. IQ9 is not needed for this current workflow.

## Project notes

- [AutoCAD plugin setup and commands](autocad-plugin/README.md)
- [Team handoff and next tasks](docs/team-handoff.md)
- [Challenge requirements and current coverage](docs/challenge-requirements.md)
- [How the route checks work](docs/routing-prototype.md)
- [CAD import and known gaps](docs/cad-workflow.md)
- [Units and rule assumptions](docs/assumptions.md)
- [Packages and setup details](docs/dependencies.md)

Run tests with `.venv/bin/python -m pytest -q` on Mac or `.venv\Scripts\python -m pytest -q` on Windows. A successful test run or DLL build does not verify that the plugin loads in AutoCAD. That check must happen on the demo laptop.

## Team

- Efe ([@InferniusS](https://github.com/InferniusS))
- Hasan Onallah ([@HasanOnallah1](https://github.com/HasanOnallah1))
- Murad Gasimov ([@EJICKmini](https://github.com/EJICKmini))
- Krishna Challapalli

## AI use

Codex generated the initial README and `.gitignore` before challenge implementation. During the event Codex generated the initial prototype code in `pipe_router/` and `web/` along with the scripts and examples and tests. Codex also helped extend the AutoCAD integration and write this documentation. All initial code in those new prototype files was AI generated. The exact model build was not recorded.

Automated validation has run on the Mac. Human code review and final CAD comparison are still pending. Event laptop and IQ9 execution have not been verified. Keep recording AI assistance as the project grows.
