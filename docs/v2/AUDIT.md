# PipeForge 2.0 engineering audit

Baseline: `afb9626befed67be329e58e502ead1aafff97fd6` on `work`, initially clean. All 58 existing tests executed and passed before changes. Actual baseline scenario outputs and a cProfile trace are retained beside this document.

Verified existing subsystems: typed SI model, direction/straight-run A*, finite capsule/box and capsule/capsule geometry, independent preservation/slope/catalog validator, conditional Darcy-Weisbach hydraulics, JSON/CSV/report/DXF adapters, asynchronous jobs, five fixtures and React/Three workspace.

Findings driving implementation:

- Gravity systems stopped after testing only a direct connector. A monotone detour search is needed, with per-segment pitch, fixed elevations and explicit fitting applicability.
- Fitting entries lacked compatible sizes/specifications, tangent/connection dimensions and declared angular tolerances. Unapproved development entries must remain unverified.
- Search repeatedly calculated the same original-route modification distances for each incoming-direction state. cProfile attributes most congested search time to segment distance calls. Cache by spatial node and skip the calculation when its weight is zero.
- The heap could re-expand stale state entries. Retain queued costs and discard stale entries.
- There was one selected proposal and no candidate frontier/order comparison. Every alternative needs the same original comparison baseline and independent final-system checks.
- Validation had grouped booleans with little numerical evidence. Add measured surface clearances, object references, required values, canonical status codes and explicit coverage.
- Analysis UI sent the model without its selected clearance, silently using 0.1 m. Preserve legacy API input while supporting explicit analysis clearance.
- DXF held only current centerlines. Add original modification layers and equipment/structure box faces, retain identities/units and check reimport.
- Hydraulic K lookup relied on angle alone; search used the first catalog item's K. Select the actually compatible angle/size/specification entry consistently.
- Frontend was a large single module and used remote fonts. Add focused components for candidate evidence/what-if tools and remove network-dependent fonts rather than rewriting the existing viewer.

Exact straight-envelope distance tests are retained. Elbows remain conservative enclosing spheres, not exact manufactured solids. No certification or official standard approval can be inferred. Optional STEP dependencies must be isolated from the main environment.
