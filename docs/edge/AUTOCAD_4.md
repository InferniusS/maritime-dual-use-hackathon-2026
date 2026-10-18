# PipeForge Edge 4.0 in regular AutoCAD

The connected-network commands are additive source changes to the team's AutoCAD 2027 plugin. Existing `MARITIMECAD`, `MARITIMEOPTIMIZE`, and `MARITIMEPREVIEW` are preserved. The older [2025/2026 Edge connector](AUTOCAD.md) is preserved separately.

The complete project cross-compiled successfully in Linux with the official .NET SDK 10.0.100 and pinned Autodesk `AutoCAD.NET` 26.0.0 reference package: **0 warnings and 0 errors**. All three C# source files also passed syntax-tree inspection and the project passed XML parsing. This extension has **not run inside AutoCAD** in this environment. No prebuilt DLL is included in this source snapshot. A source build and the Windows checks below are required before using these commands.

## Build and start

Use a synthetic test drawing first. Use confidential drawings only with authorization for local processing and keep generated geometry and exports out of Git. The service processes a local temporary DXF; the command does not send drawing contents to a public service.

On Windows with **regular AutoCAD 2027**, Python supported by the repository, and the .NET 10 SDK installed, run from the repository root in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
dotnet build autocad-plugin\Maritime.AutoCAD.csproj -c Release -p:AutoCADManagedDir="C:\Program Files\Autodesk\AutoCAD 2027"
.\.venv\Scripts\python.exe scripts\run_web.py
```

The existing project also supports building with its Autodesk reference NuGet package by omitting `AutoCADManagedDir`. Do not change the project target to guess compatibility with another AutoCAD release. Confirm the installed release first and use its supported SDK and runtime. This project targets Windows x64 and .NET 10; the earlier Edge connector has its own instructions.

In AutoCAD 2027, enter `NETLOAD` and select the newly built `autocad-plugin\bin\Release\net10.0-windows\Maritime.AutoCAD.dll`. Restart AutoCAD before loading a rebuilt DLL if the earlier assembly is already loaded. Keep normal AutoCAD trusted-folder controls enabled. The companion workspace is the `/connected` page on the same local service as the existing team workflow, port 8767 by default.

## Commands and data flow

| Command | Behavior |
| --- | --- |
| `PIPEFORGENETWORK` | Select 1–256 source solids or blocks, obtain their current handles, inspect the local snapshot, and save a canonical network JSON handoff. The connected engine limits the selected graph to 128 spans and 4,096 centerline points. Surrounding geometry remains context. |
| `PIPEFORGECONNECTIONS` | Load a handoff, display exact component coordinates, and review each ambiguous endpoint group as Connect, Separate, or Skip. Skip is the default. Save the reviewed JSON for the companion. |
| `PIPEFORGEREVIEW` | Load an approved exported `network-result.json`, check current drawing/revision/source handles, recompute independent validation using the local service, display properties and exact coordinates, and preview original/proposed centerlines. Keep is an explicit optional addition of separate review polylines. |

`PIPEFORGENETWORK` first asks whether local processing is authorized. It then asks for source units and six **global WCS millimetre** coordinates describing the permitted routing volume. These bounds are an explicit designer declaration; drawing extents are not used as routing permission. The handoff declares WCS but leaves ship fore-aft, athwartship, and up directions unresolved until a designer supplies them. Do not infer ship directions from screen orientation or arbitrary X/Y axes.

Solids and blocks must be selected directly in model space. Open referenced drawings directly; Xrefs are rejected. The snapshot limit is 48 MB. Arbitrary boxes are not converted into pipe centerlines. Unsupported geometry, missing system/port dimensions, nominal sizes, obstacles, installation access, and missing fitting catalogs remain visible limitations. Inspection does not certify a complete ship model.

The native session revision consists of a random session ID and an edit counter. Database object additions, modifications, and erasures invalidate the handoff. Closing/reopening the drawing or restarting AutoCAD creates a new session token and requires fresh inspection. Drawing fingerprint, revision, source handles, and instance paths are retained. The result's original-network digest must match the local service's independently computed reference digest. A stale result stops before preview or save.

An ambiguous connection is not automatically accepted from proximity. The native connection command explicitly confirms all endpoints in the displayed group at the first endpoint's exact position, within the network's existing tolerance. It does not enlarge tolerance. The service rejects inconsistent systems, movement of equipment ports, unknown endpoint IDs, or an invalid merge. Use the companion's JSON editor to select a subset, correct engineering metadata, declare actual valves/tees/equipment ports, and provide reviewed dimensions and permitted movement regions. A confirmed geometric junction still has UNVERIFIED fitting compatibility until its physical data is supplied.

## Review, implementation instructions, and Excel

1. Keep the inspected drawing open and unchanged while reviewing its network in the companion.
2. Load the handoff JSON. Correct system identities and equipment/valve ports; resolve ambiguous connections; provide nominal sizes, fitting catalog evidence, obstacle/coverage metadata, ship axes, and applicable constraints. Preserve drawing identity and revision.
3. Run coordinated optimization. Inspect the complete original and proposed networks, exact WCS coordinates, metrics, and every FAIL or UNVERIFIED check. A `NO IMPROVEMENT FOUND` result preserves the original; a failed search does not prove infeasibility.
4. Explicitly approve the current proposal's instruction export in the companion. This recomputes validation and checks the source digest and revision. Download `network-result.json`, `instructions.json`, `instructions.md`, `components.xlsx`, `components.csv`, and `network-preview.dxf` into the same authorized local folder.
5. Run `PIPEFORGEREVIEW`, choose `network-result.json`, and inspect the printed component properties and round-trip numeric WCS coordinates. The local validation response is checked against the original-network digest; service success is not a standards certificate.
6. Explicitly choose Yes to display overlays. Original spans are cyan, proposed spans green, and spans named by failed checks red. Source entities involved in failed checks use AutoCAD's normal highlight. UNVERIFIED findings and numerical evidence are printed and included in the reports.
7. Choose Clear to remove all temporary graphics, or Keep to add review centerlines on `PIPEFORGE4_ORIGINAL` and `PIPEFORGE4_PROPOSED`. Keep adds one undoable transaction. It preserves the original solids and stores span/source/proposal metadata on each review polyline. Reinspect afterward because this drawing addition changes the revision.
8. Open the exported `components.xlsx` and implementation instructions. Match every source CAD handle, instance path, fixed connection, global coordinate, piece, bend radius, fitting specification, and unresolved issue before manually editing the CAD assembly. The plugin prints paths of adjacent bundle files that exist.

Review centerlines are not replacement pipe solids. This extension does not provide native physical fitting insertion, automatic source replacement, or a commercial/shipyard approval. Original authoritative Fincantieri PDFs and approved component catalogs must be reviewed by the responsible engineer. The versioned profile is the user's supplied transcription; ambiguous requirements remain UNVERIFIED.

## Windows acceptance checklist

Record the installed AutoCAD release/build, SDK version, .NET build output, Python version, command transcript, and source/result hashes using synthetic fixtures. Do not attach confidential geometry to a public issue.

1. Build successfully against the installed Autodesk 2027 assemblies. Confirm all six earlier/new commands load. Run the earlier synthetic line workflow to check regression behavior.
2. Create two or three synthetic cylindrical pipe spans with separate source handles. For a tee, create three actual selected sources; for a gap, leave two endpoints within the declared tolerance without declaring a connection. Run `PIPEFORGENETWORK`; confirm selected source handles and instance paths survive the handoff.
3. Confirm coordinates and scale in a millimetre drawing and repeat in a metre/inch drawing. Enter routing-volume bounds in WCS millimetres in all cases. Verify a rotated UCS does not change the handoff's WCS coordinates.
4. Run `PIPEFORGECONNECTIONS`. Skip must leave the connection unresolved. Separate must preserve disconnection. Connect must require an explicit decision and retain endpoint source identity. Cross-system and out-of-tolerance joins must be rejected by the service.
5. In the companion, supply reviewed synthetic engineering metadata and optimize the connected graph. Export the reviewed bundle and verify spreadsheet/global coordinate agreement with the synthetic CAD sources.
6. Preview the exported result. Check original/proposed overlays, red failed spans, normal source highlighting, numerical findings, and exact property output. Clear must leave no persistent review entities.
7. Choose Keep. Confirm only the two review layers receive new centerlines, the original source entities remain, and `UNDO` removes the additions. Preview alone must not create source edits.
8. Before preview, move/erase any source, add an unrelated drawing object, save/reopen, or change drawings. Each old bundle must be rejected. Reinspect to obtain a fresh revision, then verify the fresh bundle is accepted.
9. Cancel the unit/bounds/file/confirmation prompts; simulate a stopped service and a malformed/oversized result. Confirm temporary DXF cleanup, no original entity edits, and no partial persistent overlay transaction.
10. Repeat the full test with the actual installed hardware and approved authorized drawing. Record the result separately; Linux cross-compilation and HTTP protocol tests are not native runtime evidence.
