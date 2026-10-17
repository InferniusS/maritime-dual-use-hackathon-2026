# Team handoff

Updated October 17 2026. Start here to catch up on the current version and continue work at the venue.

## Start here

Download or clone the repo. Open [the saved report](../examples/real-cad-demo/report.html) from the downloaded folder to explore the current result without installing anything. The [README](../README.md#run-the-app) has Mac and Windows steps for running new calculations.

The shared example contains the selected route and 36 local obstacle boxes. You do not need Efe's files or the full organizer DXF to rerun it. The screenshot in the README shows the full CAD background. The saved report shows the local routing test. Get the original drawing from [the organizer Drive folder](https://drive.google.com/drive/folders/18ESskQZ73e8eus2aQ8Cm-h57o3OF8fop) if you want to load the whole model.

| File | What it is for |
| --- | --- |
| [report.html](../examples/real-cad-demo/report.html) | Rotate and inspect the saved result offline |
| [baseline.json](../examples/real-cad-demo/baseline.json) | Load the unchanged route into the app |
| [detour.json](../examples/real-cad-demo/detour.json) | Load the same route with an artificial detour |
| [result.json](../examples/real-cad-demo/result.json) | Read the saved checks and route coordinates |
| [route-centerlines.dxf](../examples/real-cad-demo/route-centerlines.dxf) | Compare the original and suggested centerlines in CAD |

## What improved in the last iteration

Before this we could view a real drawing and test an individual extracted component. Now the importer can join compatible component ends into longer candidate routes. It found 40 open chains in the cooling drawing. These are geometry matches that still need review as actual pipe connections.

The routing test can preserve the original sections at the ends of a route. Only those fixed sections receive limited contact exemptions for explicitly listed nearby objects. The movable middle still has to avoid all obstacle boxes.

We also added a controlled detour test. It starts from a route that passes the implemented checks and inserts a deliberately longer section. The same obstacles stay in place. A restore button returns to the unchanged baseline.

## The result we have actually checked

The current example joins cooling drawing components DB2 and E16 and E6D. It uses millimeters as an assumption with a 50 mm clearance. All 36 nearby obstacle boxes remain present. Four start contacts are treated as fixed connections under a local test assumption. The original end section must leave those contact boxes within a 250 mm limit.

| Input or result | Length |
| --- | ---: |
| Untouched CAD baseline | 3679.25 mm |
| Artificial detour | 4179.25 mm |
| Suggested replacement | 3728.30 mm |

The search removes about 451 mm of the added detour. Its result remains about 49 mm longer than the unchanged baseline. The search uses square corners while the source has a rounded bend. Running the baseline on its own keeps the existing route.

This proves that our current search can recover from one deliberately added problem in real drawing context. It does not prove that the original design was bad or that the whole system has been optimized.

The current version passed 310 automated tests on the Mac. We also checked the real drawing workflow and baseline restore in the browser. The tests cover the behavior we implemented. They do not certify the piping design.

## Next tasks in order

1. **Get the demo running at the venue.** Set up the Snapdragon laptop and run both saved inputs. Confirm that the baseline stays unchanged and the artificial detour becomes shorter. Open the exported DXF in AutoCAD or Navisworks and compare the coordinates and scale.
2. **Keep all required connections.** Extend beyond one route while preserving branches and equipment connections. We must not shorten a pipe by disconnecting something in the middle.
3. **Check more of the actual space.** Include surrounding structure and other systems. Replace the assumed local routing box and improve the obstacle shapes where simple boxes are too rough.
4. **Make the changes usable in CAD.** Check real fitting sizes and their full shapes. Provide enough component information for a designer to implement the change.
5. **Build the final demonstration.** Use a reviewed real problem as the main example. Show before and after with the same rules. Explain unsupported checks and flag cases that need a designer.

We can continue building with the current unit and rule assumptions. Record any corrections when they become available and rerun the checks after changing them.

## What each device does

| Device | Current role |
| --- | --- |
| Mac | Development and testing. The search runs on its CPU |
| Snapdragon laptop | Main demo target. Actual execution still needs verification |
| Dragonwing IQ9 | Optional future processing target. No service or AI processor integration yet |

The program uses geometric search. It does not train a model and does not learn from CAD uploads. Plugging in IQ9 does not automatically speed it up.

## Limits to be clear about

The drawing preview is partial. Some surface types are unsupported. Matching endpoints does not prove that components belong to the same piping system. The current example uses obstacle boxes and an assumed local boundary. The fixed contact assumptions do not confirm that the objects are real fittings.

The export contains centerlines rather than a complete replacement pipe assembly. Whole system optimization and official standards compliance remain unfinished. The [requirements checklist](challenge-requirements.md) maps these gaps to the challenge brief.
