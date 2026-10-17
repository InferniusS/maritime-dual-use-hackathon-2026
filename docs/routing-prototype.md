# Local piping prototype

This prototype calculates a route for one pipe at a time. It preserves fixed endpoint positions and the supplied outside diameter. Pressure pipes and gravity drains use different searches. A separate CAD preview can display supported geometry from real drawings and identify possible pipe axes for review.

The synthetic routing examples do not establish that an entire supplied ship system can be optimized. CAD candidate extraction is not automatic network reconstruction.

## Start the editable workspace

From the project folder on the prepared Mac:

```sh
.venv/bin/python scripts/run_web.py
```

On the prepared Windows laptop:

```powershell
.venv\Scripts\python scripts\run_web.py
```

Open [the workspace](http://127.0.0.1:8767/) and keep the server running. The browser shows the controls and results. The local Python process performs the calculations.

Add local DXF choices with repeated options:

```sh
.venv/bin/python scripts/run_web.py \
  --cad-sample '/path/to/scupper.dxf' \
  --cad-sample '/path/to/cooling.dxf'
```

The **Check a route** view has eight synthetic layouts. It accepts a layout JSON file and allows edits to the coordinates. The controls change the rule profile and selected dimensions. Click **Check and improve route** to calculate again. Editing an input clears the previous displayed result so it cannot be mistaken for a result from the new settings.

The **Explore real CAD** view reads a DXF from the local computer. It displays supported surfaces and boundary curves. Candidate axes retain source references and need review. Large drawings use reduced surface detail. The exported CAD inspection retains all extracted objects.

Choose a joined chain or single run and click **Use this run in tester** to create a provisional layout. The server uses the saved full inspection rather than the reduced display preview. Each prepared layout records the drawing snapshot id and selected candidate id. It also records how surrounding objects became obstacles.

## What routing checks cover

- Fixed start and end connections
- Preserved outside diameter
- Continuous segment collisions against supplied obstacle boxes
- The permitted rectangular routing boundary
- Pipe radius and the selected clearance at obstacles and boundaries
- Downhill direction and the selected minimum slope for gravity flow
- Available tangent lengths when an optional bend radius is supplied

The obstacle check covers each entire segment. It does not only check the endpoints. It expands the obstacle boxes by pipe radius plus clearance. This is conservative near box corners. A route rejected by this approximation may fit with a more detailed geometry check.

The input diameter is an outside diameter. The input must account for insulation if it is part of the required collision envelope. The displayed lines show centerlines even though the calculation reserves space for thickness.

An optional bend radius only checks whether adjacent straight legs have sufficient tangent length. It does not check curved fitting volumes against obstacles. The small manufacturer catalog is a reference. The solver does not select or approve those products.

## Pressure route search

Pressure routing searches a three dimensional grid. It can move along the x or y or z axis. A route is scored by its physical length plus a configurable penalty for each direction change.

The result is limited to this finite search space. It is not a guarantee of the best continuous route. A valid original route is retained if the candidate does not improve its score. An invalid original with no accepted replacement produces a no-route result. Very large searches stop with an explicit limit error.

The pressure search does not search again to solve an optional bend-radius failure. Another route with sufficient fitting space may exist even when the current result has no accepted candidate.

## Gravity route search

Gravity flow runs from the supplied start point to the end point. Positive z is assumed to point upward. Each segment must meet the selected minimum fall per horizontal distance. An uphill section is rejected even if the route has an overall fall.

The search considers a bounded collection of horizontal paths with up to three turns. Height decreases uniformly with accumulated horizontal travel. Every generated candidate is checked in three dimensions against the actual obstacle height. An overhead obstacle does not block a lower pipe merely because they overlap when viewed from above.

This search can miss routes with variable slopes or more turns. It can report insufficient endpoint fall when the available fall is less than the selected slope requires even for the shortest horizontal distance. A no-route result from the bounded search is not proof that no physical design exists.

Slope checks do not establish drain sizing or venting. The actual flow direction and coordinate orientation remain essential inputs.

## Units and provisional settings

The workflow accepts `mm` or `m` or `in` as input units. All result geometry and lengths are normalized to millimeters. Missing units are explicitly recorded as a provisional millimeter assumption. Unknown unit names are rejected.

The interface control reinterprets existing numbers. It is not a display-only switch. Changing an input from millimeters to inches makes an existing coordinate of `10` represent 254 mm. Coordinates and diameters scale together. Entered clearance and grid spacing and other linear rules scale in the same way. Minimum slope is a ratio and does not scale.

The profiles in [rule-profiles.json](../examples/rule-profiles.json) have millimeter defaults. When selected in the interface those defaults are converted to the current input unit. For a missing linear rule value the workflow also supplies its default in millimeters. This differs from an explicitly entered rule value whose units follow the input.

| Prototype setting | Default |
| --- | --- |
| Clearance | 50 mm |
| Grid spacing | 250 mm |
| Turn penalty | 300 mm added to search score |
| Minimum gravity slope | 0.01 or 1 percent |

These are chosen test settings. They are not official ship standards. Some older scenario files retain their original sample clearance. The result saves the actual settings used. See [the assumptions](assumptions.md) for fitting references and details about correcting units later.

## Files produced by the workspace

Each calculation gets its own folder under `data/generated/workspace/`.

- `result.json` records the original input and normalized layout with results
- `route-centerlines.dxf` contains original and accepted candidate centerlines
- `report.html` is a standalone interactive report

The JSON retains input units and records output coordinates in millimeters. The DXF declares millimeters. It includes the candidate only when one was accepted. Centerline exports do not manufacture elbows or modify the original CAD model.

A CAD import produces `cad-inspection.json`. It records source provenance and supported geometry. It also records omitted geometry and unverified pipe candidates. Generated output and original CAD files are excluded from Git by default.

The standalone HTML contains its plotting library for offline use. The workspace serves the same library locally. Geometry processing stays on the local machine.

## Earlier saved viewers

The original commands still provide reproducible saved examples:

```sh
.venv/bin/python scripts/run_demo.py
.venv/bin/python scripts/run_scenarios.py
```

The first creates the report under `data/generated/first-route-demo/`. The second creates a seven-case comparison under `data/generated/scenario-suite/`. The saved comparison dropdown switches between existing calculations. It does not run the solver again. The live workspace adds the gravity example and editable inputs.

The seven pressure scenarios have hand-calculated expectations for their original settings.

| Scenario | Expected outcome |
| --- | --- |
| Long route around equipment | 10500 mm becomes 8000 mm with two turns |
| Clear straight route | Keep the existing 5000 mm route |
| Pipe crossing equipment | Replace the invalid route with an 8000 mm detour |
| Equipment across the room width | Rise from z=1000 to z=2000 then return to z=1000 |
| Sealed partition | Report no route |
| 100 mm pipe in a 300 mm gap | Keep the straight route with 50 mm clearance |
| 250 mm pipe in the same gap | Report no route with the same clearance |

Changing the rules changes the problem. The old expected lengths are not promises for edited inputs.

## Real CAD scope

The preview importer has been exercised on the supplied scupper and cooling drawings. It reconstructs supported boundary curves and selected surface patches from their ACIS records. It applies body placement and block placement. It records unsupported geometry instead of claiming complete coverage.

Geometric candidates may be pipe sections or fittings or other round objects. Joining axes within a single solid does not establish connectivity across a system. Some curved axes are explicitly inferred. They still need comparison with the original CAD software.

The separate network inspector joins unambiguous matching ends across components. It checks endpoint position and inferred diameter and tangent direction. It stops at ambiguous branches. Member geometry and source placements are retained. This still does not establish system function or the identity of valves and equipment ports.

The bridge uses the selected candidate's endpoints and geometric envelope diameter. It creates an assumed local search box using the chosen padding. Surrounding solid instances become boxes around their full extracted samples. Only the selected source solid placements are excluded from these obstacles. Neighboring connectors are retained. Contacts at the fixed endpoints are reported for review.

Explicit endpoint connection assumptions preserve an exact prefix or suffix of the original route. The preserved stub must leave every selected contact box within its bounded length. The default limit is 250 mm and the maximum is 1000 mm. Only the listed obstacle contacts on that exact unchanged stub are exempted. All obstacles still apply to the searched middle and any changed segment. The full assembled result is validated again.

The controlled detour generator first checks the unchanged baseline. It then inserts four points into an eligible straight interior segment and validates the resulting detour. All original points and fixed stubs remain. The request may choose an offset up to 1000 mm. Failed trials preserve the baseline. The returned layout includes a `benchmark` record with the original layout and validation and a synthetic mutation label. This is a pressure-route test tool. It does not create training data with engineer-approved labels.

These boxes can be overly restrictive around curved components. They also cannot represent missing geometry. The meshes are visual samples rather than watertight collision solids. Neither preview bounds nor the assumed local search box establish the ship's permitted routing boundary. A candidate test remains provisional until its identity and surroundings are reviewed.

See [the CAD workflow](cad-workflow.md) for actual coverage counts and the provenance of the downloaded drawings.

## Remaining scope

- Verified pipe identity and connectivity from the supplied CAD
- Branches and multiple pipes competing for space
- Intermediate valves and fixed equipment ports along a run
- Full fitting shapes and connection orientations
- Installation access and maintenance space
- Pipe supports and hydraulic performance
- Complete ship structure and nearby systems for collision checking
- Official engineering rules and final CAD validation

The output distinguishes implemented checks from unverified items. Passing the available checks does not make a result ready for installation.

## Devices

The Mac runs development and validation on its CPU. There is no trained model or IQ9 connection in the current application.

The provided Snapdragon laptop is the main execution and demonstration target. The Notion resources confirm that extra hardware is optional for this track. Recreate the Python environment on that laptop and run the tests there. Its AutoCAD or Navisworks setup also needs verification.

IQ9 could later host a separate processing service. It does not automatically accelerate the laptop when connected. Its software setup and any supported AI model would need explicit integration. No cross-device speed comparison has been measured.

## Verification

Run the full automated suite from the project folder:

```sh
.venv/bin/python -m pytest -q
```

The checks have run on the Mac. Pressure routing tests use independently checked obstacle geometry. Gravity tests cover uphill sections and insufficient endpoint fall. They also cover sloped collisions and downward vertical runs.

CAD tests use constructed geometry with known placements. They verify source preservation and unit rescaling. Candidate extraction cases include branches and mixed radii that must be rejected.

HTTP integration tests run the local server on a temporary port. They exercise pressure and gravity calculations and unit conversion. Downloaded DXF coordinates are compared with the JSON result. A known cylinder upload is prepared as a CAD candidate test and run in both pressure and reversed gravity modes. Its source snapshot id survives through the result export. Other cases check malformed uploads and origin handling. The suite is a prototype behavior check rather than engineering certification.

New cases cover multi-solid chains and branch rejection. They also cover bounded endpoint stubs and collision checks on the movable middle. End-to-end HTTP tests run an unchanged chain and its generated detour and verify the benchmark metadata in the result export. The iteration has 307 passing automated tests on the Mac.

Human code review and final CAD comparison remain pending. Execution on the Snapdragon laptop and IQ9 has not been verified.
