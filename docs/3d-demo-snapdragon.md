# Test the 3D pipe proposal on the Snapdragon

The current build adds coordinated selected sections with `MARITIMEOPTIMIZE` and report recovery with `MARITIMEREPORT`. Use the [new coordinated test guide](coordinated-snapdragon-test.md) for that workflow. The steps below retain the existing single-section and synthetic demo. Rounded meshes and geometric schedules remain review output, not approved catalog fittings.

The quickest visible demonstration is `MARITIMEDEMO` with the congested-room example below. It is an explicitly synthetic test, separate from the real cooling-drawing test. The original room fixture deliberately includes an unnecessary detour.

## 1. Update the existing installation

1. Save your drawing work, then close AutoCAD completely. A loaded DLL stays loaded until AutoCAD exits.
2. In the PowerShell window running Python, press **Ctrl+C** once. Wait for the `PS` prompt.
3. Download the latest ZIP from [the repository](https://github.com/InferniusS/maritime-dual-use-hackathon-2026) and extract it to a temporary folder.
4. Copy these updated folders into the existing project folder: `autocad-plugin`, `pipe_router`, `scripts`, `examples`, `web`, `tests`, and `docs`. Merge folders and replace matching files. Also copy `README.md`, `requirements.txt`, and `requirements-lock.txt`.
5. Keep the existing `.venv`, `data`, and drawing files. Do not copy a Mac Python environment to Windows.

Open PowerShell in the existing project folder. For the team's Desktop location, run this one line:

```powershell
cd "$env:USERPROFILE\Desktop\maritime-dual-use-hackathon-2026-main"
```

Check that the new runner is present:

```powershell
Get-Item .\scripts\check_assembly.py
```

Run the five controlled examples:

```powershell
.\.venv\Scripts\python.exe scripts\check_assembly.py
```

Expect five `PASS` outcomes and a report folder under `data\generated\assembly-checks`. The blocked example passes by refusing an invalid route. These checks run on the laptop; no CAD license or training dataset is needed for them.

If `check_assembly.py` is missing, the new files were copied into the wrong folder or the ZIP is older. Check the folder location before continuing.

## 2. Start Python and verify the version

Start the service:

```powershell
.\.venv\Scripts\python.exe scripts\run_web.py
```

Leave this PowerShell window open and running throughout the AutoCAD test. Not being able to type a new command in that window is expected. Open another PowerShell tab for other commands; press Ctrl+C here only when intentionally stopping the service.

Open [the local health page](http://127.0.0.1:8767/api/health) on the **Snapdragon**, then refresh it. Its capabilities must include:

```text
rounded_pipe_proposal
drawing_review
coordinated_sections
```

If any is absent, stop the Python process, confirm that `pipe_router` was updated in this same folder, and restart it. Opening this address on the Mac checks the Mac service, not the Snapdragon.

## 3. Load the new DLL

Reopen AutoCAD 2027. Enter this in the **AutoCAD command line**, not PowerShell:

```text
NETLOAD
```

Select this file from the updated existing project folder:

```text
autocad-plugin\prebuilt\Maritime.AutoCAD.dll
```

If Windows explicitly blocks this downloaded file, inspect that DLL's Properties and use **Unblock** if offered. Use the specific plugin folder as a trusted location if AutoCAD asks. Keep AutoCAD's security checks enabled.

The included DLL is already built. The .NET SDK and the optional build steps at the end are unnecessary for this normal installation.

## 4. Run the first visible 3D comparison

1. Create a **new empty drawing** in AutoCAD. Use the **Model** tab. Do not run this demo in the cooling drawing.
2. Enter:

```text
MARITIMEDEMO
```

3. In the file picker, open:

```text
examples\assembly\01-congested-room.json
```

4. Wait for the local calculation. The view changes to a 3D view. Grey envelopes represent the obstacles, cyan is the original reference, and green is the proposed pipe envelope.
5. At the comparison prompt, enter these choices one at a time to inspect the difference:

```text
Original
```

```text
Proposal
```

```text
Both
```

6. To keep the comparison, enter:

```text
Save
```

   `Discard` or Escape clears the temporary preview instead. `Save` keeps a review model in this drawing; it does not save the drawing file to disk.
7. Use **Save As** to save a separate demo drawing. The saved drawing uses millimetres.

The proposal uses the same 168.3 mm outside diameter and fixed endpoints as the original fixture. Its 304.8 mm bend radius is an explicit geometric assumption, not a verified manufacturer's fitting.

In the verified Mac run, this synthetic example changed from approximately **17.0767 m and four rounded bends** to **13.4199 m and two rounded bends**, with the ports unchanged. This demonstrates the controlled example. It is not a measured improvement to the organizer's cooling drawing or a guarantee for unseen test files. The search is bounded, so the laptop may report a different valid outcome if its search limit is reached.

## 5. Switch the saved comparison

After saving the 3D comparison, enter:

```text
MARITIMEVIEW
```

Choose `Original`, `Proposal`, or `Both`. This changes the review layers only:

| Layer | Contents |
| --- | --- |
| `MARITIME_PIPE_PROPOSAL` | Green rounded proposal meshes |
| `MARITIME_PIPE_REFERENCE` | Cyan original reference meshes |
| `MARITIME_SUGGESTION` | Rounded proposal centerline |
| `MARITIME_DEMO_CONTEXT` | Synthetic obstacle envelopes and test label |

Source CAD geometry remains unchanged and visible when this feature is used in a real drawing. The Original/Proposal toggle does not hide the source system or modify its layers.

Use normal AutoCAD orbit and zoom after saving to inspect the result. Use `Both` before saving another comparison so its review layers are visible. One `UNDO` immediately after saving removes that saved comparison as one drawing operation.

## 6. Try the other scenarios

Use a **new empty drawing for each case**, run `MARITIMEDEMO`, then select one of these files:

| File under `examples\assembly` | What it tests |
| --- | --- |
| `01-congested-room.json` | Removes a deliberate detour among several obstacles |
| `02-collision-repair.json` | Repairs an original route that crosses equipment |
| `03-blocked-route.json` | Rejects a blocked case instead of inventing a safe proposal |
| `04-elevation-repair.json` | Checks a route involving an elevation change |
| `05-already-good.json` | Retains an already good route when the search finds no better one |

A collision repair may be longer than the unsafe original. That is a repair, not a pipe-length saving. An unchanged result can legitimately look identical. A blocked result has no accepted green proposal to save.

The report links are printed in AutoCAD's command history. Press **F2** to read the history, then open the HTML report or Excel link on this laptop. The reports distinguish sharp design-line length from rounded pipe length and list the remaining review requirements.

## 7. Retest the real cooling drawing

Open a separate test copy of the cooling drawing, keep Python running, and enter:

```text
MARITIMECAD
```

Follow the established workflow: select the intended pipe solid, confirm the source units, choose **Pressure**, then **Challenge**. Use **50.8 mm** clearance, **500 mm** search padding, and **250 mm** grid step for the repeat test.

Select the intended route number. Inspect its start, end and included components. Confirm listed end contacts only when they are intended connections; **250 mm** is the maximum preserved end-section length for the previous repeat test.

There is now one more prompt:

```text
Proposed bend centerline radius in mm (review assumption)
```

The default is the larger of **304.8 mm** and **1.5 × outside diameter**. For the 168.3 mm route this is **304.8 mm**. Press Enter to use that declared assumption, or enter a supported radius supplied by the designer.

If a rounded proposal passes the implemented checks, choose **3D** at the review-output prompt. Inspect Original/Proposal/Both, then Save. If rounding cannot fit the fixed ends or surrounding envelopes, the program rejects the physical proposal and explains why. A previously accepted green centerline is not proof that rounded fittings will fit.

This is still one selected non-branching route at a time. It does not automatically redesign the entire cooling network. Missing surroundings and engineering evidence remain unverified; use the report to distinguish those gaps from actual passes.

## 8. Use supplied native centerlines

If a mentor supplies a poor route as connected `LINE` objects or an open straight polyline, use:

```text
MARITIMELINEOPTIMIZE
```

1. Select the connected route entities and press Enter.
2. Select every surrounding obstacle this check must avoid and press Enter. Obstacle objects need actual 3D thickness; the command uses their bounding boxes.
3. Enter outside diameter, clearance, grid step and search padding in the drawing's units.
4. Enter `mm`, `m`, or `in` for those drawing coordinates.
5. At **Create a rounded 3D pressure-pipe proposal**, choose **Yes**.
6. Enter the bend radius in **millimetres**; the prompt's default is calculated from the declared diameter.
7. Review and save the resulting 3D comparison if accepted.

This command is for pressure pipes and uses the entered prototype rules. It does not apply the complete V3 rule profile, infer missing obstacles, accept branched paths, or infer connections through equipment. Fixed endpoints are preserved. Source centerlines remain unchanged. Curved polyline segments are rejected rather than silently converted into guessed fittings.

## Optional: build the DLL locally

Use this only if deliberately rebuilding from source. The normal venue procedure uses the included DLL. Building requires the .NET 10 SDK; AutoCAD 2027 must remain closed until the build finishes.

Check the SDK:

```powershell
dotnet --version
```

From the project folder, build against the installed AutoCAD managed assemblies:

```powershell
dotnet build .\autocad-plugin\Maritime.AutoCAD.csproj -c Release -p:DebugType=None -p:AutoCADManagedDir="C:\Program Files\Autodesk\AutoCAD 2027"
```

If AutoCAD is installed elsewhere, use its actual installation folder containing `AcMgd.dll`, `AcDbMgd.dll`, and `AcCoreMgd.dll`. Expect zero errors. Reopen AutoCAD and `NETLOAD` this freshly built file:

```text
autocad-plugin\bin\Release\net10.0-windows\Maritime.AutoCAD.dll
```

Do not try to overwrite or reload an already loaded plugin DLL in the same AutoCAD session.

## What the test proves

A successful live run proves that this laptop can run the local optimizer and display/save its checked rounded review geometry inside AutoCAD. The controlled examples test several outcomes, including refusal and no change. It does not prove all unseen ship layouts are supported, certify engineering compliance, or supply a purchased-parts catalog. Those limits remain in the report and should remain clear in the pitch.
