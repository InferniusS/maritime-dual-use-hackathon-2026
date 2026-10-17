# Team handoff

Updated October 17 2026. We are now making AutoCAD the main way to use the project. The browser version stays available as a fallback.

## Start here at the venue

AutoCAD 2027 is already installed on the Snapdragon laptop. Follow the [step-by-step Snapdragon guide](snapdragon-guide.md). It includes each Python command in its own copyable block and the fix for the unsigned setup script message.

Start the Python service on the same laptop as AutoCAD. Load the included `autocad-plugin/prebuilt/Maritime.AutoCAD.dll` with `NETLOAD`. Try the small test drawing before using `MARITIMECAD` on a real pipe solid or block. The included DLL lets you try it without installing a build SDK. The [plugin reference](../autocad-plugin/README.md) also covers rebuilding and command details.

The first venue milestone is a successful run inside AutoCAD. The code and service can be built and tested on the Mac. That does not prove the DLL loads on the Snapdragon laptop or that AutoCAD exports every needed object correctly.

If setup takes too long you can still open [the saved report](../examples/real-cad-demo/report.html) from a downloaded copy of this repo. It works offline without installation. The [README](../README.md#start-the-python-service) explains how to start the browser app and run new calculations.

## What this iteration adds

The previous iteration could display a drawing in the browser and join compatible component ends into a longer route. It could preserve selected end sections and repair an artificial detour.

The teammates then added an AutoCAD plugin with commands for previewing line geometry and sending a selected line route to the Python search. We have extended that path so a test can start from a pipe solid or block in the drawing.

The new flow is:

1. Select an object in AutoCAD.
2. Send a temporary DXF snapshot to the local Python service.
3. Choose a possible route found from that object.
4. Prepare a local test with nearby obstacle boxes and the selected rule settings.
5. Run the existing search and review the returned route in AutoCAD.
6. Optionally keep a separate review centerline in the drawing.

This removes the manual browser upload from the AutoCAD workflow. It reuses the importer and calculation code we already have. It does not replace the original pipe solids or create a finished replacement pipe assembly.

## What we have checked

The Python code passed 373 automated tests on the Mac. That includes 26 new tests for the AutoCAD service connection.

The new snapshot path also read the 26 MB cooling DXF. It extracted 21003 objects and found 40 joined candidate routes. Selecting source handle `DB2` found the same joined route used by our saved example. Running that unchanged route kept its original length of about 3679.25 mm.

A full AutoCAD command run is still unverified. Loading the DLL and checking its overlays and saved centerline must happen at the venue.

## The saved routing result

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

1. **Load the plugin on the event laptop.** Confirm AutoCAD 2027 for Windows opens the DLL through `NETLOAD`. Check that the Python service is running locally. This also checks the actual laptop and plugin compatibility.
2. **Test the drawing workflow.** Select a supported cooling pipe object. Review the detected route and the unit assumption. Run the baseline and confirm that the overlay matches the source drawing. For a result reported as improved try saving a review centerline and undoing it. An unchanged result does not offer Save.
3. **Compare a controlled test.** Run the saved baseline and detour in the browser fallback. Confirm the baseline stays unchanged and the artificial detour becomes shorter. Compare the exported DXF with the drawing at the correct scale.
4. **Keep all required connections.** Extend beyond one route while preserving branches and equipment connections. We must not shorten a pipe by disconnecting something in the middle.
5. **Improve the checks.** Include surrounding structure and other systems. Replace the assumed local boundary where actual space information is available. Check fitting sizes and full shapes.
6. **Prepare the final demonstration.** Use a reviewed real problem as the main example. Show before and after with the same rules. Explain unsupported checks and flag cases that need a designer.

We can continue with the current unit and rule assumptions. Record corrections when they become available and rerun the checks after changing them.

## What each device does

| Device | Current role |
| --- | --- |
| Mac | Develop and test the Python code. Build the Windows plugin DLL. Use the browser fallback |
| Snapdragon laptop | Run AutoCAD and the local Python service for the demo. Actual plugin execution still needs verification |
| Dragonwing IQ9 | Optional future processing target. No service or AI processor integration is included |

The program uses geometric search. It does not train a model or learn from CAD uploads. Plugging in IQ9 does not automatically speed it up. The current demo does not depend on IQ9.

## Limits to be clear about

The drawing importer supports some surface types. Unsupported geometry can leave gaps in what we check. Matching endpoints does not prove that components belong to the same piping system. The current route test uses obstacle boxes and an assumed local boundary. The fixed contact assumptions do not confirm that the objects are real fittings.

The saved CAD output contains centerlines. It is not a complete replacement pipe assembly. Whole system optimization and official standards compliance remain unfinished. The [requirements checklist](challenge-requirements.md) maps those gaps to the challenge brief.
