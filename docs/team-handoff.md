# Team handoff

Updated October 17 2026. We are now making AutoCAD the main way to use the project. The browser version stays available as a fallback.

## Start here at the venue

AutoCAD 2027 is already installed on the Snapdragon laptop. Follow the [step-by-step Snapdragon guide](snapdragon-guide.md). It includes each Python command in its own copyable block and the fix for the unsigned setup script message.

Start the Python service on the same laptop as AutoCAD. Load the included `autocad-plugin/prebuilt/Maritime.AutoCAD.dll` with `NETLOAD`. Try the small test drawing before using `MARITIMECAD` on a real pipe solid or block. The included DLL lets you try it without installing a build SDK. The [plugin reference](../autocad-plugin/README.md) also covers rebuilding and command details.

Two venue milestones have passed. The team saved a suggestion from the small line test. `MARITIMECAD` then read the real cooling drawing and received a calculation result. Its connection problem was fixed. The next checkpoint is the new pipe-shape geometry update described below.

Update the Python code and DLL together using the guide. Keep the existing `.venv` and `data` folders. After restarting the service its health response must include `pipe_envelopes`. Load the fresh DLL in a new AutoCAD session.

If setup takes too long you can still open [the saved report](../examples/real-cad-demo/report.html) from a downloaded copy of this repo. It works offline without installation. The [README](../README.md#start-the-python-service) explains how to start the browser app and run new calculations.

## What this iteration adds

The previous iteration connected AutoCAD to the existing drawing reader and route search. Its real route check used one large box for each neighboring component. The latest update uses closer pipe-shaped envelopes for fully supported components. Components that cannot be represented that way keep their boxes.

The same geometry now serves end-contact detection and connection limits and full route checks. No obstacle is removed to make the result pass. New diagnostics identify the source object when an endpoint blocks the search. The script `scripts/check_real_routes.py` can replay several selected routes and save their reports.

The new flow is:

1. Select an object in AutoCAD.
2. Send a temporary DXF snapshot to the local Python service.
3. Choose a possible route found from that object.
4. Prepare a local test with nearby pipe envelopes and fallback boxes.
5. Run the existing search and review the returned route in AutoCAD.
6. Optionally keep a separate review centerline in the drawing.

This removes the manual browser upload from the AutoCAD workflow. It reuses the importer and calculation code we already have. It does not replace the original pipe solids or create a finished replacement pipe assembly.

## What we have checked

The tests below ran on the Mac against the cooling drawing. They use provisional millimetres and 50 mm clearance with 500 mm local padding. Search grid spacing and the maximum fixed connection length are both 250 mm. All listed end contacts were explicitly accepted as test assumptions.

| Test | Result |
| --- | --- |
| `DB1` selected pipe | 2413.47 mm original fails the selected clearance near `DD7`. A 2558.00 mm replacement passes the implemented checks |
| Joined `DB2` / `E6D` / `E16` route | Valid baseline stays unchanged at 3679.25 mm |
| Same joined route with an artificial 250 mm offset | 4179.25 mm becomes 3728.30 mm |
| `DC1` with the same connection limit | Preparation stops because the accepted connection cannot be exited within 250 mm |

The `DB1` check keeps 76 obstacles. Of those 43 use pipe-shaped envelopes and 33 retain boxes. Its route search expands 123 states. The replacement adds 144.53 mm because this test repairs a clearance issue. It does not demonstrate a shorter original design.

The Snapdragon has confirmed simple line Save and real drawing extraction and calculation. Saving this new real-drawing suggestion and Undo still need venue verification. See [the milestone notes](pipe-shape-milestone.md) for exact results and limits.

## The earlier saved routing result

The example joins cooling drawing components DB2 and E16 and E6D. It assumes millimeters and a 50 mm clearance. All 36 nearby obstacle boxes remain present. Four start contacts are treated as fixed connections under a local test assumption. The original end section must leave those contact boxes within a 250 mm limit.

| Input or result | Length |
| --- | ---: |
| Untouched CAD baseline | 3679.25 mm |
| Artificial detour | 4179.25 mm |
| Suggested replacement | 3728.30 mm |

The search removes about 451 mm of the added detour. Its result remains about 49 mm longer than the unchanged baseline. The search uses square corners while the source has a rounded bend. Running the baseline on its own keeps the existing route.

This shows recovery from one deliberately added problem in real drawing context. It does not show that the original design was bad or that the whole system has been optimized.

The shared example contains the route and its 36 local obstacle boxes. You do not need Efe's files or the full organizer DXF to rerun it. The screenshot in the README shows the full CAD background. The saved report shows the local routing test.

| File | What it is for |
| --- | --- |
| [report.html](../examples/real-cad-demo/report.html) | Rotate and inspect the saved result offline |
| [baseline.json](../examples/real-cad-demo/baseline.json) | Load the unchanged route into the browser app |
| [detour.json](../examples/real-cad-demo/detour.json) | Load the same route with an artificial detour |
| [result.json](../examples/real-cad-demo/result.json) | Read the saved checks and route coordinates |
| [route-centerlines.dxf](../examples/real-cad-demo/route-centerlines.dxf) | Compare the original and suggested centerlines in CAD |

Get the original drawing from [the organizer Drive folder](https://drive.google.com/drive/folders/18ESskQZ73e8eus2aQ8Cm-h57o3OF8fop) to test the new AutoCAD selection workflow.

## Next tasks in order

1. **Confirm the new geometry on the event laptop.** Update the Python files and DLL together. Run `MARITIMECAD` on `DB1`. Review the cyan original and the listed end contacts. Compare the proposed green route with the nearby `DD7` component before saving.
2. **Check a joined route and Undo.** Run the unchanged `DB2` / `E6D` / `E16` route. It should retain the original under the same assumptions. Check Undo after saving a reviewed improved result. The plugin still changes one selected route at a time.
3. **Compare a controlled test.** Run the saved baseline and detour in the browser fallback. Confirm the baseline stays unchanged and the artificial detour becomes shorter. Compare the exported DXF with the drawing at the correct scale.
4. **Keep all required connections.** Extend beyond one route while preserving branches and equipment connections. We must not shorten a pipe by disconnecting something in the middle.
5. **Improve the checks.** Include surrounding structure and other systems. Replace the assumed local boundary where actual space information is available. Check fitting sizes and full shapes.
6. **Prepare the final demonstration.** Use a reviewed real problem as the main example. Show before and after with the same rules. Explain unsupported checks and flag cases that need a designer.

We can continue with the current unit and rule assumptions. Record corrections when they become available and rerun the checks after changing them.

## What each device does

| Device | Current role |
| --- | --- |
| Mac | Develop and test the Python code. Build the Windows plugin DLL. Use the browser fallback |
| Snapdragon laptop | AutoCAD demo. Simple line Save and real drawing calculation confirmed. Latest pipe-shape update needs venue verification |
| Dragonwing IQ9 | Optional future processing target. No service or AI processor integration is included |

The program uses geometric search. It does not train a model or learn from CAD uploads. Plugging in IQ9 does not automatically speed it up. The current demo does not depend on IQ9.

## Limits to be clear about

The drawing importer supports some surface types. Unsupported geometry can leave gaps in what we check. Matching endpoints does not prove that components belong to the same piping system. Supported components use conservative pipe envelopes. Other components use sample boxes. Both can still reject usable space. The boundary is an assumed local box. Fixed contact assumptions do not confirm that the objects are real fittings.

The saved CAD output contains centerlines. It is not a complete replacement pipe assembly. Whole system optimization and official standards compliance remain unfinished. The [requirements checklist](challenge-requirements.md) maps those gaps to the challenge brief.
