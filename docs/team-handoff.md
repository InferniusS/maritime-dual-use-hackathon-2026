# Team handoff

Updated October 17 2026. AutoCAD is the main application. The browser remains available as a fallback. The current milestone is [V3 standards and model context](standards-iteration.md).

## Start here at the venue

AutoCAD 2027 and Python 3.14.8 already run on the Snapdragon. Follow the [Snapdragon guide](snapdragon-guide.md), starting with its update instructions. Update the Python code, examples and DLL together. Keep the existing `.venv`, `data` and drawings.

Before starting the service, run the new public checks:

```powershell
.\.venv\Scripts\python.exe scripts\check_challenge.py
```

Expect eight `PASS` lines and a report folder. Then start the local service:

```powershell
.\.venv\Scripts\python.exe scripts\run_web.py
```

Keep PowerShell running. The health response should include `challenge_rules_v3`, `model_context` and `review_excel`. In a new AutoCAD session load `autocad-plugin\prebuilt\Maritime.AutoCAD.dll` with `NETLOAD`.

Run `MARITIMECAD` on a reviewed test copy. Choose **Challenge** with 50.8 mm clearance, 500 mm local padding and a 250 mm grid. Review the cyan route and its end contacts. Save a suggested route only after comparing it with the original. The guide lists each prompt.

If a reviewed model context JSON is ready, use `MARITIMECONTEXT` then `Load` before `MARITIMECAD`. It can include additional aligned drawings, allowed spaces, protected passageways and route-bound engineering evidence. It does not align drawings or classify parts automatically. Reload context after restarting AutoCAD.

## What this iteration adds

The previous iteration made the real drawing read, route calculation, preview and Save work on the Snapdragon. Its pipe-shaped obstacle envelopes improved local collision checks.

This iteration adds:

- The V3 Challenge profile with a minimum 50.8 mm shock clearance
- Rule findings that distinguish passes, failures, preferences and missing evidence
- Directional drain fall checks when ship axes and flow direction are declared
- Additional aligned scene geometry, allowed spaces and exclusion areas
- Manufacturing and access checks from explicit evidence tied to the selected original route
- HTML findings and an Excel review workbook with coordinates and declared pieces
- Eight public regression examples and saved reports

A changed route cannot automatically inherit the original route's bend, weld or access evidence. That must be reviewed or supplied for the changed design. The Excel file is review data. It is not yet the challenge's complete manufactured parts list.

## What is verified

The team saved a real drawing suggestion on the Snapdragon as review object `26B9` on `MARITIME_SUGGESTION`. That live run used the previous 50 mm setting. It proves the plugin connection and Save path. The original source solids remained intact.

The latest Mac replay uses 50.8 mm:

| Test | Result |
| --- | --- |
| DB1 | Original 2413.467 mm. Suggestion 2558 mm. Local geometric repair with engineering review items |
| Joined DB2 / E6D / E16 route | Original remains unchanged at about 3679.248 mm with 37 nearby obstacles |
| Eight public challenge examples | All expected outcomes passed, including valid, repaired and blocked cases |
| Updated Windows plugin build | Zero warnings and zero errors |

The new Challenge profile, context loading and Excel report flow still need a live Snapdragon check. Undo after saving still needs an explicit venue check. Saving a review object and saving the drawing file to disk are different actions.

The historical DB1 test used 76 nearby components: 43 supported pipe envelopes and 33 fallback boxes. It changed one selected route. Green did not mean globally shortest or fully compliant. The replacement was longer to satisfy the modeled clearance check.

Earlier saved baseline and artificial detour examples remain available in [the browser example](../examples/real-cad-demo/report.html). They use historical 50 mm assumptions. Do not present them as newly tested V3 results. See [the earlier geometry milestone](pipe-shape-milestone.md) for their recorded measurements.

## What remains

1. **Confirm this update on the Snapdragon.** Run the eight cases, load the fresh DLL and check DB1 with Challenge. Inspect the HTML and Excel findings. Test a reviewed context file. Confirm Save and Undo.
2. **Bind actual components to their rules.** Identify size designations, manufactured bends, fittings, welds, supports and valve stems. Check physical bend volumes and meaningful direction and elevation objectives.
3. **Coordinate connected sections.** Preserve equipment connections, branch ports and required intermediate spaces. Check proposed changes against one another.
4. **Verify a complete real scene.** Align the relevant structure, equipment, ventilation, wireways and other piping. Mark permitted spaces and access areas from the source models.
5. **Deliver manufactured assemblies.** Select suitable standard parts, plan fabricated lengths and check installation. Produce dimensioned results and the final Excel parts list with global coordinates.
6. **Demonstrate unseen cases.** Keep some damaged and valid layouts separate until evaluation. Show unchanged, repaired and blocked outcomes across different systems. Report unresolved findings honestly.

The V3 document's numeric rules are now recorded. We do not need to ask the mentors for those same values again. The remaining unknowns concern each actual model and any interpretation or exception that the brief does not resolve.

## Which application and device

| Item | Role |
| --- | --- |
| Mac | Development, regression tests, real drawing replay and plugin build |
| Snapdragon laptop | AutoCAD and local Python execution. Real route Save is proven. New standards update needs venue verification |
| Dragonwing IQ9 | Optional future work. No board dependency or measured speed benefit in this workflow |
| Navisworks | Optional combined-model review. Keep AutoCAD for editing and the plugin. No Navisworks installation on Snapdragon has been verified |

The program performs geometric search. It does not learn from uploaded drawings or require a training dataset. The brief accepts showing the result in AutoCAD or Navisworks. Autodesk's Manage edition adds clash detection, but switching applications does not supply our missing engineering or routing logic. Read [the Navisworks assessment](standards-iteration.md#navisworks-decision).

The [requirements register](challenge-requirements.md) maps the project to the brief. The [assumptions](assumptions.md) explain numeric interpretations and missing evidence. Keep source drawings and generated geometry-containing reports local.
