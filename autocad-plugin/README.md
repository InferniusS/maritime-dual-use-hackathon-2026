# AutoCAD plugin

This plugin brings the existing Python pipe router into AutoCAD. AutoCAD handles selection and review. A local Python service reads the drawing snapshot and searches for a route.

The website does not need to be open. Both the plugin and browser app use the same calculation code.

## Requirements

- AutoCAD 2027 for Windows with managed .NET plugin support
- Standard Python 3.14.x for the local service
- The .NET 10 SDK only if you want to rebuild the plugin

The DLL targets an x64 AutoCAD process. The actual AutoCAD installation on the Snapdragon laptop still needs a `NETLOAD` test. Building successfully on the Mac does not confirm that the plugin runs on that laptop.

AutoCAD for Mac cannot load this managed .NET plugin. The Mac can run the Python service and browser tests. It can also compile the Windows DLL using the official Autodesk reference package.

## Start the local service

From a PowerShell window in the repo folder:

```powershell
.\scripts\setup_windows.ps1
.\.venv\Scripts\python.exe scripts\run_web.py
```

Keep this window running. The plugin connects to `http://127.0.0.1:8767` on the same computer. It does not connect to Efe's Mac or a hosted website.

You can check the service at [http://127.0.0.1:8767/api/health](http://127.0.0.1:8767/api/health). It should identify itself as `maritime-pipe-router` with API version 1. The [main setup guide](../README.md#start-the-python-service) has alternatives if the PowerShell setup script is blocked.

## Use the included DLL

The repo includes [Maritime.AutoCAD.dll](prebuilt/Maritime.AutoCAD.dll) in `autocad-plugin/prebuilt`. Use this for the first venue check. You do not need the .NET SDK to load an already built plugin. AutoCAD 2027 provides the runtime.

[build-info.json](prebuilt/build-info.json) records the source hashes for that build. The package contains our DLL and does not bundle Autodesk runtime DLLs. AutoCAD execution remains unverified until the venue test.

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
| `MARITIMECAD` | Read a pipe solid or block from the current drawing and test a detected route |
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
| Clearance | 50 mm |
| Local search padding | 500 mm |
| Search grid step | 250 mm |
| Gravity flow direction | Keep the inferred start and end |
| Minimum gravity slope | 0.01 fall per horizontal distance |

These values are prototype assumptions. They are editable in the command. The gravity options appear only when you choose Gravity.

After you choose a candidate the command shows its original route in cyan. Check the endpoints and included components. The service prepares nearby obstacle boxes and keeps the route endpoints fixed.

If obstacle boxes touch the ends the command lists those contacts. It asks whether to treat all listed contacts as intended fixed connections. The default is No. Choosing Yes preserves bounded original end sections. The initial limit is 250 mm and the maximum is 1000 mm. This assumption does not allow the movable middle to pass through an obstacle.

The final preview shows the original route in cyan and the suggestion in green. The command prints lengths and check findings with links to local result files. If a route improves under the implemented checks you can choose **Save**. The default is **Discard**. Unchanged results and failed searches do not offer a replacement to save.

Save adds a 3D review polyline on the `MARITIME_SUGGESTION` layer with source information. It keeps the original pipe solids and can be undone. A review centerline is a proposed path for a designer to inspect. It is not a new pipe assembly with validated fittings.

The command handles one selected route at a time. It does not optimize the complete ship drawing in one step. Select objects directly in model space. For external references open the referenced drawing itself. Snapshot uploads are limited to 48 MB. An unsupported object or an overly broad block selection may not produce a usable candidate.

### Simple line workflow

Use `MARITIMEOPTIMIZE` when the intended route already exists as straight line or polyline centerlines.

Select one connected route and the obstacles to check. Enter the outside diameter and the clearance. Then choose the search settings and drawing units. The service returns the same comparison preview used by the real drawing command. Improved results also offer Save or Discard.

This path uses selected obstacle extents as boxes. It checks the geometry you selected. It does not infer other objects or required connections from the whole drawing.

### Preview only

`MARITIMEPREVIEW` shows selected line geometry in cyan. It is a quick way to check that the plugin loads and draws an overlay. It does not run a route search. Press Enter to clear the temporary graphics.

## First venue checks

1. Confirm the DLL loads and all three commands are recognized.
2. Confirm the local health address responds.
3. Try `MARITIMECAD` on a supported pipe object in the cooling drawing. Source handle `DB2` was used in the Mac snapshot test and matched the saved joined route.
4. Check that the selected route appears at the correct position and scale. Millimeters are the current prototype assumption.
5. To reproduce our saved baseline choose the joined route containing DB2 and E6D and E16. Use millimeters with Pressure and the default 50 mm clearance. Our local test treats the four listed start contacts as fixed connections with a 250 mm limit. Review those contacts before applying the same assumption. The expected saved example result is unchanged at about 3679.25 mm. A finished route does not need to become shorter.
6. On a result reported as improved try keeping a review centerline. Check its layer and coordinates. Use Undo to remove it. The unchanged baseline will not offer Save.
7. Record any command errors along with the AutoCAD version and selected object handle.

### A small test that should improve

Open [line-test.dxf](examples/line-test.dxf) in AutoCAD. This is a clearly synthetic test drawing for checking the plugin controls. It contains one yellow route and one white equipment box.

1. Run `MARITIMEOPTIMIZE` and select the yellow polyline as the route.
2. Select the white equipment block as the obstacle. Leave the text label unselected.
3. Enter diameter `100` and clearance `50`.
4. Enter grid step `250` and padding `500`.
5. Enter `mm` for the drawing units.

The Python check for these exact inputs reduces the 8000 mm route to 4900 mm. Both routes pass the implemented checks. Verify that the green suggestion avoids the box. Choose Save to add the review centerline. Then use Undo and confirm that the original yellow route and white box remain.

This example tests the line command and saved review geometry. Use the cooling drawing to test solid extraction through `MARITIMECAD`.

The Python service passed its automated tests on the Mac. The real cooling snapshot also passed through the new inspection path over HTTP. The baseline stayed unchanged at 3679.25 mm. A controlled detour of 4179.25 mm was reduced to 3728.30 mm. That remains longer than the untouched baseline. Full AutoCAD execution and the review save still need verification on the event laptop.

## What still needs work

We reuse the existing search rather than training a new model. Adding the plugin does not add knowledge of pipe standards or equipment requirements.

The importer supports some surface types. Obstacles are approximate boxes. The local routing boundary and engineering values are provisional assumptions. Branches and required equipment need more work before whole system optimization. Saved suggestions contain centerlines instead of complete fittings and pipe solids.

The browser version and the [saved example](../examples/real-cad-demo/report.html) remain available if the AutoCAD integration needs debugging. See [the team handoff](../docs/team-handoff.md) for the full next steps.

## Platform references

- [Autodesk AutoCAD 2027 SDK and .NET 10](https://blog.autodesk.io/autocad-2027-sdk-what-every-plugin-developer-needs-to-know/)
- [Official AutoCAD.NET 26.0.0 reference package](https://www.nuget.org/packages/AutoCAD.NET/26.0.0)
- [AutoCAD for Mac programming interfaces](https://help.autodesk.com/cloudhelp/2027/ENU/AutoCAD-MAC-Customization/files/GUID-E6429154-36DF-4D84-8ABC-9FCA15B66158.htm)
