# Test coordinated pipe sections on the Snapdragon

This build adds `MARITIMEOPTIMIZE` for several sections of the current CAD drawing.
The old line/polyline test command is now `MARITIMELINEOPTIMIZE`.
`MARITIMEREPORT` opens the latest report for the current drawing without searching command history.

## Update once

1. Save your work and close AutoCAD completely.
2. Press Ctrl+C in the PowerShell window running the local Python service.
3. Extract the new ZIP. Copy `autocad-plugin`, `pipe_router`, `scripts`, `examples`,
   `web`, `tests` and `docs` into the existing project folder, replacing matching
   files. Copy the README and both requirements files too. Keep your existing
   `.venv`, `data` and drawing files.
4. In PowerShell, in the existing project folder, run:

   ```powershell
   .\.venv\Scripts\python.exe scripts\check_cad_acceptance.py
   ```

   These are new synthetic CAD input tests, not a test of the organizer's drawing.
   Expect four PASS outcomes. The blocked case passes by refusing the route.
5. Start the service and leave its window running:

   ```powershell
   .\.venv\Scripts\python.exe scripts\run_web.py
   ```

6. On the Snapdragon open `http://127.0.0.1:8767/api/health`. Confirm it contains
   `coordinated_sections`. If absent, the old Python service or folder is running.
7. Reopen AutoCAD 2027. Run `NETLOAD` and select the updated
   `autocad-plugin\prebuilt\Maritime.AutoCAD.dll`.

## Prove fresh input, before relying on the prepared demo

Follow [the native CAD acceptance instructions](../examples/cad-acceptance/README.md).
Run `01-collision-repair.scr` in a **new empty drawing**, then `MARITIMEREVIEW`
with mm / Pressure / Challenge / 50.8 clearance / 1500 padding / 250 grid.
Select the 6000 mm route with 114.3 mm OD. Do not declare the wall an intended
connection. Use radius 304.8. Save a successful green rounded proposal around
the wall, then run `MARITIMEREPORT`.

This is a stronger runtime test than opening `MARITIMEDEMO`: the pipe and wall
are authored by AutoCAD, exported, extracted and searched afresh. Label the
scene synthetic in the pitch. It still requires a Windows rehearsal; the Mac
automated result is not evidence that this exact native script ran there.

## Coordinate sections in the supplied drawing

1. Open a working copy of the cooling drawing and select its Model tab.
2. Run `MARITIMEOPTIMIZE`.
3. Confirm mm / Pressure / Challenge. Use 50.8 mm clearance, 500 mm local search
   padding and 250 mm search grid for comparison with the earlier test.
4. Read the numbered sections and their **source handles**. Enter the desired
   numbers separated by commas. Up to twelve non-overlapping sections are allowed.
   Numbers belong to this fresh review; do not assume the old numbering is stable.
   The previously successful section has handles **DB2, E16, E6D**, 88.9 mm OD
   and approximately 3679 mm sampled length.
5. For each section, inspect the cyan route, then press Enter to continue.
6. If contacts are listed, enter only the numbers of intended fixed connections
   you can verify. `ALL` is available only when every listed contact is intended.
   Enter leaves contacts unwaived. Use a 250 mm fixed length only if it matches
   the intended connection; increasing this value is not a general collision fix.
7. Accept or specify the reviewed bend radius; the current default is at least
   304.8 mm. Then choose `Run` for the combined calculation.
8. The command displays progress. Earlier accepted proposals become obstacles
   for later routes, and the accepted set is checked again for mutual clearance.
   A blocked section remains in the report without discarding other successful
   sections. A search can miss jointly feasible moves because other original
   pipe sections are conservatively retained as obstacles.
9. In the combined 3D view choose `Original`, `Proposal`, then `Both`. Choose
   `Save` to retain the accepted comparisons. Green means accepted under the
   implemented checks, not necessarily shorter or fully compliant.
10. Run `MARITIMEREPORT` for outcomes and download the Excel review. Use
    `MARITIMEVIEW` to switch the saved comparison. Save As to a separate DWG.

If you stop waiting with Esc, no batch geometry is saved. Let the local job
finish, then use `MARITIMEREPORT` to recover its report. Starting another
calculation while the job runs produces an explicit busy message.

## Photos and evidence to retain

- Original and Proposal with the same camera, then Both.
- Report showing proposed, unchanged, blocked and needs-review outcomes as applicable.
- Excel showing section identity, geometric pieces and global coordinates.
- One visible refusal, such as the blocked-port native test, with its reason.
- A timed fresh-input run on the actual Snapdragon. Save a fallback recording.
- In a disposable saved copy, verify one Undo removes the combined review while
  leaving source geometry intact. Do not undo unrelated earlier work.

## Honest completion boundary

This is a working local CAD plugin with bounded section screening, rounded
proposals, multi-section coordination and review exports. It does not redesign
all 1416 inferred units in the supplied drawing. The current screen lists at most
50 units; the coordinated run accepts at most twelve selections.

Network branches, verified ports, complete vessel spaces, commercially suitable
fittings, supports, installation access and rounded pitched drains remain gaps.
The report must retain these as designer intervention rather than claiming a
fully buildable compliant system. Those gaps affect the hackathon requirement,
not only a future marketplace release. AutoCAD remains the implementation
environment; moving the same engine to Navisworks would not resolve them.
