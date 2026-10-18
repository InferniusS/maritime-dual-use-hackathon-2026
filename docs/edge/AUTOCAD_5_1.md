# AutoCAD 2027: PipeForge Edge 5.1

The existing `PIPEFORGENETWORK`, `PIPEFORGECONNECTIONS`, `PIPEFORGEOPTIMIZE`, and `PIPEFORGEREVIEW` commands are preserved. This iteration extends measured progress and industrial-size selection without replacing source entities or weakening inspected-geometry binding. See the [5.0 native workflow](AUTOCAD_5.md) for complete approval, save/Undo, stale-drawing, and Windows acceptance procedures.

## Installation and execution

Use Windows, regular **AutoCAD 2027**, the **.NET 10 SDK**, and this branch's Python dependencies. From PowerShell in the repository root:

```powershell
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt
./.venv/Scripts/python.exe -m pip check
dotnet build autocad-plugin/Maritime.AutoCAD.csproj -c Release -p:AutoCADManagedDir="C:/Program Files/Autodesk/AutoCAD 2027"
./.venv/Scripts/python.exe scripts/run_web.py
```

Run `NETLOAD` and load `autocad-plugin/bin/Release/net10.0-windows/Maritime.AutoCAD.dll`. Retain normal AutoCAD security controls. Restart AutoCAD before replacing an already-loaded assembly. Without `AutoCADManagedDir`, the project uses its pinned `AutoCAD.NET` 26.0.0 compile-reference package. No Autodesk assemblies or prebuilt binary are committed.

1. Inspect authorized geometry with `PIPEFORGENETWORK`. Select source solids/blocks and declare units, endpoint tolerance, and permitted global WCS bounds. Surrounding geometry remains context. Native selection is bounded to 256 entities; the canonical graph to 128 spans and 256 nodes.
2. Review actual systems, ports, dimensions, catalogs, ship axes, and movement permissions in the companion. `PIPEFORGECONNECTIONS` requires explicit endpoint decisions. Layers, proximity, meshes, or cylindrical faces do not independently identify engineering systems, valves, tees, flanges, or reducers.
3. Run `PIPEFORGEOPTIMIZE`, select Multiple traced sources, Systems, or All reviewed spans, and choose an objective. The service must advertise `industrial_network_v51`; older servers are rejected. Limits remain 1–64 candidate evaluations and 1–120 seconds. Selections above 16 spans use bounded coordinated neighborhoods with fixed boundaries and complete-network acceptance checks.
4. The dialog displays actual phase, selected spans, completed/total neighborhoods, evaluated/max candidates, local solves, expanded states, peak grid nodes, and active span IDs when supplied. Elapsed time is measured locally. Missing/invalid counters remain absent; no percentage is fabricated. Cancel and closing the dialog request cooperative server cancellation.
5. Review failures, UNVERIFIED evidence, exact coordinates, and overlays. Approval defaults to No. Export downloads the existing eight-file bundle into a fresh folder. Source revision/binding checks run before preview, approval, every download, final completion, and persistent save. Optional Keep adds separate undoable review centerlines; source solids remain unchanged.

A timeout, exhausted evaluation budget, or unsuccessful neighborhood search proves neither global infeasibility nor optimality. Complete-network acceptance includes unchanged pipes and physical obstacles.

## CAD interpretation and provenance

The existing DXF/ACIS translator remains in use. It is not a native DWG/STEP parser or semantic plant-model database. AutoCAD supplies its local DXF snapshot; a supported local DXF uses the same translator.

Existing cylindrical/annular geometry, conservative analytic envelopes, source curves, and provisional torus interpretation are preserved. Source handles, block instance paths, world transforms, and declared unit scaling remain intact. Actual source layers now survive on axis/route candidates for designer review; they are provenance, not automatic system assignments.

**MESH, 3DFACE, SOLID, TRACE, polyface meshes, and polygon meshes** are retained as physical unknowns. Their complete transformed vertex bounds enter both route and network obstacle preparation. They never become inferred pipes or automatic valve/reducer identities. Below the display budget, source facets are rendered; above 20,000 preview vertices or 40,000 preview triangles, a labeled conservative whole-vertex box is retained instead of dropping the component. Collision preparation uses the complete vertex AABB. This approximation does not verify closed solids, component semantics, or installation feasibility.

Missing, recursive/deep, and unresolved externally referenced INSERT geometry has explicit unsupported-source counts. Unsupported ACIS faces, failed solids, incomplete extraction, and unresolved components remain coverage limitations. Open an authorized Xref source directly; absent geometry is not routing permission.

## Verification and pending native acceptance

The scale regression creates **40 actual synthetic ACIS cylinder source entities**, translates their DXF through the existing APIs, and explicitly confirms 39 joints into one connected graph. It submits all 40 selected spans through the native job protocol and verifies handles/topology/selection, measured progress, and unchanged source bytes. An already-straight synthetic network is not forced to produce an improvement.

Faceted regressions cover all six primitive families, rotated/nonuniform nested transforms, unit scaling, display-budget fallback, wall retention in both translators, layer provenance, and unresolved INSERT diagnostics. Execute:

```powershell
dotnet run --project tests/native/SourceBindingHarness.csproj -c Release
./.venv/Scripts/python.exe -m pytest -q tests/test_cad_facets.py tests/test_network_native_protocol.py
```

The console harness executes actual production geometry-binding and measured-progress helpers without Autodesk. **Native Windows AutoCAD execution remains unverified in Linux.** Cross-compilation, helper execution, DXF translation, and HTTP tests establish separate evidence.

Recorded verification: the complete AutoCAD 2027 project builds with **0 warnings and 0 errors** using .NET SDK 10.0.100 and AutoCAD.NET 26.0.0 references. The CAD/native regression group (`test_cad_facets`, `test_cad_import`, `test_cad_layout`, `test_cad_envelopes`, `test_cad_network`, `test_network_native_protocol`) passed **121 tests in 7.55 seconds**. The C# harness passed **20 source-binding checks and 9 measured-progress assertions**. Build, harness, and official SDK checksum evidence are recorded outside the repository in the local engineering artifacts.

Complete the earlier Windows checklist and additionally select at least 40 sources; match progress to server counters; cancel during a neighborhood search; verify handles, immutable geometry, and context; test stale/reopened drawings and copied-hash JSON tampering; inspect faceted/unknown-obstacle coverage; export the exact approved proposal; and verify separate overlays and Undo.

No new real CAD model was attached to the 5.1 request. Historical confidential drawings were not substituted. Real-model interpretation, semantic confirmation, engineering validation, and native hardware acceptance remain pending a newly supplied authorized model and a Windows AutoCAD run. Keep original models and generated real-CAD artifacts outside Git.
