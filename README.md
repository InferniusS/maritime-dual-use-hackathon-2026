# Maritime Dual Use Hackathon 2026

We picked Track 2. We are building a tool that checks existing ship piping and suggests better routes while keeping pipe sizes and connection points fixed.

**Current status on October 17:** the Mac prototype can display real CAD geometry and run a local routing test on a joined pipe route. It can recover from an artificial detour in that route. A complete ship system and the final Snapdragon demo are still unfinished.

## See what we have now

![Current piping test](docs/images/current-model.jpg)

For Murad and Krishna at the venue:

1. Download this repo with **Code → Download ZIP** or clone it.
2. Extract it and open [examples/real-cad-demo/report.html](examples/real-cad-demo/report.html) in your browser.
3. Rotate the saved 3D result and compare the original route with the suggestion. This works offline and does not need Python or CAD software.

GitHub shows the screenshot above with the full CAD background. Download the HTML to explore the selected route and its 36 obstacle boxes. The report shows a saved calculation. Use the app below to change inputs and run the search again.

The [team handoff](docs/team-handoff.md) explains the current result and what we should tackle next.

## What this version does

- Displays supported geometry from uploaded DXF files
- Finds possible pipe runs and joins compatible component ends into longer routes
- Checks a selected route against nearby obstacle boxes
- Keeps the original connection end sections fixed when the user applies that assumption
- Creates a labeled artificial detour so we can test whether the search repairs it
- Runs eight example layouts including pressure pipes and gravity drains
- Exports exact coordinates with a centerline DXF and an offline report

The latest saved example comes from three components in the cooling drawing.

| Route | Length |
| --- | ---: |
| Untouched CAD baseline | 3679.25 mm |
| Baseline with an artificial detour | 4179.25 mm |
| Suggested replacement | 3728.30 mm |

The search removes about 451 mm of the added detour. The untouched CAD baseline is still shorter. This is a recovery test rather than evidence that we improved the supplied design. It retains all 36 nearby obstacle boxes with an assumed 50 mm clearance.

**310 automated tests passed on the Mac.** The workflow and baseline restore were also checked in the browser. The units and rules remain editable prototype assumptions. Passing these checks does not establish official engineering compliance.

## Run the app

Use standard CPython 3.14.x. On a Snapdragon Windows laptop use the ARM64 build. The setup scripts create a separate environment for this project. Run these commands from the extracted repo folder.

### Mac

```sh
bash scripts/setup_mac.sh
.venv/bin/python scripts/run_web.py
```

Efe's Mac is already set up and only needs the second command. If Python is not found you can give the setup script its full path.

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

Open [http://127.0.0.1:8767/](http://127.0.0.1:8767/) on the same laptop. Keep the terminal running. That address points to your own computer so it will not connect to Efe's Mac from the venue.

In **Check a route** choose **Open a layout file** and load [baseline.json](examples/real-cad-demo/baseline.json). Click **Check and improve route**. Repeat with [detour.json](examples/real-cad-demo/detour.json) to test the artificial detour. These saved inputs include the local route and obstacles so the full organizer drawing is not required for this test.

The Windows packages were checked for ARM64 availability. The actual setup and full workflow still need to be tested on the event laptop. Recreate the environment there instead of copying the Mac's `.venv`.

## AutoCAD 2027 plug-in prototype

A separate C# plug-in prototype is in [`autocad-plugin/`](autocad-plugin/README.md). Build it on the Windows laptop with the .NET 10 SDK and AutoCAD 2027 managed assemblies, then load the generated DLL with `NETLOAD`. `MARITIMEPREVIEW` shows the selected route as a temporary cyan overlay. `MARITIMEOPTIMIZE` sends the route and selected obstacle objects to the Python service running on the same laptop, then shows the suggested route as a temporary green overlay. Neither command changes the drawing. See the plug-in README for setup and limitations.

## Use a full drawing

In **Explore real CAD** open a local DXF. Convert DWG files to DXF first. The browser app does not need AutoCAD or FreeCAD to run. The final result still needs comparison in the CAD software required for the challenge.

Original organizer files stay outside Git. Get those from [the organizer Drive folder](https://drive.google.com/drive/folders/18ESskQZ73e8eus2aQ8Cm-h57o3OF8fop) to inspect the full drawing. This repo includes the small saved routing example so everyone can reproduce our current test. See [the CAD guide](docs/cad-workflow.md) for conversion and import details.

## What comes next

1. Run the saved example on the Snapdragon laptop and compare its exported centerlines in AutoCAD or Navisworks.
2. Preserve branch connections and required equipment through longer routes.
3. Include more surrounding geometry and check actual fitting shapes.
4. Demonstrate an improvement on a reviewed real problem and explain anything that still needs a designer.

The algorithm currently searches possible routes. It does not train a model or learn from uploaded drawings. The Mac is our development machine. The Snapdragon laptop is the demo target. No IQ9 service or AI processor integration is included yet.

## Project notes

- [Team handoff and next tasks](docs/team-handoff.md)
- [Challenge requirements and current coverage](docs/challenge-requirements.md)
- [How the route checks work](docs/routing-prototype.md)
- [CAD import and known gaps](docs/cad-workflow.md)
- [Units and rule assumptions](docs/assumptions.md)
- [Packages and setup details](docs/dependencies.md)

Run tests with `.venv/bin/python -m pytest -q` on Mac or `.venv\Scripts\python -m pytest -q` on Windows.

## Team

- Efe ([@InferniusS](https://github.com/InferniusS))
- Hasan Onallah ([@HasanOnallah1](https://github.com/HasanOnallah1))
- Murad Gasimov ([@EJICKmini](https://github.com/EJICKmini))
- Krishna Challapalli

## AI use

Codex generated the initial README and `.gitignore` before challenge implementation. During the event Codex generated the initial prototype code in `pipe_router/` and `web/` along with the scripts and examples and tests. Codex also helped write this documentation. All initial code in those new prototype files was AI generated. The exact model build was not recorded.

Automated validation has run on the Mac. Human code review and final CAD comparison are still pending. Event laptop and IQ9 execution have not been verified. Keep recording AI assistance as the project grows.
