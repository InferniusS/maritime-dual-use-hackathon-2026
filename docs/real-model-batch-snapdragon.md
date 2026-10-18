# Real-model batch: install, inspect and retain evidence

Release label: `real-model-batch-20261018`.

The same scan-and-optimize workflow operates on the active supported DWG/DXF;
the cooling drawing and route 17 are not hardcoded. This batch makes selected
geometry, fixed connections, search limits and delivered coordinates easier to
inspect. It does not automatically identify all engineering connections, redesign
an entire system, or complete every requirement in the challenge brief.

The previous `repair-batch-20261018` release has user-confirmed native outcomes
for repair, unchanged and blocked synthetic cases. The new release still needs
its own Snapdragon run. Do not substitute those prior outcomes for a new-release
real-model acceptance test.

## Install and confirm

1. Save the open drawings, close AutoCAD and stop the old Python service.
2. Update the complete repository, including `pipe_router`, `autocad-plugin`,
   `scripts`, `tests`, `docs`, `examples`, `web` and tracked root files. Keep
   `.venv`, `data` and source drawings.
3. From the Windows project folder start:

   ```powershell
   .\.venv\Scripts\python.exe scripts\run_web.py
   ```

4. Open AutoCAD and a copy of the source drawing. `NETLOAD` the updated
   `autocad-plugin\prebuilt\Maritime.AutoCAD.dll`.
5. Run `MARITIMEVERSION`. Both plugin and service must show
   `real-model-batch-20261018` and match. If not, stop and update/restart the
   mismatched process before testing.

## A. Recheck the three controlled cases

Use a new empty drawing for each script. Run `SCRIPT` and choose a file from
`autocad-plugin\examples\acceptance`:

| Script | Expected outcome |
| --- | --- |
| `01-collision-repair.scr` | Rounded proposal detours around the wall |
| `02-already-clear.scr` | Unchanged straight geometry |
| `03-blocked-port.scr` | Blocked endpoint, no accepted proposal |

Run `MARITIMESCAN`: millimetres, Pressure, Challenge, clearance **50.8**, padding
**1500**, grid **250**. Choose Optimize and the pipe's number from the current
scan. At the new **Search padding for this optimization in mm** prompt, type
**1500** again to make the test setting explicit. At the route inspector press
Enter to Continue. Approve no endpoint contacts. Use bend radius **304.8** and
Run. Before confirming Run, verify the printed padding is 1500 and clearance is
50.8. A tighter 500 mm region is not the expected collision-repair test.

Use Both/Save if the comparison prompt appears. Once the command finishes,
`MARITIMEREPORT` opens the result. The blocked case must not save a green
proposal. These are invented source inputs; label all screenshots accordingly.

## B. Inspect a selected real pipe section

1. Open a copy of an organizer-provided pressure-piping DWG or DXF. Include the
   relevant aligned surroundings for the claim you want to test. A pipe-only
   drawing cannot establish absence of clashes with missing structure or HVAC.
2. Run `MARITIMESCAN`. Enter the actual source units, Pressure, Challenge,
   required clearance, local padding and grid step. The earlier cooling test
   used millimetres, clearance 50.8, padding 500 and grid 250. These settings
   are inputs to review, not universal engineering approval.
3. Inspect the new report and choose the pipe by its **current global number**
   and source handles. Global numbering can change with a new snapshot. An
   inventory of inferred cylindrical components is not a verified pipe graph.
4. Choose Optimize, enter up to 12 non-overlapping section numbers, and read the
   new padding prompt. Enter the intended local search padding. This applies to
   the selected optimization; it does not rewrite the earlier full-scan report.
   A larger region includes more nearby obstacles and may reach a work limit;
   no automatic resizing or obstacle dropping is implied.
5. The source inspector shows **magenta selected route**, **red start**, and
   **blue end**. Use:
   - **Start / End:** focus on the corresponding connection neighborhood.
   - **Route:** frame the selected route.
   - **Component:** choose an included source component; its bounds appear
     yellow when available. A nested source may highlight its owner rather than
     provide separately available component bounds; the command tells you.
   - **Region:** show the **white search box**. This box is not the ship hull.
   - **Continue** or Enter: proceed without approving any connection.
   - **Cancel:** stop and restore the temporary display.

The overlays are intentionally visible above the source geometry. Their colors
locate records; visibility alone does not prove physical clearance or a joint.

## First real-model candidate to inspect: Fire-Fighting and Deck Wash

Open a copy of the organizer's **`642_U02_Fire-Fighting and Deck Wash.dwg`**.
This is a different source drawing from the repeatedly tested cooling layout.
Run the fresh scan with millimetres, Pressure, Challenge, clearance **50.8**,
padding **1500** and grid **250**. In the report, look for this source group:

```text
CD, D0, D2, D3, D4, D8, D9, DA, DC
```

The local converted snapshot labels it `network:1b1aa05acda58321`. Use its number
from **your current AutoCAD scan**, not a remembered row number. If the identity
does not match, inspect the corresponding source geometry before proceeding.
Choose Optimize and confirm padding **1500** at the new optimization prompt.

A local exploratory run found a **conditional geometric proposal** for this
source group: a straight path about **1739.503 mm** long at **88.9 mm OD**, with
both original connection portions of about **137.251 mm** retained. The inferred
source path is about **1850.453 mm** long. These are different representations:
there is no validated rounded source baseline, so do not turn the difference
into an approved physical length saving or a fabricated-parts claim.

**The condition is unresolved:** that exploratory run assumed all 14 listed
endpoint contacts belonged to intended fixed connections. Their identities were
not verified. This is a candidate for native inspection, not a demonstrated
organizer-model repair or proof that a new algorithm fixed it. No instruction
here authorizes accepting those contacts automatically.

Use Start and End in the route inspector to locate the two connection groups,
then `INSPECT <current contact number>` at contact selection. The local snapshot
provided these cross-references; confirm the current source and instance path:

| End | Nearby source records to inspect | Local grouping |
| --- | --- | --- |
| Start | `CA`; `111`, `112`, `113`, `114`, `115`, `116` | `111`–`116` are nested in block instance `119[0]` |
| End | `CF`; `105`, `106`, `107`, `108`, `109`, `10A` | `105`–`10A` are nested in block instance `10D[0]` |

The inspected source should let you determine whether each object is actually
part of the intended end connection assembly or an unrelated nearby obstacle.
Several contacts are clearance-only, and some use conservative fallback boxes;
proximity and a shared bounding box alone do not establish an intended joint.
The nested owner may highlight as a group. If the individual component cannot
be distinguished well enough, leave it unresolved rather than approving it.

Enter only the **current contact numbers you verified**. Do not copy a list of
old numbers or choose ALL to reproduce the exploratory outcome. If no contacts
can be verified, Enter for none is a useful blocked-result check. If appropriate
connections are verified, the local numerical probe used a maximum unchanged
stub length **250 mm** and radius assumption **304.8 mm**. Check the newly shown
cyan stub portions and movable anchors before Run. Those dimensions are test
assumptions to review, not catalog or engineering approval.

If accepted with justified selections, the expected visible change is removal
of the selected pipe's shallow dip, leaving a straight same-height connection
between the fixed ends. Check both attachment portions, surrounding source
objects, dimensions and report. The probe contains this piping drawing's local
surroundings; complete ship structure and other systems were not established.
If blocked, keep the report and its specific unresolved contacts. Do not reduce
clearance or approve unrelated objects to force the expected appearance.

The exploratory real-model reports and geometry are kept locally under
`data/generated/real-connections-next/`, including `conditional-fire-review`.
They are not release fixtures and must not be copied into the distributable
example package. Preserve the native test's own report and source hash so it
can be distinguished from the exploratory, assumption-dependent result.

## C. Verify fixed connections and run

At the intended-contact prompt, use `INSPECT 1` and Next/Previous to review
contacts. Enter/Return returns to selection without approval. Enter at the
selection prompt approves **none**. Only enter the numbers of connections you
have actually verified. Do not choose ALL just to force the search to proceed.

If contacts are explicitly selected, choose a finite maximum fixed-stub length
consistent with the source connection. The prepared route inspector appears
again: **cyan marks unchanged fixed-stub portions**, with the remaining selected
source path magenta. Check both the fixed endpoint and the transition to the
movable section. The command reports the movable anchor and any active source
obstacles that still touch the fixed geometry. Continue does not add approvals.

Use a known applicable bend radius where available; the entered radius otherwise
remains a geometric assumption. At the Run summary verify padding, clearance,
grid, radius, selected contact IDs and stub limits for every section. Choose Run.

The previously blocked cooling section must not be forced green. Its contacts
and preserved geometry remain a real investigation case. A fixed endpoint or
unchanged-stub conflict cannot be solved by merely enlarging the search box.

The importer also removes only demonstrably redundant or contained conservative
tube envelopes before applying its existing representation limit. This lets
some supported complex solids retain a tighter analytic representation instead
of falling back to a broad box. It preserves the represented occupied coverage,
does not drop unrelated obstacles, and does not turn unsupported faces into
verified surfaces. This change helps obstacle representation; it does not
identify connections or make arbitrary models solvable.

## D. Understand and retain the output

- **Proposed:** a rounded geometric candidate passed the implemented checks.
  Inspect both ends and the tightest passage, then compare Both and Save.
- **Unchanged:** the original section was retained under those checks; no better
  accepted candidate was found. This is not proof of global optimality.
- **Blocked:** no accepted proposal is available. Read **Next action** and the
  **Fixed connection check** before changing assumptions. The search settings
  distinguish fixed conflicts from a represented obstacle spanning a finite
  search region; neither establishes that every physical alternative is
  impossible.
- **Needs review / not processed:** source evidence, supported capability or a
  processing limit prevented a completed accepted result. No unsupported part
  should silently count as compliant.

Once the command returns to `Command:`, run `MARITIMEREPORT`. Record:

1. **Batch item** and **source scan number**, when present. Batch item 1 is the
   first selected item, not automatically scan route 1. A scan number is a
   client cross-reference; stable Candidate ID, handles and source snapshot hash
   identify the source.
2. **Active search settings and evidence:** actual padding, clearance, grid,
   radius, bounds and diagnosis. Padding does not reduce clearance or authorize
   leaving the required ship spaces.
3. **Fixed connection check:** selected stubs, movable anchors and remaining
   source conflicts. A no-conflict result here does not establish port identity
   or a complete accepted route.
4. Download **Full result** and **Excel review and global coordinates**. In Excel,
   inspect **Geometric pieces**, **Fixed connections**, **Search settings** and
   **Export reconciliation**. Choose a component ID, compare its numeric XYZ,
   OD, length and bend radius/angle with the model. Bend centers and normals are
   in the same source global frame. They are geometric review pieces, not a
   verified commercial parts catalog.
5. Export reconciliation must agree. If a component ID, endpoint, OD or length
   total is inconsistent, the report explicitly withholds the affected green
   DXF geometry/part schedule. Keep that report as a failure; do not use an
   intermediate schedule for implementation.

For accepted geometry, run `MARITIMEDIMENSIONS` → On. Save As **in AutoCAD** to a
new DWG, e.g. `Real-Model-Review.dwg`. The report's 3D review DXF is a separate
export. Close/reopen the DWG and check `MARITIMEVIEW` → Both. Keep a screenshot
with dimensions, a second viewpoint showing clearance, and the report/Excel
files. Only check Undo on a separate copy.

## What closes this batch

Keep all three synthetic outcomes passing. Then demonstrate an accepted repair
on an organizer-provided pressure-piping drawing, including verified connection
assumptions and relevant surrounding geometry, and repeat the workflow on a
different drawing. An unchanged or blocked outcome on a second file is useful
behavior evidence but is not a successful complex repair. Record source hashes,
settings, outcomes and unresolved requirements rather than claiming universal
model support.

The brief's remaining catalog-fitting, pitched-system, full connectivity,
containment, support/access and installation gates remain explicit in
[requirement-acceptance-matrix.md](requirement-acceptance-matrix.md) and
[pitch-batch-acceptance.md](pitch-batch-acceptance.md). This release's clearer
reports and geometric proposals do not by themselves satisfy those gates.
