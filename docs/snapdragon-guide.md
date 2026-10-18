# Run the project on the Snapdragon laptop

**Latest build:** follow the [3D proposal update and demo guide](3d-demo-snapdragon.md) after the base setup. It adds a bend-radius prompt, rounded geometry and new comparison commands. The detailed centerline walkthrough below records the earlier flow.
AutoCAD 2027 is already installed on the event laptop. The setup photo also shows Python 3.14.8 working. Use the included plugin DLL. You do not need to install the .NET SDK or Visual Studio for these steps.

AutoCAD and the Python routing service run on this same laptop. The Mac and IQ9 board do not need to be connected.

The team has saved both the small line suggestion and a real cooling-drawing suggestion on this laptop. The latest [standards and model context iteration](standards-iteration.md) adds the V3 Challenge profile, optional model context and Excel review reports. Those new features have passed Mac checks and still need this venue test.

## Update an existing working setup

This update changes the rule checks, model context, reports and plugin. Follow these steps if Python is already installed and the small test works.

1. Save any drawing work you need and close AutoCAD completely.
2. In the Python PowerShell window press **Ctrl+C**. Wait for the `PS` prompt.
3. Download the latest repo ZIP and extract it into a temporary folder.
4. Copy the new `pipe_router` and `scripts` and `autocad-plugin` folders into your existing project folder. Merge folders and replace matching files. Also copy `examples`, `web`, `tests`, `docs`, `README.md`, `requirements.txt` and `requirements-lock.txt` to keep the new checks and instructions current.
5. Keep your existing `.venv` and `data` folders and your drawing files.
6. In PowerShell open the existing project folder. For the team's Desktop setup use:

```powershell
cd "$env:USERPROFILE\Desktop\maritime-dual-use-hackathon-2026-main"
```

Check the eight public V3 examples before starting the service:

```powershell
.\.venv\Scripts\python.exe scripts\check_challenge.py
```

Expect eight `PASS` lines and a report folder. A blocked case can pass this test because rejecting that input is its expected outcome. Open a case's `report.html` or `route-review.xlsx` to see the explanation.

Start the service:

```powershell
.\.venv\Scripts\python.exe scripts\run_web.py
```

Leave that window running. Open [the health page](http://127.0.0.1:8767/api/health) on the Snapdragon and refresh it. The `capabilities` list must include **`challenge_rules_v3`**, **`model_context`** and **`review_excel`**. If they are missing you are still running older Python files.

Reopen AutoCAD and run `NETLOAD`. Choose the fresh `autocad-plugin\prebuilt\Maritime.AutoCAD.dll` from the existing project folder. Then follow section 6 to rerun `MARITIMECAD`. Review the listed end contacts before accepting them.

This update uses the existing package installation. You do not need to rebuild `.venv` or install a training dataset.

## 1. Open the project folder

Get the latest files from [the GitHub repo](https://github.com/InferniusS/maritime-dual-use-hackathon-2026). If you use **Code → Download ZIP** extract the ZIP before running anything.

Open the extracted folder in File Explorer. It should contain `README.md` and the folders `scripts` and `autocad-plugin`. Click the Explorer address bar. Type `powershell` and press Enter to open PowerShell in that folder.

If you already have PowerShell open and extracted the default ZIP folder to Desktop you can enter:

```powershell
cd "$env:USERPROFILE\Desktop\maritime-dual-use-hackathon-2026-main"
```

If your folder is elsewhere use its actual path. You are in the right place when this command lists the setup file:

```powershell
Get-Item .\requirements-lock.txt
```

## 2. Set up Python one command at a time

Run each block separately. Wait until the PowerShell prompt returns before running the next one. If a command fails stop at that step and share the error.

Use these direct commands if `setup_windows.ps1` says it is not digitally signed. They perform the setup without changing the PowerShell execution policy. You do not need to run `Activate.ps1`.

Check Python:

```powershell
python --version
```

Expect `Python 3.14.x`. The `Python 3.14.8` shown in the setup photo is the right version family.

Create the project's Python environment:

```powershell
python -m venv .venv
```

This may finish without printing anything. It creates a `.venv` folder on this laptop. Do not copy the Mac's `.venv` here.

Install the project packages:

```powershell
.\.venv\Scripts\python.exe -m pip install --only-binary=:all: -r requirements-lock.txt
```

This step needs internet access and may take a few minutes. Wait for it to finish. An optional pip upgrade notice does not mean the install failed.

Check the installed package dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip check
```

Expect `No broken requirements found.`

Run the project setup check:

```powershell
.\.venv\Scripts\python.exe scripts\check_setup.py
```

Expect a final line beginning with `PASS:`. This checks the pinned packages and a small geometry example. Continue only after it passes.

## 3. Start the local service

In the same PowerShell window run:

```powershell
.\.venv\Scripts\python.exe scripts\run_web.py
```

Expect this address in the output:

```text
Workspace: http://127.0.0.1:8767/
```

Leave this window open. The command stays running and does not return to the PowerShell prompt. That is expected. AutoCAD sends its requests to this process.

On the Snapdragon laptop open [the service check](http://127.0.0.1:8767/api/health) in a browser. It should show `maritime-pipe-router` with `api_version` set to `1` and `units` set to `mm`. The current service includes `challenge_rules_v3`, `model_context` and `review_excel` in its capabilities.

That address always means the computer where you open it. Opening it on the Mac does not check the Snapdragon service. Once you have checked it you can close the browser tab. The PowerShell service must stay running.

## 4. Load the plugin in AutoCAD 2027

These commands go into AutoCAD's command line. They do not go into PowerShell.

Open AutoCAD and then open the included `autocad-plugin\examples\line-test.dxf` through **File → Open**. It contains a yellow route and a white equipment box. This is our small synthetic test drawing.

Enter this in AutoCAD:

```text
NETLOAD
```

In the file picker choose this file from the extracted repo:

```text
autocad-plugin\prebuilt\Maritime.AutoCAD.dll
```

Use the actual DLL on disk. The GitHub page for the DLL is not a file that AutoCAD can load.

If AutoCAD asks about a trusted location use the specific `autocad-plugin\prebuilt` folder from this repo. Do not turn off AutoCAD's security checks. If Windows explicitly blocks this downloaded DLL you can inspect that file's **Properties** and use **Unblock** if it is offered. Apply this only to our downloaded plugin file.

Test that the plugin loaded:

```text
MARITIMEPREVIEW
```

Click the yellow route. Press Enter to finish selecting. A cyan overlay should appear. Press Enter again to clear it.

If the command is unknown or the DLL fails to load stop here and record the exact AutoCAD error. A successful build on the Mac does not prove that loading works on this machine.

## 5. Run the small route test

Keep the same test drawing open. Make sure the Python service is still running.

Enter this in AutoCAD:

```text
MARITIMELINEOPTIMIZE
```

Answer its prompts in this order:

| Prompt | What to do |
| --- | --- |
| Select the connected route | Click the yellow polyline then press Enter |
| Select obstacle objects | Click an edge of the white equipment block then press Enter |
| Pipe outside diameter | Enter `100` |
| Required clearance | Enter `50` |
| Search grid step | Enter `250` |
| Search-bound padding | Enter `500` |
| Drawing units | Enter `mm` |

Do not select the yellow route again when choosing the obstacle. Leave the text label unselected.

The original route is 8000 mm long. The Python check for these exact inputs produces a 4900 mm suggestion. The AutoCAD preview should show the original in cyan and the suggestion in green. Check that the green route avoids the equipment box.

The final prompt offers **Discard** or **Save**. Enter `Save` to keep the proposed route as a separate 3D centerline on `MARITIME_SUGGESTION`. Pressing Enter accepts the default Discard.

After testing Save enter:

```text
U
```

Check that the added green centerline disappears and the original route and equipment block remain. This tests one-step Undo. The plugin does not save the drawing file automatically. Use Save As if you want to keep a separate copy of a reviewed result.

## 6. Try the actual cooling drawing

Get the organizer model from [the supplied Drive folder](https://drive.google.com/drive/folders/18ESskQZ73e8eus2aQ8Cm-h57o3OF8fop). The original organizer files are not included in this repo.

Open a test copy of the Fresh Water Cooling Auxiliaries drawing in AutoCAD. Use model space. Keep the surrounding geometry available so it can be included in the checks.

Enter:

```text
MARITIMECAD
```

Select a pipe solid or a block containing supported pipe geometry. The command creates a temporary DXF snapshot and sends it to the local Python service. You do not need to export or upload the drawing by hand.

For the first cooling test use these settings:

| Prompt | Initial choice |
| --- | --- |
| Interpret drawing coordinates as | `Millimetres` |
| Pipe system | `Pressure` |
| Rule profile | `Challenge` |
| Clearance in mm | `50.8` |
| Local search padding in mm | `500` |
| Search grid step in mm | `250` |

Challenge uses the V3 brief and requires at least 50.8 mm shock clearance. Millimetres are still an interpretation of the drawing units. The padding and grid are search settings. For an exact replay of the old 50 mm test, select `Prototype` explicitly. Do not describe that old test as a V3 run.

The command lists possible routes associated with the selected object. Enter the number of the route you want to check. Joined routes appear before individual runs.

The chosen route appears in cyan before the calculation. Check that it follows the intended pipe and that its endpoints are in the right place. Press Enter to continue or Esc to cancel.

If obstacle envelopes touch its ends the command lists those contacts. The default answer to treating them as intended fixed connections is **No**. Choose Yes only after reviewing them. It preserves a bounded section at each accepted end. The initial maximum length is 250 mm. This is a cap rather than a command to preserve exactly 250 mm. It does not remove those obstacles from checks on the movable middle.

The latest Mac replay of source handle `DB1` at 50.8 mm clearance returns a 2558.00 mm checked replacement for the 2413.47 mm original. The longer suggestion addresses the modeled clearance issue under the implemented checks. Its local test keeps 43 pipe-shaped obstacles and 33 fallback boxes. The previous 50 mm run was saved successfully on the Snapdragon as object `26B9`. This update needs a new live run with the Challenge profile.

Our saved cooling example uses source handle `DB2` and a joined route containing `DB2` and `E6D` and `E16`. Its inferred diameter is 88.9 mm and its original length is about 3679.25 mm. You can inspect a selected object's handle with AutoCAD's `LIST` command. Handles can differ if the source drawing has changed.

That example has four start contacts treated as fixed connections within 250 mm. With the same source and assumptions the expected result is **unchanged** at about 3679.25 mm. Keeping an already better route is a valid result. It will not offer Save.

If the search finds an improvement it shows the suggestion in green and offers Save or Discard. Saved output is a review centerline. It is not a complete new pipe assembly with fittings.

Other outcomes need review:

- **No checked replacement found** means this search did not find one under the selected settings. It does not prove that no possible route exists.
- **No supported pipe route found** means the selection could not be read as a supported pipe run. Try a more specific pipe object.
- A block with more than 50 matching candidates needs a narrower selection.
- The current snapshot limit is 48 MB. If it is exceeded use a smaller working drawing that still includes the relevant surrounding geometry.

The command prints local links to result JSON, a centerline DXF, an HTML report and an Excel review workbook. The report now has a Challenge rule review section. Read its failures and `needs_review` items. The workbook contains rule findings and coordinates. It is not yet a manufactured parts list. Copy those addresses into the browser on the same laptop. Generated files are also saved under `data\generated\workspace` in the repo folder. Keep the reports when sharing a result or reporting an issue.

### Include reviewed surroundings and engineering evidence

A test without supplemental context is allowed, but the report must flag missing ship structure, access spaces and part evidence. Loading a context file does not automatically establish full coverage.

For a prepared and reviewed context JSON, activate the intended drawing and enter:

```text
MARITIMECONTEXT
```

Choose `Load` and select that JSON file. Run the command with `Status` to confirm that a file is loaded. Use `Clear` to remove it. Then run `MARITIMECAD` as above.

The file belongs to the drawing fingerprint for this AutoCAD session. Reload it after restarting AutoCAD. It can be a raw context object or a wrapper with `model_context` and optional `engineering`. The service validates the contents when preparing a selected route. A loaded file can still be rejected if its geometry or evidence is invalid.

The [context preparation steps](standards-iteration.md#add-reviewed-surroundings) explain the template and `scripts/build_model_context.py`. Context preparation requires verified units and alignment. The tool does not automatically align separate drawings or invent missing part identities.

## 7. Start again later

You only need the package installation once for this project folder. After restarting the laptop open PowerShell in that same folder and run:

```powershell
.\.venv\Scripts\python.exe scripts\run_web.py
```

Then open AutoCAD. Load the DLL with `NETLOAD` again for a new AutoCAD session. Reload any reviewed context with `MARITIMECONTEXT` before running `MARITIMECAD`.

To stop the Python service press Ctrl+C in its PowerShell window. Closing the browser does not stop it. Closing the PowerShell window does stop it.

When the team publishes a new DLL close AutoCAD after saving any work you need. Get the updated repo files. Restart the Python service and reopen AutoCAD before loading the new DLL. AutoCAD may retain a loaded assembly until it exits.

For a downloaded ZIP use [the update steps above](#update-an-existing-working-setup) to merge files into the existing project while keeping the environment and reports. If you cloned the repo use your normal Git update workflow instead.

## 8. If a step fails

### A connection error while the health page still works

An earlier venue run reported `ResponseEnded` before `/api/run` reached Python. The connection update fixed that path on the Snapdragon. The request now reaches the service and returns a result. The plugin uses a fresh connection for each request and the server closes completed responses explicitly.

If this returns after an update keep both the full AutoCAD error and the latest PowerShell lines. Check the health page. Use the same repo version for the Python files and DLL. Restart both through [the update steps](#update-an-existing-working-setup).

The current update is larger than the earlier connection patch. Replacing only `run_web.py` leaves out the new rule checks and model context.

### A calculation returns no accepted route

A completed request is separate from a valid route. The earlier `DB1` result was stopped by a large obstacle box near its fixed start section. The new Mac replay uses pipe envelopes and finds a checked replacement under the same provisional settings. See [the milestone results](pipe-shape-milestone.md).

Other selections can still fail. `DC1` exceeds the 250 mm connection cap during preparation. Read the source handle and reason in the report before changing settings. A no-route result does not establish that the source design is wrong or that no route exists.

| What you see | What to do next |
| --- | --- |
| `setup_windows.ps1` is not digitally signed | Use the separate Python commands in section 2. Do not rerun the blocked script |
| `python` is not found | Confirm Python 3.14 is installed and reopen PowerShell. The photographed event laptop already found Python 3.14.8 |
| A file or folder cannot be found | Return to the extracted repo root containing `requirements-lock.txt` and `scripts` |
| `No matching distribution found` during installation | Keep the package pins unchanged. Share that output with the Python version and processor architecture |
| `SETUP CHECK FAILED` | Stop before starting the service and share the full check output |
| The health page cannot connect | Check that the service is running on this same laptop and printed port 8767 |
| Port 8767 is already in use | Check the health page. If it is our current service reuse it. Otherwise stop only the old service you recognize before restarting. The plugin expects port 8767 |
| `NETLOAD` fails or commands are unknown | Record the full AutoCAD message. Confirm you selected the included DLL from the extracted folder |
| AutoCAD cannot reach the local optimizer | Check the health page and keep the Python PowerShell window open |
| The service reports an incompatible version | Use plugin and Python files from the same repo update. Restart both programs |
| Selecting an obstacle reports zero thickness | Select the whole white equipment block in the small test. Individual edges and flat objects do not provide a 3D obstacle box |
| No route is found near a connection | Review the listed endpoint contacts and settings. Do not remove obstacles just to get a passing result |

For an installation error these two commands help identify the environment. Run them separately in PowerShell:

```powershell
python --version
```

```powershell
python -c "print(__import__('platform').machine())"
```

For an AutoCAD issue share the command you ran and the full message from its command history. Include the drawing name and the selected object's handle if available.

## 9. Browser fallback

While the Python service is running open [the browser workspace](http://127.0.0.1:8767/) on the Snapdragon laptop.

In **Check a route** use **Open a layout file** to open `examples\real-cad-demo\baseline.json`. Run the check. Then load `detour.json` from the same folder and compare the result.

For a saved view without Python open `examples\real-cad-demo\report.html` directly from the extracted folder. It works offline.

The Mac remains our development machine. The Snapdragon laptop runs the demo. IQ9 is not part of this workflow and no model training is required.

## Navisworks

Keep AutoCAD for this iteration. Navisworks is optional for reviewing a combined model. Manage adds clash detection, but it does not implement our missing routing or manufacturing checks. See [the Navisworks assessment](standards-iteration.md#navisworks-decision). No Navisworks installation on Snapdragon has been verified.
