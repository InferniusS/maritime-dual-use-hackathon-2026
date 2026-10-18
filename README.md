# Maritime Dual Use Hackathon 2026

We picked Track 2. We are building a tool that checks existing ship piping and suggests better routes while keeping pipe sizes and connection points fixed.

**Current status:** the team has displayed and saved a rounded proposal in the supplied cooling drawing on the Snapdragon. The newest build adds `MARITIMESCAN` to screen the full inferred drawing inventory and `MARITIMEFIND` to locate its findings. The supplied cooling file has 1,416 inferred sections; all were geometrically screened on the Mac with none omitted. Follow the [whole-drawing Snapdragon guide](docs/whole-drawing-snapdragon-test.md). The new commands need a Windows rehearsal. Complete engineering and fabrication requirements remain tracked in the [acceptance matrix](docs/requirement-acceptance-matrix.md).

## Start with AutoCAD

**At the venue start with the [3D demo guide](docs/3d-demo-snapdragon.md).** It gives the update commands and exact AutoCAD steps. Use the [full setup guide](docs/snapdragon-guide.md) for a fresh installation. This milestone changes the Python checks, examples, reports and plugin. Replacing only `run_web.py` is not enough.

The [plugin guide](autocad-plugin/README.md) explains the commands and optional source build. The short version is:

1. Download or clone this repo on the AutoCAD laptop.
2. Install the Python environment and start the local service.
3. Load `autocad-plugin/prebuilt/Maritime.AutoCAD.dll` through `NETLOAD` in AutoCAD 2027 for Windows. Rebuilding from source is also supported.
4. For a supplied drawing, run `MARITIMESCAN`, open its report, use `MARITIMEFIND` to locate a numbered section, and `MARITIMEOPTIMIZE` to coordinate selected repairs. `MARITIMEREVIEW` remains a quick bounded review; `MARITIMECAD` selects one known pipe. For the labeled synthetic fallback, use an empty drawing and `MARITIMEDEMO` with `examples/assembly/01-congested-room.json`.

The Python service runs on the same laptop. You do not need to open the website or upload the drawing through a browser. AutoCAD sends a temporary DXF snapshot to the local service.

`MARITIMEVIEW` switches saved review layers between Original, Proposal and Both. `MARITIMEPREVIEW` and `MARITIMELINEOPTIMIZE` are available for line and polyline tests; the latter can also request a rounded pressure-pipe proposal. `MARITIMEOPTIMIZE` now coordinates sections from actual CAD solids. Saved proposals are separate review geometry. They do not replace the source pipe solids.

## What this iteration adds

Coordinated runs keep every requested outcome visible. Accepted proposals reserve their full pipe width and the larger requested pair clearance before later sections are searched. A final pair check rejects mutual clashes. Source objects, endpoints and diameters are preserved. Combined 3D comparison, section identity, generated-piece coordinates and rule findings are exported together. This remains order-dependent selected-section search; branches and the entire model are not silently treated as solved.

Fresh CAD acceptance now exercises uploaded DXF geometry through extraction and search, including repair, unchanged and refusal cases, translated block placement and a joined nine-component detour. [Native AutoCAD scripts](examples/cad-acceptance/README.md) are included for the venue runtime test. Synthetic fixtures are labeled and do not guarantee unseen judge files.

The full scan accounts for every supported inferred section, including dense regions checked in bounded obstacle batches. Stable numbers, source handles, progress, cancellation and searchable HTML/Excel findings make all outcomes inspectable. Unsupported geometry, ambiguous connections and missing rule evidence remain explicit. The older quick review still lists at most 50 sections; coordinated optimization still accepts at most twelve selections per run. Screening all sections does not automatically redesign or certify the system.

Rule findings distinguish computed geometry, supplied measurements, reviewer declarations and unimplemented checks. This distinction is separate from pass/fail: a declaration cannot silently become proof that the software measured the complete model.

Proposed-piece checks now measure lengths, interface spacing, bend counts, angles and planarity from the generated geometry. Verified nominal-size evidence enables radius and straight-between-bends checks. Missing commercial fittings, joint design and installation evidence remain review items. AutoCAD now frames the selected 3D proposal and saved comparison automatically.

The previous visible iteration generates straight pipe envelopes and circular bends at the selected outside diameter. It rejects rounding that cannot fit the fixed ends or supplied surroundings. It compares bounded alternatives by rounded bend count, vertical runs, then rounded length.

The new controlled congested-room example changes from about **17.0767 m and four rounded bends** to **13.4199 m and two rounded bends**, with fixed connections preserved. It is labeled synthetic: the source drawing was not secretly damaged or presented as having that defect. Other cases cover a collision repair, a blocked search, an elevation change and an unchanged valid route.

The HTML report, DXF and Excel review now include rounded geometry or component dimensions. Printed reports include a static diagram so the 3D browser plot does not disappear from the PDF. These are **geometric proposals**, not approved catalog fittings or a complete manufactured bill of materials.

The V3 Challenge profile, 50.8 mm minimum clearance and `MARITIMECONTEXT` support remain. The team's latest Snapdragon report confirms that the Challenge profile ran. Additional context loading and complete engineering checks are separate tasks.

Read [what changed, measured results and what remains](docs/assembly-iteration.md). Keep AutoCAD as the main application. Navisworks remains optional for combined-model review; the [earlier assessment](docs/standards-iteration.md#navisworks-decision) explains why switching does not supply the remaining routing or engineering logic.

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

This saved example is from the earlier box-based iteration. The latest [3D proposal iteration](docs/assembly-iteration.md) has its own controlled examples and measurements. This older example retains its original unit and rule assumptions. Passing the implemented checks does not establish official engineering compliance.

## Start the Python service

Use standard CPython 3.14.x. Create a separate environment for this project on each computer. Run these commands from the extracted repo folder that contains `requirements-lock.txt` and `scripts`.

### Windows

The team laptop already reports Python 3.14.8 through the `python` command. Use the commands below in PowerShell. Run **one block at a time** and wait for it to finish. If any step reports an error stop there and use the [Snapdragon guide](docs/snapdragon-guide.md) before continuing.

First create the project environment:

```powershell
python -m venv .venv
```

Install the required packages:

```powershell
.\.venv\Scripts\python.exe -m pip install --only-binary=:all: -r requirements-lock.txt
```

Check that the packages agree with each other:

```powershell
.\.venv\Scripts\python.exe -m pip check
```

This should say `No broken requirements found.` Then run the project check:

```powershell
.\.venv\Scripts\python.exe scripts\check_setup.py
```

Continue only when it prints `PASS`. Start the local service:

```powershell
.\.venv\Scripts\python.exe scripts\run_web.py
```

These commands call the environment's Python directly. You do not need to run `Activate.ps1` or change PowerShell's execution policy. The blocked setup script is not needed for these steps.

### Mac

```sh
bash scripts/setup_mac.sh
```

After setup finishes start the service:

```sh
.venv/bin/python scripts/run_web.py
```

Efe's Mac is already set up and only needs the second command. If Python is not found you can give the setup script its full path.

Keep the terminal running. The plugin uses [http://127.0.0.1:8767/](http://127.0.0.1:8767/) on the same computer. That address will not connect to Efe's Mac from the venue. A browser is optional when using the plugin.

The local service, real drawing Save and Challenge report have run on the Snapdragon laptop. The new 3D commands and a reviewed context file still need their venue test. Create the environment on each laptop instead of copying the Mac's `.venv`.

## Browser fallback

Open [http://127.0.0.1:8767/](http://127.0.0.1:8767/) while the Python service is running.

In **Check a route** choose **Open a layout file** and load [baseline.json](examples/real-cad-demo/baseline.json). Click **Check and improve route**. Repeat with [detour.json](examples/real-cad-demo/detour.json) to test the artificial detour. These inputs include the route and local obstacles. The full organizer drawing is not required.

In **Explore real CAD** open a local DXF. Convert DWG files to DXF first. The browser app can display supported geometry and find possible pipe runs. It can prepare a local route test and export coordinates with a centerline DXF and an offline report. Eight example layouts also cover pressure pipes and gravity drains.

The browser app does not need AutoCAD or FreeCAD to run. The plugin needs AutoCAD 2027 for Windows. It cannot run as a managed plugin in AutoCAD for Mac.

Original organizer files stay outside Git. Get those from [the organizer Drive folder](https://drive.google.com/drive/folders/18ESskQZ73e8eus2aQ8Cm-h57o3OF8fop) to inspect the full drawing. See [the CAD guide](docs/cad-workflow.md) for conversion and import details.

## What comes next

1. Update Python files and the DLL together on the Snapdragon. Run `scripts/check_assembly.py`, then rehearse the new AutoCAD comparison, Save and Undo using the [3D demo guide](docs/3d-demo-snapdragon.md).
2. Confirm actual component identities, port orientations, nominal sizes and suitable catalog parts. The new rounded envelope checks do not establish manufacturing approval.
3. Preserve branches, valves and required spaces across longer connected sections. The current optimizer handles one selected non-branching route at a time.
4. Verify the complete aligned surroundings and access areas on real drawings. Missing structure or other systems remain unchecked.
5. Complete fabrication, installation and system-function checks and the final parts list. Test held-out valid and damaged cases across systems.

The current deliverable is a routing and review prototype with a defined demo. It is not yet a complete engineering solution for every unseen ship model. The [demo readiness assessment](docs/demo-readiness.md) separates measured claims from remaining work.

The Mac is our development machine. The Snapdragon laptop is the AutoCAD demo target. The algorithm searches possible routes and does not learn from uploaded drawings. No IQ9 service or AI processor integration is included. IQ9 is not needed for this current workflow.

## Project notes

- [Snapdragon setup one step at a time](docs/snapdragon-guide.md)
- [New 3D demo steps on Snapdragon](docs/3d-demo-snapdragon.md)
- [3D proposal iteration and remaining work](docs/assembly-iteration.md)
- [AutoCAD plugin setup and commands](autocad-plugin/README.md)
- [Team handoff and next tasks](docs/team-handoff.md)
- [Standards iteration and Navisworks assessment](docs/standards-iteration.md)
- [Pipe-shape milestone and measured results](docs/pipe-shape-milestone.md)
- [Challenge requirements and current coverage](docs/challenge-requirements.md)
- [How the route checks work](docs/routing-prototype.md)
- [CAD import and known gaps](docs/cad-workflow.md)
- [Units and rule assumptions](docs/assumptions.md)
- [Packages and setup details](docs/dependencies.md)

Run tests with `.venv/bin/python -m pytest -q` on Mac or `.venv\Scripts\python -m pytest -q` on Windows. The five new demo scenarios run with `scripts/check_assembly.py`. Real drawing Save and the Challenge report are confirmed on the demo laptop. The new 3D comparison, context loading and an explicit Undo check still need live verification.

## Team

- Efe ([@InferniusS](https://github.com/InferniusS))
- Hasan Onallah ([@HasanOnallah1](https://github.com/HasanOnallah1))
- Murad Gasimov ([@EJICKmini](https://github.com/EJICKmini))
- Krishna Challapalli

## AI use

Codex generated the initial README and `.gitignore` before challenge implementation. During the event Codex generated the initial prototype code in `pipe_router/` and `web/` along with the scripts and examples and tests. Codex also helped extend the AutoCAD integration and write this documentation. All initial code in those new prototype files was AI generated. The exact model build was not recorded.

Automated validation has run on the Mac. The simple line Save, real drawing Save and V3 Challenge report have run on the Snapdragon laptop. The latest rounded geometry and final CAD comparison still need venue review. IQ9 execution remains unverified. Keep recording AI assistance as the project grows.
