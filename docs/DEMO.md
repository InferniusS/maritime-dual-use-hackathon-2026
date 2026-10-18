# PipeForge 2.0: reproducible 90-second demonstration

Run `scripts/setup.sh`, optionally `scripts/setup-cad.sh`, then `scripts/start.sh`. Keep the single server running. All calculations use the current engineering model; no optimized outcomes are stored in fixtures.

| Time | Action | Evidence |
| --- | --- | --- |
| 0–15 s | Load **Congested machinery**. Orbit, show the original route, run analysis. | Real equipment clash marked at a computed centerline witness; other systems occupy finite volume. |
| 15–30 s | Enable **Compare five objective modes** and **Explore coarse + fine grids**, then optimize. | Live cumulative search progress and cancellation control; independently validated distinct geometry. |
| 30–45 s | Select alternative cards. Inspect the explored-frontier chart and numerical evidence. | Actual length/bend/modification/loss tradeoffs; safe route may be longer than the clashing original. Approximate nondominance is labeled. |
| 45–60 s | Load **Obstructed gravity drain**, optimize, inspect coordinates/pitch/catalog. | Computed tank detour, fixed elevations, minimum pitch on every segment, explicitly unapproved illustrative fitting tolerance. |
| 60–75 s | Open validation and measured constraint evidence. Isolate a system or use Ortho/Top. | Dimensions and endpoints retained; measured versus required clearances; official standards remain UNVERIFIED. |
| 75–90 s | Export JSON instructions plus DXF or optional STEP. | Exact added/removed segments, source coordinates, dimensions and named CAD geometry; not construction approval. |

For live what-if exploration, add a restricted box with explicit meter coordinates or increase required clearance, then rerun. Each edit establishes a new model baseline and invalidates old results. Fixed equipment and pipe sizes remain protected. The browser regression exercises an actual zone change and recomputes its proposal.

Additional checks: **Rising vent detour** demonstrates configured upward pitch, **Coordinated services** compares routing orders for two editable systems, **Sealed bulkhead** reports no route found, and **Already efficient** retains an equally good original.

The official vessel CAD/standards/catalog have not been supplied. These fixtures demonstrate implemented behavior, not official challenge acceptance. Replace them with reviewed engineering inputs for the organizer demonstration. Open exported files in Autodesk software when available; kernel/DXF roundtrips are not Autodesk interoperability certification.
