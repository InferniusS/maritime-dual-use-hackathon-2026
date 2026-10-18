# AutoCAD plugin

This plugin brings the existing Python pipe router into AutoCAD. AutoCAD handles selection and review. A local Python service reads the drawing snapshot and searches for a route.

The website does not need to be open. Both the plugin and browser app use the same calculation code.

**Update with the [Snapdragon guide](../docs/3d-demo-snapdragon.md), then follow the [pitch workflow](../docs/pitch-demo.md).** The team has displayed and saved the 3D comparison at the venue. This build adds `MARITIMEREVIEW`, proposed-piece findings and automatic framing; those changes need a new live rehearsal. The [original setup guide](../docs/snapdragon-guide.md) remains available for fresh installations.

## Requirements

- AutoCAD 2027 for Windows with managed .NET plugin support
- Standard Python 3.14.x for the local service
- The .NET 10 SDK only if you want to rebuild the plugin

The DLL targets an x64 AutoCAD process. The simple line, real solid and rounded demo comparison have run on the tested Snapdragon installation. The new drawing review, context workflow and Undo need explicit venue checks.

AutoCAD for Mac cannot load this managed .NET plugin. The Mac can run the Python service and browser tests. It can also compile the Windows DLL using the official Autodesk reference package.

## Start the local service

Open PowerShell in the extracted repo folder. You should see `requirements-lock.txt` and the `scripts` folder there. The team laptop already has a working `python` command for Python 3.14.8.

Run each block separately. Wait for it to finish before continuing. **Stop at the first error.** The [Snapdragon guide](../docs/snapdragon-guide.md) explains what each result should look like.

Create the project environment:

```powershell
python -m venv .venv
```

Install the packages:

```powershell
.\.venv\Scripts\python.exe -m pip install --only-binary=:all: -r requirements-lock.txt
```

Check their dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip check
```

This should print `No broken requirements found.` Then check the project:

```powershell
.\.venv\Scripts\python.exe scripts\check_setup.py
```

Start the service only after the check prints `PASS`:

```powershell
.\.venv\Scripts\python.exe scripts\run_web.py
```

Keep this window running. The plugin connects to `http://127.0.0.1:8767` on the same computer. It does not connect to Efe's Mac or a hosted website.

These commands do not require `Activate.ps1` or a PowerShell execution policy change. You can leave the blocked setup script alone.

Check the service at [http://127.0.0.1:8767/api/health](http://127.0.0.1:8767/api/health). It should identify itself as `maritime-pipe-router` with API version 1. Its capabilities must include `drawing_review`, `rounded_pipe_proposal`, `challenge_rules_v3`, `model_context` and `review_excel`. If they are missing, update the Python files and restart the service before loading the current DLL.

## Use the included DLL

The repo includes [Maritime.AutoCAD.dll](prebuilt/Maritime.AutoCAD.dll) in `autocad-plugin/prebuilt`. Use this for the venue tests. You do not need the .NET SDK to load an already built plugin. AutoCAD 2027 provides the runtime.

[build-info.json](prebuilt/build-info.json) records the source hashes for that build. The package contains our DLL and does not bundle Autodesk runtime DLLs. Those records describe the build. The venue screenshots confirm the earlier simple line and real drawing review saves. A successful build alone does not establish that new commands have run on the laptop.

## Rebuild from source

With the .NET 10 SDK installed run this from the repo folder:

```powershell
dotnet build autocad-plugin/Maritime.AutoCAD.csproj -c Release
```

The same build command works on the Mac. It downloads the official `AutoCAD.NET` 26.0.0 reference package. AutoCAD itself is still required to run the result.

The DLL is written here:

```text
autocad-plugin/bin/Release/net10.0-windows/Maritime.AutoCAD.dll
```

The build does not package Autodesk runtime DLLs. AutoCAD provides them when the plugin is loaded.

If you prefer to build against an installed AutoCAD 2027 SDK instead of the NuGet package:

```powershell
dotnet build autocad-plugin/Maritime.AutoCAD.csproj -c Release -p:AutoCADManagedDir="C:\Program Files\Autodesk\AutoCAD 2027"
```

That folder must contain `AcCoreMgd.dll` and `AcDbMgd.dll` and `AcMgd.dll`. A different AutoCAD release may need a different target and SDK. This project currently targets 2027.

## Load it in AutoCAD

1. Open AutoCAD 2027 for Windows and a test copy of the drawing.
2. Enter `NETLOAD`.
3. Select `autocad-plugin/prebuilt/Maritime.AutoCAD.dll`. If you rebuilt it use the DLL from `bin/Release/net10.0-windows` instead.
4. Run `MARITIMEPREVIEW` on a simple line to confirm the command loads and the temporary overlay appears.
5. Start the Python service if it is not already running.
6. Run `MARITIMECAD` to test a real pipe solid or block.

If AutoCAD blocks the DLL use a trusted project folder or add that specific folder to `TRUSTEDPATHS`. Keep the normal security settings enabled. After rebuilding a DLL that AutoCAD has already loaded you may need to restart AutoCAD before loading the new version.

## Commands

| Command | What it is for |
| --- | --- |
| `MARITIMECAD` | Read a pipe solid or block; check a detected route and optionally review a rounded 3D pressure-pipe proposal |
| `MARITIMEDEMO` | Run a labeled synthetic layout in a new empty drawing with full-width before/after geometry |
| `MARITIMEVIEW` | Show saved Original, Proposal or Both review layers |
| `MARITIMECONTEXT` | Load, inspect the loaded status or clear reviewed context for the active drawing session |
| `MARITIMEOPTIMIZE` | Test a route made from selected straight lines or polylines with selected obstacle objects |
| `MARITIMEPREVIEW` | Check that selected line geometry can be shown as a temporary cyan overlay |

### Real drawing workflow

`MARITIMECAD` uses a temporary AutoCAD 2010 DXF snapshot. It sends that snapshot and the selected source handle to the local service. You do not need to export a DXF by hand or upload it in the browser.

The service looks for supported pipe geometry associated with the selection. It lists longer joined routes before individual runs. A match is a geometric candidate that still needs review as a real pipe connection.

The command asks for these settings before you choose a route:

| Setting | Initial value |
| --- | --- |
| Drawing units | Choose millimeters or meters or inches |
| System | Pressure |
| Rule profile | Challenge |
| Clearance | 50.8 mm for Challenge, 50 mm for Prototype |
| Local search padding | 500 mm |
| Search grid step | 250 mm |
| Gravity flow direction | Keep the inferred start and end |
| Minimum gravity slope | 0.01 fall per horizontal distance |

Challenge uses the V3 brief and rejects clearance below 50.8 mm. Prototype retains the earlier assumptions for replay. Units, padding and grid remain explicit choices. The gravity options appear only for Gravity. The generic slope prompt is not a substitute for declared ship axes and flow direction. Challenge directional checks need those declarations in reviewed context and engineering evidence. Missing information appears as `needs_review`.

After you choose a candidate the command shows its original route in cyan. Check the endpoints and included components. The service prepares nearby supported pipe envelopes and fallback boxes. It keeps the route endpoints fixed. Loaded context may add surrounding geometry, allowed spaces and protected exclusion volumes.

If obstacle envelopes touch the ends the command lists those contacts. It asks whether to treat all listed contacts as intended fixed connections. The default is No. Choosing Yes preserves bounded original end sections. The initial limit is 250 mm and the maximum is 1000 mm. This assumption does not allow the movable middle to pass through an obstacle.

The final preview shows the original route in cyan and the suggestion in green. The command prints lengths, geometric check results and separate V3 rule findings with local links to JSON, DXF, HTML and Excel review files. Green identifies a geometrically checked suggestion. It does not certify complete standards compliance. If a route improves under the implemented checks you can choose **Save**. The default is **Discard**. Unchanged results and failed searches do not offer a replacement to save.

Save adds a 3D review polyline on the `MARITIME_SUGGESTION` layer with source information. It keeps the original pipe solids and can be undone. A review centerline is a proposed path for a designer to inspect. It is not a new pipe assembly with validated fittings.

The command handles one selected route at a time. It does not optimize the complete ship drawing in one step. Select objects directly in model space. For external references open the referenced drawing itself. Snapshot uploads are limited to 48 MB. An unsupported object or an overly broad block selection may not produce a usable candidate.

### Drawing context

Run `MARITIMECONTEXT` with the intended drawing active. Choose `Load` and select a reviewed JSON file. It accepts a raw context object or a wrapper with `model_context` and optional `engineering`. The file must be at most 16 MB. Use `Status` to check whether context is loaded or `Clear` to remove it.

Context is tied to the active drawing's fingerprint for the current AutoCAD session. Reload it after restarting AutoCAD. The service validates its geometry and evidence when `MARITIMECAD` prepares a route. Loading a file alone does not establish coverage or alignment.

The [context preparation guide](../docs/standards-iteration.md#add-reviewed-surroundings) explains `examples/model-context-template.json` and `scripts/build_model_context.py`. The builder reads local aligned DXF exports without changing them. It does not align drawings, infer allowed compartments or approve coverage.

Manufacturing and access data must be explicit and tied to the original route. Changing the route does not automatically update that evidence. The Excel review contains coordinates, findings and declared pieces. It is not a finished manufactured parts list.

### Simple line workflow

Use `MARITIMEOPTIMIZE` when the intended route already exists as straight line or polyline centerlines.

Select one connected route and the obstacles to check. Enter the outside diameter and the clearance. Then choose the search settings and drawing units. The service returns the same comparison preview used by the real drawing command. Improved results also offer Save or Discard.

This path uses selected obstacle extents as boxes. It checks the geometry you selected. It does not infer other objects or required connections from the whole drawing.

### Preview only

`MARITIMEPREVIEW` shows selected line geometry in cyan. It is a quick way to check that the plugin loads and draws an overlay. It does not run a route search. Press Enter to clear the temporary graphics.

## Venue checks

The simple line and real drawing Save paths are complete on the Snapdragon. The new profile, context and report flow remain the next live checks. The list below also applies when setting up another laptop.

1. Update the Python code, examples and DLL together. Close AutoCAD before replacing a loaded DLL.
2. Run `scripts/check_challenge.py` from the repository with the project Python. Expect eight `PASS` lines.
3. Start the service and confirm the health capabilities listed above. Load the fresh DLL with `NETLOAD` and confirm all four commands are recognized.
4. Run `MARITIMECAD` on a supported pipe object in a cooling drawing copy. Choose Millimetres, Pressure, Challenge, 50.8 mm clearance, 500 mm padding and 250 mm grid. Confirm the selected route's position and scale.
5. For DB1, the latest Mac replay returns a 2558 mm suggestion for the 2413.467 mm original. The joined DB2/E6D/E16 route remains unchanged at about 3679.248 mm. Review endpoint contacts before accepting bounded fixed sections of at most 250 mm. Missing engineering information must remain visible in the report.
6. Load prepared reviewed context with `MARITIMECONTEXT`. Rerun the route and inspect the added scene coverage and rule findings. Do not claim complete ship coverage from a cooling-only snapshot.
7. On an improved result, Save the separate review centerline. Check its layer and coordinates, then use Undo and confirm the original remains. The unchanged baseline will not offer Save.
8. Open the HTML and Excel review. Record any error with the AutoCAD version and selected handle.

### Repeat the small line test

Open [line-test.dxf](examples/line-test.dxf) in AutoCAD. This is a clearly synthetic test drawing for checking the plugin controls. It contains one yellow route and one white equipment box.

1. Run `MARITIMEOPTIMIZE` and select the yellow polyline as the route.
2. Select the white equipment block as the obstacle. Leave the text label unselected.
3. Enter diameter `100` and clearance `50`.
4. Enter grid step `250` and padding `500`.
5. Enter `mm` for the drawing units.

The Python check for these exact inputs reduces the 8000 mm route to 4900 mm. Both routes pass the implemented checks. Verify that the green suggestion avoids the box. Choose Save to add the review centerline. Then use Undo and confirm that the original yellow route and white box remain.

The venue screenshots confirm that this line workflow and the real cooling workflow both saved review centerlines. Undo has not been explicitly confirmed yet.

The Mac suite passes 610 tests. The updated DLL builds with zero warnings and zero errors. Eight public challenge cases and the 50.8 mm real drawing replays have also completed. These checks support the implementation but do not replace live verification of the new commands in AutoCAD.

## What still needs work

We reuse the existing search rather than training a new model. The V3 assessment uses encoded requirements and explicit model evidence. It does not infer all equipment requirements from geometry.

The importer supports some surface types. Supported components use conservative pipe envelopes and other objects retain fallback boxes. Without reviewed context, the local routing box is not a verified ship boundary. Branches, actual component identities, physical fitting shapes and installation planning need more work. Saved suggestions contain centerlines instead of complete fittings and pipe solids.

The browser version and the [saved example](../examples/real-cad-demo/report.html) remain available if the AutoCAD integration needs debugging. See [the team handoff](../docs/team-handoff.md) for the full next steps.

## Platform references

- [Autodesk AutoCAD 2027 SDK and .NET 10](https://blog.autodesk.io/autocad-2027-sdk-what-every-plugin-developer-needs-to-know/)
- [Official AutoCAD.NET 26.0.0 reference package](https://www.nuget.org/packages/AutoCAD.NET/26.0.0)
- [AutoCAD for Mac programming interfaces](https://help.autodesk.com/cloudhelp/2027/ENU/AutoCAD-MAC-Customization/files/GUID-E6429154-36DF-4D84-8ABC-9FCA15B66158.htm)
