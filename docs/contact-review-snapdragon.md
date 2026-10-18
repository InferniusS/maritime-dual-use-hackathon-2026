# Contact-review iteration: Snapdragon rehearsal

This build adds `INSPECT <number>` at the coordinated optimization contact prompt.
It corrects the blocked-endpoint diagnosis. It does not automatically waive contacts,
change bend radius, or certify the full system.

## Install into the existing project

1. Save your work and completely exit AutoCAD.
2. Stop the Python service with Ctrl+C in its PowerShell window.
3. Extract the supplied contact-review ZIP. Copy its `autocad-plugin` and
   `pipe_router` folders into the existing Windows project, replacing matching
   files. Keep `.venv`, `data`, and source drawings. The included DLL is prebuilt.
4. In PowerShell opened in the existing project folder, run
   `.\.venv\Scripts\python.exe scripts\run_web.py` and leave it running.
5. Open AutoCAD, run `NETLOAD`, and select
   `autocad-plugin\prebuilt\Maritime.AutoCAD.dll` from that same updated folder.

## Inspect before deciding

1. Open the real drawing and run `MARITIMESCAN`. Use the drawing's actual units
   and system type. For the existing cooling test, retain the previous settings.
2. Use the new scan report to confirm the section number; 17 is not guaranteed
   after a drawing change. Choose Optimize and enter that number.
3. Check the cyan section, then press Enter.
4. At the fixed-contact prompt, type `INSPECT 1`, then Enter. The view frames the
   contact component. Its source object is highlighted when available; yellow
   outlines its recorded bounds and a red cross marks the route endpoint.
   A nested component highlights its owning block; the box locates the instance.
5. Press Enter to return. Repeat `INSPECT 2`, etc. Inspection accepts nothing.
   Enter verified connection numbers separated by commas only after identifying
   the actual mating components. A clearance contact is not necessarily a joint.
   If unsure, press Esc and retain the case for designer intervention.
6. For verified connections, review the fixed stub length, retain an appropriate
   bend radius, and Run. A blocked result has no new geometry to save.
7. If accepted geometry is returned, compare Original/Proposal/Both, Save,
   use `MARITIMEDIMENSIONS` → On, and save the drawing under a separate name.

## Native checks still needed

- Each numbered contact frames the matching source instance; source geometry is unchanged.
- Enter returns without accepting a contact; Esc removes temporary boxes/highlights.
- Source units match the model. Nested blocks are not mistaken for direct entities.
- A confirmed connection remains fixed; a real collision remains rejected.
- Save/reopen and Undo of an accepted review work. Screenshots show actual outcomes.

The existing result2 run remains blocked: four unapproved start contacts and no
accepted rounded alternative. The previous tangent error describes reconstruction
of the sampled reference curve, not a proven source-pipe defect.
