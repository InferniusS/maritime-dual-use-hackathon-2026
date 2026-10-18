# Team handoff

The latest build adds rounded 3D pipe proposals. Start with the [one-command-at-a-time Snapdragon guide](3d-demo-snapdragon.md). The [iteration record](assembly-iteration.md) explains the measured progress and limits. AutoCAD stays the main application; the browser remains a fallback.

## Venue sequence

1. Save work and close AutoCAD. Stop the Python service with Ctrl+C.
2. Merge the new project files into the existing folder, preserving `.venv`, drawings and `data`.
3. Run `.\.venv\Scripts\python.exe scripts\check_assembly.py`. Expect five PASS outcomes, including correct rejection of the blocked case.
4. Start `.\.venv\Scripts\python.exe scripts\run_web.py`. Leave that PowerShell running. The health page must include `rounded_pipe_proposal`.
5. Reopen AutoCAD 2027 and NETLOAD the updated prebuilt DLL. In an empty drawing run MARITIMEDEMO and select `examples/assembly/01-congested-room.json`.
6. Compare Original, Proposal and Both. Save the 3D review; use MARITIMEVIEW to switch views. Check Undo and the exported Excel/DXF/report.
7. Test a real drawing through MARITIMECAD, reviewing the selected connected route and endpoint contacts. A failure to fit the radius is a valid blocked result; do not reduce it simply to force a green route.

## Evidence

The prior Snapdragon report confirms the V3 rule profile and a centerline clearance repair. The new Mac tests demonstrate five physical-proposal scenarios and a rounded proposal for the real DB2/E6D/E16 connected section. DB1 did not yield an accepted rounded proposal under the selected radius and fixed-stub constraints.

The congested-room example is invented and labeled as a test. It reduces the rounded route from about 17.08 m and four bends to 13.42 m and two bends, keeping its ports fixed. Do not describe this as a saving achieved on the untouched ship model.

A successful DLL build is not a live AutoCAD test. The new mesh rendering, comparison, Save and Undo must be checked on the Snapdragon. Once they work, record a short fallback video before the pitch.

## What remains beyond this iteration

- Complete ship surroundings, alignment and explicit allowed spaces for the real test model.
- Reliable equipment, valve, branch and connection identities across full networks.
- Verified catalog fittings, nominal sizes, materials, wall thickness and fabrication allowances.
- Rounded drain/vent geometry with pitch through every bend. Existing drain centerline checks remain available.
- Full challenge compliance and testing on the organizer's withheld files.

The geometric search does not learn from uploads. No training dataset or IQ9 board is needed for this implementation. AutoCAD runs the plugin and the same laptop runs Python. Navisworks is optional review software; switching will not supply missing route semantics or engineering checks.

Keep source drawings and newly generated real-geometry reports local. Use the five synthetic files for portable rehearsal. Preserve the older website and historical milestone documents as fallback/reference material.
