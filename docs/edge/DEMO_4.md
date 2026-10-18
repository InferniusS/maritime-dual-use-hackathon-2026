# PipeForge Edge 4.0: 90-second connected-network demonstration

Use the synthetic connected-network scenarios for this demonstration. Confidential organizer drawings and derived geometry require separate authorization. AutoCAD commands need the Windows verification in [AUTOCAD_4.md](AUTOCAD_4.md); a browser demonstration does not establish native execution.

Start the team's local service from the repository root:

```sh
.venv/bin/python scripts/run_web.py
```

On Windows, use `.\.venv\Scripts\python.exe scripts\run_web.py` after installing the checked dependencies. Open `/connected` on the running application's address. The same page is served by the preserved Edge service. Do not replace an existing native session with a fixture.

Before presenting, calculate scenarios A–F once, inspect the actual validation, and retain the current result packages. Confirm that any selected proposal is bound to the current drawing revision. Use the measured outcomes from that run, including remaining UNVERIFIED checks. The demonstration must not imply that provisional synthetic catalog entries are commercially approved components.

| Time | Action | Evidence to show |
| --- | --- | --- |
| 0–12 s | Choose scenario C and select **Load scenario**. | Stable node/span IDs, fixed equipment ports, valve movement limits and system identifiers. Coordinates are global WCS millimeters. |
| 12–25 s | Inspect topology and unresolved connection controls. | Connectivity is explicit. Proximity candidates require a designer decision; no unseen automatic join is treated as engineering truth. |
| 25–43 s | Select **Optimize connected network**. | Actual elapsed time, changed spans and before/after network metrics. Required connectivity and surrounding systems remain constraints. |
| 43–61 s | Compare the original/proposed 3D overlay and select a component. | Equal-scale geometry, component IDs, exact coordinates, dimensions, fixed ports and structural context. Preview leaves source objects unchanged. |
| 61–76 s | Open full-network validation. | Numeric clearance/slope/bend/manufacturing evidence, hard requirements versus preferences, and remaining UNVERIFIED support/catalog/installation checks. |
| 76–90 s | Check the current-revision review approval and select **Generate reviewed CAD instructions and Excel schedule**. | Download `components.xlsx`, `instructions.json`/`.md` and `network-preview.dxf`. Show CAD identities, original/proposed coordinates, manufactured pieces and fitting notes. |

The six scenarios exercise different obligations: A preserves an already-good route; B repairs a collision; C changes coordinated connected spans; D reports an infeasible route; E checks interaction with a second system; F preserves directional drainage pitch. A shorter route is not automatically an acceptable route. Collision repair may increase length, and missing engineering information must stay visible.

After the main demonstration, load A and D to show **NO IMPROVEMENT FOUND** and explicit blocked-route evidence. For an authorized imported snapshot, use its CAD snapshot ID with **Reconstruct imported CAD network** and resolve the resulting topology before seeking a proposal. The editable JSON shows exact values; editing it invalidates any earlier review package.

For a Windows CAD demonstration, first run the synthetic native acceptance steps in [AUTOCAD_4.md](AUTOCAD_4.md). Select or import the current network with its drawing identity/revision and source handle map. Review a fresh proposal as separate overlay geometry, inspect exact component coordinates and open the generated schedule/instructions. Deliberately edit a source entity and verify that the old proposal is rejected as stale. Do not claim source-solid replacement or Undo behavior until those steps have succeeded in the actual host.

If optimization cannot establish a valid improvement, show its measured failure or unresolved reason. Keep the original route and explain what additional catalog, geometry, topology or standard authority is required. This is an intended engineering outcome.
