# Fresh CAD acceptance cases

These are invented test scenes, not organizer drawings. They provide a fresh
input test for the plugin, separate from `MARITIMEDEMO`'s JSON scene loader.

## Native AutoCAD test

1. Keep the local Python service running and load the current plugin.
2. Open a **new, empty drawing**. Each script adds one 114.3 mm diameter pipe
   cylinder and one rectangular obstruction. Do not run it in the organizer model.
3. Enter `SCRIPT` and select `01-collision-repair.scr` from this folder.
4. Enter `MARITIMEREVIEW`. Choose millimetres, Pressure and the Challenge profile;
   use clearance **50.8**, search padding **1500**, grid **250**.
5. Select the candidate that is **6000 mm long and 114.3 mm diameter**. Check that
   its cyan preview is the pipe crossing the wall. Continue, keeping the default
   **No** if asked to treat contacts as fixed connections. Use bend radius **304.8**
   when prompted.
6. A successful result must show a green rounded route around the wall. It must
   keep both endpoints and the original diameter. Save the comparison and record
   the resulting report and a screenshot. This confirms the native AutoCAD
   creation → DXF snapshot → import → search → preview path on that machine.
7. Repeat in another new drawing with `02-already-clear.scr`: expect **unchanged**.
   `03-blocked-port.scr` should refuse a replacement because the first endpoint
   is inside the obstruction. Keep **No** for fixed connections; this is a wall,
   not a connector. `04-translated-block.scr` reproduces the collision at a
   distant global position. Its native script creates translated primitives;
   the generated Python DXF version additionally tests block placement.

If a script stops at a prompt, save the command history and do not claim it passed.
These scripts are prepared for a Windows runtime test, not a claim that the test
has already been performed. The [Autodesk CYLINDER command](https://help.autodesk.com/cloudhelp/2026/ENU/AutoCAD-Core/files/GUID-81157F00-D5A5-4307-AD0C-4332066DE2AE.htm)
supports the axis endpoint used to create the horizontal cylinder.

## Repeatable service acceptance

From the repository folder run:

```powershell
.\.venv\Scripts\python.exe scripts\check_cad_acceptance.py
```

This generates new DXF files containing analytic cylinder and box geometry,
starts a temporary local service, uploads each DXF through the plugin HTTP API,
prepares its inferred route and runs the rounded proposal workflow. It never
replaces the extracted route, diameter or obstacles with JSON test geometry.
Results and reports are written under `data/generated/cad-acceptance`.

The synthetic analytic DXFs are parser fixtures, not ACIS-kernel-certified
authoring files. Use the native scripts above for Autodesk-generated solids.
The automated tests additionally check source identity, endpoint and diameter
preservation, non-mutation of the input file, and continuous segment clearance
against the known wall using an independent distance calculation.

Passing these cases does **not** establish arbitrary network connectivity,
commercial fitting availability, manufacturing approval, complete surrounding
geometry, ship-space compliance or acceptance of an unseen judge drawing.
