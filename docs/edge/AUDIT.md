# PipeForge Edge audit

The recoverable starting checkpoint is `bc6ad84855122f7d810c3af51e8fac1ca950a0d8` on `work`. All **87** existing Python tests passed before Edge changes. The previous application, deterministic routing, independent validator, hydraulics, eight scenarios, Three.js workspace and JSON/DXF/CSV/report/optional STEP exports remain the foundation.

The integrated Edge development checkpoint is `95e19bd` on `work`. Final refinement and verification follow that checkpoint without discarding the baseline.

The supplied request describes a Windows AutoCAD laptop and a physically available Qualcomm Dragonwing IQ-9075. This Linux development instance has no attached AutoCAD installation, Autodesk managed assemblies or authenticated access to that physical device. Their existence in the user's environment does not establish access from this instance.

The native target is regular AutoCAD, not a Plant 3D semantic connector. The workstation's exact release remains unknown. The supplied project targets Windows x64 AutoCAD 2025/2026 with .NET 8 and must be built against the actual host assemblies. Source parsing is useful preparation but does not establish compilation or host compatibility.

The implementation priorities are a working local engineering workflow, explicit model revisions, supported CAD file exchange, precise identity mapping and safe review. Native integration and remote computation use adapters so that neither hardware is required to use the engineering engine.

The main risks identified during inspection were:

- Existing asynchronous results referenced their submitted model but had no durable drawing revision. A late result could otherwise be displayed against newer geometry.
- The optimization output was a route proposal, but export was not governed by a durable engineer decision.
- Full analysis visits every route. Incremental reuse must include surrounding systems, added and removed geometry, configured clearance, and the original route itself; a cache keyed only by the edited pipe would miss new collisions.
- Ordinary CAD centerlines and solids do not establish pipe specification, connection topology, hydraulic properties or commercial fitting approval. Missing semantics must remain explicit.
- CAD coordinates can use nonmeter units. Canonical engineering dimensions and validation remain in meters; declared source-unit conversion and inverse export instructions must preserve the source coordinate scale. Unitless inputs cannot be guessed.
- A browser that creates a separate copy cannot represent the native connector's live drawing. It must attach to the same session ID, preserve CAD ownership of source edits and require an explicit viewport refresh after external revisions.
- A remote worker must preserve request identity and revision and must return actual computed measurements. A plausible response is insufficient; local deterministic verification is required before presenting geometry as validated.
- A local CPU benchmark cannot establish Windows performance, ARM64 binary compatibility, network offload gains, GPU execution or NPU inference.

Analysis, optimization, approval and export must preserve the source snapshot. Any explicit application action changes only the revisioned PipeForge session; it does not overwrite a DWG file. Native CAD application remains a separate engineer action in AutoCAD.

The independent session tests are in [test_edge_sessions.py](../../tests/test_edge_sessions.py). They exercise the public HTTP contract with a fresh SQLite database rather than relying on the UI's behavior. Final executed results and remaining limits are recorded in [STATUS.md](STATUS.md).
