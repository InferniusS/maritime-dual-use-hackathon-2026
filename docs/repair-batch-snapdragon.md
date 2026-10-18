# Repair-batch Snapdragon rehearsal

Release label: `repair-batch-20261018`. These steps distinguish installation,
real-model diagnosis and a controlled fresh-input repair. They do not certify
full-system compliance or a repaired organizer model.

## Install and confirm matching versions

Save work, close AutoCAD, stop Python, then merge the new repository files into
the existing Windows project. Keep `.venv`, `data` and the source drawings.
Update `pipe_router`, `autocad-plugin`, `docs`, `tests`, `scripts`, `examples`,
`web` and tracked root files together. In the existing project folder run:

```powershell
.\.venv\Scripts\python.exe scripts\run_web.py
```

Open AutoCAD and the drawing. Run `NETLOAD` and choose the updated
`autocad-plugin\prebuilt\Maritime.AutoCAD.dll`, then run `MARITIMEVERSION`.
It should print plugin and service `repair-batch-20261018` and say they match.
An unknown command means an older DLL is loaded. A mismatch means an old service
or DLL is still running; restart the corresponding process after updating it.

## A. Real cooling-drawing contact evidence

1. Open the usual cooling source DXF and run `MARITIMESCAN`: mm, Pressure,
   Challenge, clearance50.8, padding500, grid250 at the corresponding prompts.
2. Choose Optimize. Locate source DB2/E16/E6D in the fresh report; enter its
   current global number. The earlier number17 is not a permanent identifier.
3. At the cyan preview press Enter. At the contact prompt use `INSPECT 1`.
4. Magenta now marks the selected path near the endpoint; red marks its fixed
   endpoint; yellow bounds the inspected neighboring component. Read the
   contact evidence in the command history. `Next` and `Previous` move between
   contacts; Enter/Return returns without approving them. Esc cancels.
5. For the previously supplied snapshot, D92/E55 should be clearance-only
   contacts; E56 overlaps the pipe envelope and ECB uses a fallback box.
   These are approximate geometry classifications, not verified part identities.
6. Enter with no contact numbers and run with the same radius304.8 to preserve
   the no-exemptions test. Expect blocked, no saved proposal. `MARITIMEREPORT`
   should include an Endpoint contact evidence table; Excel has an Endpoint
   contacts sheet. Do not approve ALL to force a green result.

The selected real section is still unresolved. Its fixed endpoint neighborhood
must be verified by a designer or better component/port data; a clearance-only
neighbor is not automatically part of the connection assembly. Earlier local
conditional tests of sections16 and27 also did not establish an accepted repair.

## B. Fresh native CAD repair, unchanged and blocked cases

These are clearly labelled INVENTED TESTS. They exercise the ordinary scan and
optimization input path, not the canned MARITIMEDEMO output. Do not show them as
an organizer model. On 18 October the user confirmed all three expected native
outcomes on the Snapdragon. The collision case initially ran with padding 500
and was blocked; rerunning with the documented padding 1500 produced the visible
rounded detour. The unchanged and blocked explanations were then confirmed by
the user. These are user-observed outcomes; the latest saved reports for all
three cases have not been independently inspected together.

For each case start a NEW EMPTY drawing (never run the creation script in the
cooling model). Run `SCRIPT` and select one file from
`autocad-plugin\examples\acceptance`:

| Script | Created input | Expected geometric outcome |
| --- | --- | --- |
| 01-collision-repair.scr | A straight cylindrical pipe through a wall | Rounded proposal avoids the wall |
| 02-already-clear.scr | Same pipe, wall moved clear | Unchanged straight geometry |
| 03-blocked-port.scr | Wall covers the fixed endpoint | Blocked; no proposal saved |

The script creates source objects using AutoCAD commands. After it finishes,
run `MARITIMESCAN`: mm, Pressure, Challenge, clearance50.8, padding1500, grid250.
The source pipe should be the sole inferred route; confirm it in the report.
Choose Optimize and its number. Press Enter at its source preview. Approve no
endpoint contacts (the blocked-port case deliberately has an invalid endpoint).
Use bend radius304.8 and Run.

For an accepted result choose Both, inspect the full path from two angles,
then Save. Run `MARITIMEDIMENSIONS` and choose On. Run `MARITIMEREPORT` and check
the geometric pieces/global coordinates against the model. Save the DWG under a
new name; close/reopen and verify MARITIMEVIEW still shows Original/Proposal/Both.
Use a separate copy to verify one Undo removes the saved review group.

Screenshot evidence: source collision, accepted proposal+wall with dimensions,
unchanged outcome, and blocked-endpoint explanation. Record exact release label,
model name and outcomes. Green is geometric acceptance under implemented checks,
not commercial fitting or full engineering approval.

## Current minimum-requirement gaps

The brief permits AutoCAD and designer-implementable output. It also requires
commercially available fittings, correct pitched systems, preserved sizes,
containment and no clashes against relevant surroundings. Those requirements
are not all implemented or established by this release. See
[pitch-batch-acceptance.md](pitch-batch-acceptance.md) for the explicit gates and
submission checklist. Do not describe the prototype as a fully compliant model.
