# Standards and model context iteration

This iteration follows the successful real drawing test inside AutoCAD on the Snapdragon. It focuses on checking the V3 brief against the information actually available for a route and its surroundings.

## What was already working

The team loaded a real cooling drawing, selected a candidate route, ran the local Python search, viewed the result and saved review centerline `26B9` on `MARITIME_SUGGESTION`. The source solids remained intact. That closes the live plugin connection milestone.

The original DB1 route was about 2413.467 mm long. The saved suggestion was 2558 mm. It was longer because it satisfied the selected geometric checks under the old 50 mm clearance assumption. Green did not mean globally shortest or compliant with every rule. The result came from one selected route checked against 76 nearby components.

## Scope of this iteration

The previous iteration could find a geometrically checked local route. This iteration adds a V3 rule assessment and a way to include declared surrounding models and permitted spaces.

- `MARITIMECAD` now offers **Challenge** and **Prototype** rule profiles. Challenge is the default. It starts at 50.8 mm shock clearance and rejects a lower value.
- Results distinguish measured passes, strict failures, preferences and missing evidence. Missing component or scene information stays visible as `needs_review`.
- Declared ship axes and flow direction allow checks for the directional minimum falls: 1:25 fore and aft and 1:100 athwartship. This is an explicit minimum-fall interpretation. The brief does not define a tolerance or a mixed-direction formula.
- Explicit allowed spaces are checked continuously along the route. Protected passageways and other exclusion volumes participate in collision checking. Additional aligned DXF geometry can supply obstacle envelopes.
- `MARITIMECONTEXT` loads reviewed context for the active drawing. The context records what surroundings are included and what remains unreviewed. It does not align drawings or recognize compartments automatically.
- Manufacturing and access assessments use explicit evidence tied to the original route. They do not automatically identify welds, bend sizes, fabricated lengths, supports or valve stem heights from CAD geometry. A changed route cannot silently reuse the original route's manufacturing evidence.
- Reports include an Excel review workbook with summary, rule findings, route points, route legs and declared original pieces. This is review information, not a complete parts list or an orderable bill of materials.

The rule register is in [challenge-requirements.md](challenge-requirements.md). The source values and interpretation choices are in [assumptions.md](assumptions.md).

## Checks completed on the Mac

The eight public challenge examples all returned their expected results:

| Case | Expected result |
| --- | --- |
| Valid straight route | Unchanged |
| Added detour | Improved |
| Protected passageway | Rerouted around the protected space |
| Fore-and-aft drain with insufficient fall | No accepted route |
| Fore-and-aft drain with correct fall | Unchanged |
| Athwartship drain with correct fall | Unchanged |
| Declared bend below the required radius | No accepted route |
| Declared welds too close together | No accepted route |

These are controlled examples. They do not claim automatic recognition of manufactured parts in unseen drawings.

Real drawing replay at **50.8 mm** produced a 2558 mm suggestion for DB1's 2413.467 mm original. The joined DB2/E6D/E16 route stayed unchanged at about 3679.248 mm with 37 nearby obstacles. Both still have engineering review items because complete part and ship evidence has not been supplied.

The complete Mac suite passed **610 tests in 11.53 seconds**. The updated plugin built with zero warnings and zero errors. Its new Challenge profile, context loading and report flow still need a live Snapdragon check. The earlier real route Save is already proven.

## Navisworks decision

Keep AutoCAD as the main application for this iteration. Page 3 of the brief permits the demonstration in AutoCAD or Navisworks. The AutoCAD plugin already works on the team's Snapdragon laptop and the team has found a useful shaded 3D view.

Autodesk describes both Navisworks Simulate and Manage as tools for model integration and review. Manage additionally includes clash detection and interference management. Simulate does not include that Clash Detective functionality. See Autodesk's [Manage and Simulate comparison](https://www.autodesk.com/support/technical/article/caas/sfdcarticles/sfdcarticles/Functionality-differences-between-Navisworks-Manage-and-Simulate.html).

Navisworks Manage could be useful later for independent clash review of combined structure, equipment and system models. It would be an additional review step. It would not supply our missing pipe identities, permitted spaces, bend rules, parts selection or routing logic.

Autodesk's documented [SwitchBack workflow with AutoCAD](https://help.autodesk.com/cloudhelp/2023/ENU/Navisworks/files/GUID-495784CF-9052-41A6-B146-8E4337569990.htm) returns the selected object to AutoCAD, where changes are made, then refreshes Navisworks. This supports retaining AutoCAD for the editing workflow. That older documentation is evidence of the workflow, not confirmation that every AutoCAD 2027 and Navisworks version combination works on this event laptop.

Do not switch applications to address a Python request failure or an incomplete geometric rule. Neither is fixed by a new viewer. Revisit Navisworks if the organizers supply an assembled Navisworks model as the only usable context, require its review workflow, or provide Manage with a proven compatible installation that improves combined-model inspection. Check that exact software version and laptop before committing the demo to it. We have not verified Navisworks on Snapdragon.

## Remaining milestones

1. **Connected sections.** Confirm anchors at valves, equipment and branch ports. Preserve required spaces and all necessary connections. Check proposed changes against one another.
2. **Complete surroundings.** Align the relevant structure, equipment, ventilation, wireways and other piping. Identify walkways, ladders, access areas and permitted compartments. Unknown coverage remains a review item.
3. **Buildable assemblies.** Use verified nominal sizes, manufactured bend or fitting identities, full occupied shapes, fabricated pipe lengths, welds, supports and installability checks.
4. **Designer deliverables.** Produce scaled dimensioned 3D results plus an Excel parts list with global coordinates and component changes. Identify unresolved items.
5. **Unseen-case evidence.** Test deliberately damaged copies and untouched valid models across pressure and pitched systems. Keep some cases separate until evaluation. Record repair, unchanged and blocked outcomes honestly.

The plugin connection is proven. The route engine can make and validate local suggestions. The remaining work is understanding more of the system and demonstrating the engineering requirements with sufficient evidence. A completion percentage would hide those differences, so use these milestones instead.

## Team testing

Use the [Snapdragon guide](snapdragon-guide.md) for the full update and AutoCAD prompt sequence. Update the Python files and DLL together. Keep the existing `.venv`, drawings and previous reports.

Before starting the service, run this in PowerShell from the repository folder:

```powershell
.\.venv\Scripts\python.exe scripts\check_challenge.py
```

Expect eight `PASS` lines followed by a report folder. Each case includes `report.html`, `result.json`, `route-centerlines.dxf` and `route-review.xlsx`. These files let the team review both a successful repair and the reason a case was blocked.

Start the service:

```powershell
.\.venv\Scripts\python.exe scripts\run_web.py
```

Its health capabilities should include `challenge_rules_v3`, `model_context` and `review_excel`. Keep that window running. Load the updated DLL in a new AutoCAD session. Run `MARITIMECAD`, choose Challenge and keep 50.8 mm clearance. Use 500 mm padding and a 250 mm grid for comparison with the replay.

Without a context file, a local calculation is still possible. Its report must identify missing surroundings and engineering evidence. Do not describe that result as a complete ship check.

### Add reviewed surroundings

Start from [model-context-template.json](../examples/model-context-template.json). Keep a working copy under `data/generated` or another local folder. Fill it with verified drawing units, a shared coordinate frame, source files, permitted spaces and exclusions. Unknown coverage stays false. Do not set a confirmation flag merely to make the builder run.

The `drawing_files` list identifies local DXF exports. Each entry needs `id`, `path`, `category`, `source_units`, `coordinate_frame_id` and `shared_coordinates_confirmed`. Supported categories are structure, equipment, piping, ventilation and wireways. The builder reads those files without changing them. It does not register or align drawings.

Once the specification is reviewed, generate a new context file:

```powershell
.\.venv\Scripts\python.exe scripts\build_model_context.py --spec data\generated\context-spec.json --output data\generated\model-context.json
```

The output must be a new filename. Existing files are not overwritten. A raw context JSON or a wrapper containing `model_context` and optional `engineering` is accepted by the plugin. Engineering evidence must explicitly use mm and correspond to the selected original route.

In AutoCAD with the intended drawing active, run:

```text
MARITIMECONTEXT
```

Choose `Load` and select the generated JSON. Run the command again with `Status` to check whether context is loaded. `Clear` removes it. Loading is tied to that drawing's fingerprint for the current AutoCAD session. Reload it after restarting AutoCAD. The local service validates geometry and evidence when `MARITIMECAD` prepares the route.

Run `MARITIMECAD` and inspect the new report's source coverage and rule findings. Compare the local calculation with the calculation including reviewed surroundings. Do not relabel an earlier report with new units or rules without rerunning it.
