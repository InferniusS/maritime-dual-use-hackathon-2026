# PipeForge Edge 5.0 engineering deliverables

The connected-network workflow extends the existing revision-bound instruction bundle. It retains source handles, system IDs, connection IDs, drawing revision, source digest, original geometry and proposed geometry. Every coordinate is in global WCS millimetres with an identity source-to-export transform. A bundle never approves replacement of native CAD objects or releases a design for fabrication.

## Generate and review a bundle

Run the existing local application, open its **Connected network** workspace, load an authorized network, review its engineering metadata, and calculate the proposal. The workspace's explicit approval/export step uses a server-held proposal and checks the active source revision and digest. A source edit invalidates the earlier proposal. A downloaded result dictionary cannot replace the server-held approved geometry.

The export function independently normalizes and checks the source and delivered networks. It ignores supplied metric and validation claims, recomputes both sets of metrics using the same declared objective configuration and immutable source normalization, and rejects a proposed network with a hard validation failure. When a selection scope is supplied, the original and proposed validations both enforce that scope; a physically clear modification to an unselected span is rejected. Legacy results without selection metadata remain supported. Diagnostic bundles for a preserved original network retain their FAIL and UNVERIFIED evidence.

| File | Contents |
|---|---|
| `instructions.json` | Exact original/proposed centerlines, CAD/source identity, dimensions, removed/added segments, fixed connections, physical fitting requirements, manufacturing pieces, weld coordinates, original/final validation, per-system changes, candidate evidence, confidence categories and implementation gates. |
| `instructions.md` | Human-readable calculation summary, per-system comparison, exact route coordinates, deterministic explanations, coordinated implementation sequence and unresolved decisions. |
| `components.xlsx` | Components, Coordinates, Manufacturing, Checks, Baseline checks, Changes, Systems, Welds, Candidates, Installation and Metadata sheets. |
| `components.csv` | Component/fitting/piece/weld requirements, quantities, exact global positions where known and verification notes. |
| `coordinates.csv` | Every original and proposed centerline point, associated span, source handle, system, drawing revision and exact global XYZ. |
| `network-preview.dxf` | Separate original/proposed WCS 3D centerlines, physical fitting centerline arcs sampled as conservative previews, fixed ports, context envelopes and measured segment labels. Units are declared millimetres. |
| `network-comparison.svg` | Portable side-by-side isometric before/after view. System colors and line weights distinguish systems and changed/untouched routes. Labels show calculated 3D segment lengths, rounded only for display. No scripts or external resources. |
| `network-result.json` | Source/proposal, recalculated network metrics, complete validation/manufacturing evidence, export round-trip verification and added delivery audits. |

The existing `pipeforge.instructions/4.0` schema identifier is retained for additive compatibility; `product_version` identifies the 5.0 deliverable. Existing six filenames and original spreadsheet sheets remain available.

## What the calculations establish

`network_audit` derives changed and untouched spans from actual geometry rather than trusting a reported changed-ID list. It gives each span's original/proposed endpoints, segment dimensions, length and bend differences, validation references and deterministic rationale. System totals include untouched routes. Moved nodes include actual displacement and incident spans so the designer can review a coordinated change as one proposal.

Network geometric lengths are routing-polyline lengths through tangent intersections. Physical developed lengths, including circular bends, are reported separately in the manufacturing schedule and are not interchangeable with the routing objective. Original and final networks use the same independent engineering criteria. The final report separately records PASS, FAIL, UNVERIFIED and NOT_APPLICABLE checks with numerical evidence. It does not turn missing metadata into industrial approval. A component's apparent geometric improvement does not remove unresolved network-wide checks.

The Candidates sheet and JSON comparison distinguish two evidence categories:

- **Independently recomputed:** original and delivered geometries, whose metrics and validation are calculated again during export.
- **Optimizer-recorded attempt:** intermediate candidate scores, routing order, selected/accepted flags and recorded failure counts. The optimizer validates its candidates through the independent validator before acceptance; the exporter does not receive every discarded candidate's full geometry and does not claim to revalidate those geometries.

Scores remain dimensionless with source-based scales and explicit term contributions. Missing hydraulic inputs remain UNVERIFIED. Modification and constructability terms are declared geometric proxies, not prices or measured labor. The comparison does not establish global optimality.

## Fabrication and installation boundaries

Manufacturing piece lengths include their constituent physical bend lengths. Elbow rows express fitting requirements and must not be added again as extra pipe length. Weld stations and global positions are calculated on the developed physical centerline; a PASS for the weld coordinate proves only that mapping. Weld spacing, process, material, catalog availability and commercial approval remain separate evidence.

The installation sequence requires source/revision verification, review of all coordinated changes and untouched surroundings, resolution of applicable FAIL/UNVERIFIED items, an approved fabrication plan, separate CAD preview, explicit designer approval and validation after authorized implementation. Collision checks alone do not establish tool access, lifting/transport clearance or installation sequencing. No unknown part, wall thickness, material, fitting availability or weld process is invented.

The SVG is a centerline comparison, not a manufacturing drawing. Context and fitting-envelope assessment require the DXF and validation evidence. At most 256 dimension labels appear in the SVG; all route coordinates remain in the complete JSON/XLSX/CSV schedules. XLSX cells exceeding the format's 32,767-character limit carry an explicit truncation notice; full engineering evidence remains in JSON.

## Serialization safeguards and verification

Before exposing a completed bundle, the exporter reads JSON, XLSX, coordinate CSV, component CSV and DXF back from disk, checks source/CAD identities and route counts, and compares global coordinates to a tolerance of `0.000001 mm`. A serialization PASS does not erase an UNVERIFIED source frame or source-unit provenance check. All files are prepared in a temporary directory before publication to the local export folder.

XLSX textual IDs are explicitly stored as strings. CSV cells that could execute as spreadsheet formulas receive a protective apostrophe; exact engineering IDs remain in JSON and XLSX. SVG text is XML-escaped and contains no executable code or remote resources.

Run the synthetic regression checks from the repository root:

```bash
python -m pytest -q tests/test_network_exports.py tests/test_network_exports_v5.py
```

The tests calculate multi-system changed/untouched totals, normalized tradeoffs, full validation references, exact coordinates, physical weld positions, three-dimensional dimensions, stale-metadata rejection and spreadsheet/SVG input safety. They use synthetic geometry only. Native AutoCAD runtime behavior requires separate Windows/AutoCAD testing.
