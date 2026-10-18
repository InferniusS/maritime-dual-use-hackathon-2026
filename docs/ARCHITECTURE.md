# Architecture

PIPEFORGE separates engineering data, routing search, independent validation, CAD exchange, and visualization. The backend is a local Python/FastAPI service; the frontend consumes structured engineering models and numerical results. The core workflow requires no external AI service.

## Data and calculation flow

1. Load a procedural scenario or import an engineering model through a supported adapter.
2. Parse typed models with explicit metre units, stable IDs, pipe dimensions and source coordinates.
3. Analyze physical geometry, drainage rules and available metadata.
4. Search alternative centerlines while treating fixed structures and every other pipe as occupied space.
5. Independently validate the proposed model against the original model.
6. Compare calculated geometry/hydraulics and inspect the proposal in the workspace.
7. Export geometry and implementation instructions for designer review.

`backend/models.py` defines the validated exchange model. `geometry.py` supplies segment distances and route measurements. `scenarios.py` supplies deterministic development cases. `hydraulics.py` calculates pressure-loss estimates when sufficient operating data exists. Routing and validation are separate modules so an optimizer result is not trusted merely because search returned it.

Finite straight pipe envelopes are represented by capsules around centerlines. Axis-aligned obstacle boxes are checked using segment-to-box distances; other pipes use segment-to-segment distance and the sum of external radii. This representation is stronger than centerline intersection testing, but does not model the detailed swept shape of manufactured fittings.

## Verification boundaries

The automated suite checks geometric reference cases, finite-radius separation, preservation constraints, scenario outcomes, deterministic calculation, hydraulic references, API exchange and supported CAD roundtrips. A passed test suite establishes behavior for its tested models. It does not establish compliance with unspecified shipbuilding standards or certification of the underlying installation.

See [engineering assumptions](ENGINEERING_ASSUMPTIONS.md) for the model boundaries and missing source material. Installation, exact endpoints and commands are documented in the repository README.

## Optimization jobs and cancellation

`POST /api/jobs` accepts the same typed body as `/api/optimize` and returns an ID. Poll `GET /api/jobs/{id}` for `status`, the actual expanded-state count, `result` and `error`. `DELETE /api/jobs/{id}` requests cooperative cancellation; poll until the terminal `cancelled` state rather than interpreting the request acknowledgment as completion. The local runner permits two active jobs and retains a bounded in-memory history. Jobs are not durable across process restarts.

The optimizer compares feasible alternatives with the original route under the selected normalized objective. A valid original can be retained when replacing it does not improve that objective. Improving a preference does not override hard engineering constraints, and an invalid original does not become acceptable merely because it has a lower objective value.

## Version 2 extensions

`catalog.py` centralizes angle/tolerance, diameter/specification and connection/tangent compatibility. Hydraulics, validation, CAD instructions and search use the same selection rule. Empty applicability metadata remains explicitly unverified. `evidence.py` returns measured capsule/box and capsule/capsule surface clearances, including conservative elbow enclosures. Geometric witness helpers place issue markers near actual closest points.

`gravity.py` searches a two-dimensional projected graph with incoming direction, available tangent run and cumulative horizontal travel in its state. The permitted travel budget comes from endpoint signed elevation change divided by required pitch. Candidate XY paths are lifted using uniform fall/rise to exact endpoint elevations, then independently validated in 3D. Projection conservatively blocks the full endpoint elevation slab; it cannot exploit over/under corridors or claim completeness. Configured vent pitch may rise; gravity drainage may only fall.

`spatial.py` uses a cKDTree over obstacle bounding spheres for broad-phase queries. Bounding spheres enclose every box/pipe envelope, and exact distance checks remain the final narrow phase. A cached point-to-original-segment distance reduces repeated modification-cost calculations. Heap entries retain queued costs to skip stale expansions.

`advanced.py` explores objective modes, coarse/fine resolutions and at most two selected-system routing orders. Bounded local shortcuts are accepted only after fresh validation and objective comparison. Every proposal uses the same original baseline; final cross-system checks run after sequential changes. Duplicate geometry is removed. Nondominance is calculated only among explored, independently valid alternatives using length, bends, modified-pipe count, normalized modification distance and available full/partial loss estimates. It is an approximate explored set, not a true global Pareto frontier. Metrics and deterministic explanations retain unfavorable tradeoffs and unknown operating data.

The jobs API retains existing compatibility and adds `alternatives`, `multiresolution`, `try_orders` flags. Progress counts expanded states cumulatively across systems/attempts. Cancellation remains cooperative. `/api/analyze` accepts both legacy raw models and `{model,clearance}`. `/api/catalog/validate`, `/api/validate`, and `/api/capabilities` expose typed catalog checks, independent evidence and optional CAD availability.

`step_adapter.py` launches `step_worker.py` in a separate pinned OpenCascade interpreter. The main runtime imports no CAD kernel. STEPCAF writes AP214 named pipe/obstacle components with explicit meter units; reference reimport checks coordinates, solid count and known bore volume. It does not export detailed fittings or parametric manufacturing history. The JSON change report remains the complete engineering semantic model.

`EngineeringPanel.tsx` adds actual candidate selection, explored-frontier plotting, network evidence, deterministic rationale, route-vertex measurement, catalog import and what-if restrictions. The existing viewer remains intact. System isolation affects rendering only; optimization always receives every system. New constraint baselines invalidate previous proposals, and repeated optimization uses the preserved source baseline rather than silently rebasing on its last proposal. Runtime font loading has been removed.


## Edge 3.2 source and execution boundaries

The existing engine is shared by revisioned CAD sessions (`edge_store.py`/`edge_api.py`) and the optional CPU worker (`edge_remote.py`/`edge_worker.py`). `extraction.py` carries declared CAD scope separately from the canonical meter-based geometry. `fitting_geometry.py` supplies angle-bounded, symmetric fitting occupancy used by search, evidence and independent validation. Serialized missing-input provenance keeps illustrative dimensions and coefficients from becoming authoritative after a JSON/cache roundtrip.

`work_limits.py` provides bounded request-scoped computation and output budgets. Independent local validation remains required for remote proposals. A source coverage generation can invalidate results without a modeled-coordinate change. Database writes check revision, cancellation and deadline before publication; native-owned session application requires connector-confirmed geometry rather than a virtual model replacement. Exact pre-3.2 session snapshots are archived before old assurance is qualified.

See [3.2 hardening](edge/V3_2.md), [native contracts](edge/AUTOCAD.md) and [worker access/limits](edge/REMOTE.md). Network topology, raw entity interpretation and supplied-standard rule ingestion remain distinct incomplete contracts, not inferred facts.
