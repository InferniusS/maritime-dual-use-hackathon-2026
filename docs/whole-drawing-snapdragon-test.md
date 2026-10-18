# Full drawing scan and finding navigation on the Snapdragon

This release changes the workflow from selecting one of 50 listed sections to
screening every supported inferred section in the current drawing. It does not
automatically convert unknown CAD objects into verified pipe components.

## Update the service and plugin together

1. Save your drawings and close AutoCAD completely.
2. In the PowerShell window running the service, press Ctrl+C once.
3. Extract the new release ZIP. Copy its `autocad-plugin`, `pipe_router`,
   `scripts`, `examples`, `web`, `tests` and `docs` folders into the existing
   project folder, replacing matching files. Keep the existing `.venv`, `data`
   and your CAD drawings. Copy the README and requirements files too.
4. Start the existing environment's service from that project folder:

   ```powershell
   .\.venv\Scripts\python.exe scripts\run_web.py
   ```

5. Leave that PowerShell window running. On the same laptop open
   `http://127.0.0.1:8767/api/health`. It must list `drawing_scan`.
6. Reopen AutoCAD 2027, run `NETLOAD`, and choose
   `autocad-plugin\prebuilt\Maritime.AutoCAD.dll` from the updated folder.

## Scan the actual detailed model

1. Open a working copy of `542_U02_Fresh Water Cooling Auxiliaries.dxf`.
   Select its drawing tab and Model space, not the synthetic `Drawing1` tab.
2. Type `MARITIMESCAN` and press Enter.
3. For this cooling model choose `mm`, `Pressure`, `Challenge`, clearance
   `50.8`, local review padding `500`, and optimization grid `250`.
   These are this test's settings, not universal values for every system.
4. Wait through snapshot import and the numbered progress. The new full scan has
   no 50-section cap. It keeps individual failures in the inventory and continues.
5. Read the final inventoried/processed/geometry-checked/not-processed counts.
   Then choose `Report`, or run `MARITIMEREPORT` after the command finishes.
6. In the report search by source handle or exact global section number. Review
   both **Rule coverage** and **Geometry and designer review gaps**. Download Excel
   for per-section measurements, source identity and evidence origin.
7. Choose `Find`, or run `MARITIMEFIND`. Enter a global number from this full scan.
   The plugin highlights the actual source objects or their owning block, frames
   the selected section, and marks its world-coordinate endpoints. `Next`,
   `Previous` and `Number` let you inspect different sections.

Global numbers belong to this snapshot. They do not equal an older quick-review
number. Source handles and the drawing hash keep the result traceable.

If you hold Esc while scanning, cancellation is requested between section checks.
Use `MARITIMEREPORT` when the service finishes to retrieve the partial report.
Unprocessed rows remain listed. Run `MARITIMESCAN` again for a fresh complete scan.
If source entities or context change, the old report remains viewable but a new
scan is required before finding or optimizing against the edited drawing.

## Review and visualize repairs

1. From the completed scan choose `Optimize`, or run `MARITIMEOPTIMIZE` and accept
   reuse of the latest full scan. Choose up to twelve non-overlapping sections by
   their global numbers. Every section is available; the list is not capped at 50.
2. Inspect each selected original route. Confirm only source connections you can
   identify. Do not use `ALL` or increase the fixed-connection length just to make
   a failed geometry check disappear.
3. Run the coordinated search. Each selected section gets an explicit outcome;
   accepted proposals are checked against one another as well as the surroundings.
4. Compare `Original`, `Proposal` and `Both`. `Save` stores accepted review geometry
   separately. The original CAD objects remain in place. Use `MARITIMEVIEW` later
   to switch saved comparison layers, and save the working drawing under a new name.
5. Open `MARITIMEREPORT` for measured differences and unresolved findings. Green
   means accepted under implemented checks; it does not mean a complete approved
   piping installation or necessarily the globally shortest route.
6. Run `MARITIMEDIMENSIONS` and choose `On` to see saved per-piece lengths,
   bend radius/angle and outside diameter in millimetres. `Off` removes the
   callout clutter from view. These are measured review dimensions, not approved
   fabrication or catalog-part annotations.

Saving review entities invalidates the full-scan cache conservatively. Scan again
before starting a later optimization run. This prevents stale proposals from
being applied after drawing edits.

## What to capture before the pitch

- Full scan coverage and all rule categories in the report.
- Two different findings located in the detailed CAD model with `MARITIMEFIND`.
- Original and Proposal using the same camera, then Both.
- Excel with section identities, coordinates and evidence types.
- A refusal or unresolved finding and its reason.
- Timed native input → scan → selected repair → save → report, plus a recording.

The Mac test visited all 1,416 inferred sections in the supplied cooling import.
Its many contact flags include possible intended connections and conservative
boxes; they are not 1,416 confirmed engineering defects. Missing surrounding
models and unknown component identities remain gaps even with complete inferred
route coverage. The [acceptance matrix](requirement-acceptance-matrix.md) lists the
remaining fabrication, pitched routing, access, support and topology gates.
