# PipeForge 2.0 implementation and verification

This extends the committed functional v1 baseline; it does not certify ship piping or establish manufacturing approval. [Audit and baseline evidence](v2/AUDIT.md) record the starting implementation.

| Capability | Implemented scope | Important limits |
| --- | --- | --- |
| Pressure routing | Existing direction/straight-run A*, stale-state pruning, cached cost geometry, cKDTree broad phase, analytic finite-envelope checks | Bounded graph; conservative elbow spheres; no continuous global optimum |
| Gravity/vent routing | Actual obstructed lateral detours with travel/pitch state, uniform signed lift, fixed endpoint elevations, independently checked segment pitch | Conservative full-elevation-slab projection; no variable-pitch/over-under/branch gravity solution; no drainage capacity model |
| Catalog | Typed import, angle/tolerance/radius, nominal sizes, pipe specifications, connection length, tangent allowance, K, approval/provenance metadata | Development entries unapproved; supplied approval is metadata, not independent supplier/class verification |
| Alternatives | Five objectives, distinct geometry, two-grid exploration and bounded validated shortcuts, selectable proposals, empirical nondominated set | Not a complete Pareto frontier; grids are independent, not adaptive corridor refinement |
| Multiple systems | Finite occupancy of all pipes and boxes, multiple editable routes, forward/reverse order comparison, final-system checks, network objective | At most two orders; no global coordination/order permutation proof or tee synthesis |
| Validation | Endpoints, membership, dimensions, fixed environment, catalog, pitch, collisions, numerical separation evidence, canonical statuses, assurance levels | Organizer standards, nozzle topology/orientation, supports, stress, thermal/installation access remain unverified |
| Recommendations | Deterministic geometry/validation-derived tradeoffs, unchanged connections/dimensions, unresolved requirements | No opaque AI-generated claims; no fabrication approval |
| Workspace | Existing 3D viewer, real radii, issue witnesses, overlays, orthographic views, system isolation, selectable alternatives/chart, vertex measurements | No manufactured rounded elbows, arbitrary scene picking ruler, or nozzle assembly editor |
| What-if | Clearance/objectives, per-system pitch, catalog import, editable non-equipment boxes, new restricted boxes; typed baseline validation and stale-result invalidation | Fixed equipment remains protected; no drag manipulator or undo history |
| CAD | Typed JSON, precise CSV/text change instructions, DXF current/original centerlines and obstacle faces, XDATA consistency, optional named STEP pipe/box solids and known bores | No native DWG, STEP import, STL, direct AutoCAD/Navisworks execution, detailed fitting solids or parametric fabrication history |
| Performance | Cached modification distance, exact narrow-phase checks after spatial candidates, bounded cancellable jobs; real benchmark/profile artifacts | Python traced memory excludes native allocations; observations depend on host/load |

## Executed verification

- **87 automated tests passed** in the prepared instance, including the optional OpenCascade STEP tests. The initial 58-test baseline passed before changes. Existing functional assertions were retained; the fixture inventory assertion was extended to include three new scenarios.
- Tests cover numerical distance references, spatial broad-phase conservatism, nearly touching/insufficient space, finite pipe body clashes, reverse/intermediate slopes, impossible endpoint pitch, compatible/unsupported fittings, size/metadata/membership preservation, alternatives/nondominance, route order comparisons, cumulative progress, cancellation, catalog/API validation, changed-segment instructions and CAD roundtrips.
- STEP reimport checks named products, meter scale, bounds, solid count and analytic known-bore volume. The first unit-scaling failure was diagnosed and corrected before claiming support.
- Production TypeScript/Vite build passed. A nonblocking Three.js bundle-size warning remains.
- Actual Chromium tests passed all eight fixtures plus distinct candidate selection, orthographic/system filtering, point-to-point measurement, what-if recomputation and STEP download/reimport; no JavaScript exceptions.
- Pinned core and isolated CAD setup scripts are exercised in this environment; no external API credentials are required.
- One upstream Starlette/httpx test-client deprecation warning remains. No tests were disabled to conceal failures. Without the optional kernel, two STEP tests explicitly skip and the main application remains operational.

The canonical validation status is `PASS`, `FAIL`, `UNVERIFIED` or `NOT_APPLICABLE`. `status_code` matches it; `legacy_status` provides the previous display spelling. Assurance levels separate geometric validity, implemented constraints and unverified supplied standards; engineering review is always required.

Actual profiles and benchmark results: [v1 profile](v2/baseline-profile.txt), [v2 scenario measurements](v2/benchmark.json), [controlled v1/v2 observations](v2/comparison.json). Run `scripts/benchmark.py` and `scripts/compare_baseline.py` to regenerate measurements; the latter reads a Git archive into a temporary directory, never resets or checks out application files.

No official vessel CAD, organizer standards or approved catalog was supplied. Official challenge acceptance, full constructability and Autodesk compatibility remain unverified. Read [engineering assumptions](ENGINEERING_ASSUMPTIONS.md) before interpreting any PASS result.
