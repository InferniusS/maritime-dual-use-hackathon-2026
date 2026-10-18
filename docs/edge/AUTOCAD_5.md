# PipeForge Edge 5.0 native AutoCAD workflow

The existing AutoCAD 2027 plugin now contains an additive `PIPEFORGEOPTIMIZE` command. It runs the same coordinated network optimizer and independent validator used by the companion. It does not import the unrelated implementation on `main`, replace the preserved commands, or modify original pipe solids.

## Verified build and verification boundary

The complete source project cross-compiled on Linux with official .NET SDK **10.0.100**, Windows desktop reference assemblies, and the pinned **AutoCAD.NET 26.0.0** compile-reference package: **0 warnings and 0 errors**. The SDK archive SHA-512 matched Microsoft's official release metadata. The project targets `net10.0-windows` and x64; no compiled DLL is committed.

Synthetic HTTP protocol tests exercise actual DXF inspection, multi-source handoff, source-instance retention, explicit connection confirmation, asynchronous job settings, retention of an unselected system, server-side cancellation, revision-bound approval, and the exported bundle: `python -m pytest -q tests/test_network_native_protocol.py` passed **4 tests**. They do not execute Autodesk assemblies.

The actual production source-binding helper also runs independently of Autodesk: `dotnet run --project tests/native/SourceBindingHarness.csproj -c Release` passed **20 checks** covering editable-baseline tampering, source/diameter/unit substitutions, removed/resized obstacles, copied hashes, enlarged bounds/tolerances, immutable extraction evidence, and permitted metadata/topology reviews. This is a .NET helper test, not native AutoCAD execution.

**Native execution in AutoCAD has not been verified in this Linux environment.** Successful compilation and HTTP tests do not establish viewport behavior, command cancellation behavior, modal-dialog behavior, save/Undo integration, or installed hardware compatibility. Complete the Windows checklist below before project use.

## Build and start on Windows

Use regular AutoCAD **2027**, Python supported by the project, and the .NET 10 SDK. From a clone of `pipeforge-edge-v4`, run PowerShell commands at the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
dotnet build autocad-plugin\Maritime.AutoCAD.csproj -c Release -p:AutoCADManagedDir="C:\Program Files\Autodesk\AutoCAD 2027"
.\.venv\Scripts\python.exe scripts\run_web.py
```

Run `NETLOAD` in AutoCAD and select `autocad-plugin\bin\Release\net10.0-windows\Maritime.AutoCAD.dll`. Keep normal trusted-folder controls. Restart AutoCAD before loading a rebuilt assembly. The plugin communicates with the existing local Python service on the same computer; the browser is optional for native optimization, but remains useful for engineering metadata and topology review. Do not guess compatibility with an unspecified AutoCAD version; the separate legacy connector retains its own supported releases.

## End-to-end engineering workflow

1. Open a synthetic drawing first. Run `PIPEFORGENETWORK`, authorize local processing, and select the source solids or blocks. The command exports surrounding drawing context too; source entities remain read-only. It asks for source units, an explicit endpoint-review tolerance greater than zero and at most 25 mm, and permitted routing bounds in global WCS millimetres. Sampled drawing extents do not establish routing permission. It preserves source handles, source instances, drawing fingerprint, revision, and snapshot identity.
2. Review the JSON in the connected companion. Confirm actual system and port identities, nominal sizes, physical fittings, permitted movements, engineering metadata, and ship axes. Run `PIPEFORGECONNECTIONS` or the companion to decide each ambiguous endpoint connection explicitly. Proximity is never semantic proof. Unknown solids remain conservative obstacles or coverage uncertainties; missing metadata does not become a fabricated engineering default.
3. Keep the drawing open and unchanged. Run `PIPEFORGEOPTIMIZE` and choose the reviewed network JSON. Choose **Multiple** to select traced pipe solids/blocks in the drawing, **Systems** to choose one or more displayed system numbers, or **All** for every span in the reviewed network. All refers to the declared reviewed graph, not an assertion that every pipe in an arbitrary drawing was understood. The bounded optimizer currently permits at most **16 modifiable selected spans** in a calculation, while validating the complete supplied graph of up to 128 spans. Larger selections return a declared search-limit result and preserve the original; choose an affected subnetwork while retaining all context. Unselected spans and surrounding obstacles remain in the complete network and validation. An unmatched selected source causes the command to stop rather than silently omit it.
4. Choose Balanced, Length, Bends, Hydraulic, Changes, or Feasibility. Set 1–64 coordinated candidate evaluations and a 1–120 second search budget. The bounded search provides heuristic alternatives; it does not claim global optimality. Hydraulic objectives require supplied fluid and component evidence; absent evidence remains UNVERIFIED. The native command requires the service's `network_optimization_v5` capability; an older service cannot silently ignore selection or optimization settings.
5. Watch the native progress window. It reports queued/running state and actual elapsed time. **Cancel calculation** or the close button requests cooperative cancellation through the local job API; it does not simply hide a running result. A cancelled job never yields an approved export. Source geometry is never edited by a calculation.
6. Review the returned measured failures, unverified checks, exact coordinates, and calculated status. Optionally display original cyan/proposed green overlays with failed spans red and implicated original entities highlighted. Clear removes temporary graphics. The command independently revalidates the proposed network against the source; hard failures prevent native implementation-export approval.
7. Explicitly choose Yes to approve this exact proposal's review instructions. No is the default. Approval is bound to the server-held result, proposal ID, complete source digest, and source revision. Select a local output location. A fresh uniquely named folder is created, so earlier bundles are preserved. The bundle contains `network-result.json`, `instructions.json`, `instructions.md`, `components.xlsx`, `components.csv`, `coordinates.csv`, `network-preview.dxf`, and `network-comparison.svg`. URLs and filenames are allowlisted; downloads are bounded, and an incomplete folder is removed if download fails.
8. Optionally choose Keep to add separate review centerlines in one undoable transaction on `PIPEFORGE4_ORIGINAL` and `PIPEFORGE4_PROPOSED`. Discard is the default. Each carries span/system/pipe/source-handle/source-instance/proposal/revision metadata. Source solids remain untouched. These review additions themselves change the drawing revision, requiring fresh inspection before another proposal.

Review centerlines and exports are implementation aids, not commercial fittings, approved fabrication drawings, verified installation access, or an engineering certificate. Match source handles, exact coordinates, ports, piece lengths, fitting dimensions, and unresolved checks before manually implementing an approved design.

## Stale-drawing protection

The native drawing revision combines a new random session identity with an edit counter. Database object appends, modifications, and erasures invalidate old handoffs, including obstacle edits and unrelated geometry changes. Reopening or restarting creates a fresh session. Commands require the active drawing to match the source document and fingerprint, resolve all retained source handles and instance paths to live entities, and compare the current revision. Checks run before and after asynchronous calculation, before preview, before approval/export, after download, and immediately inside the persistent review-save method. Any stale result stops rather than overwriting geometry. A save/reopen requires fresh inspection.

The plugin retains a deep-cloned geometry binding from the successful native inspection in this drawing session. A file-provided hash alone is insufficient. Subsequent handoffs must preserve every inspected span ID, source identity/instance, outer diameter, original point count/interior coordinates, original obstacles and their geometry, routing bounds, and tolerance. Unknown or substituted extraction bindings require `PIPEFORGENETWORK` again. Reviewed engineering metadata can change, and an explicit confirmation of a previously identified endpoint group may snap endpoints within the original recorded tolerance. Additional conservative obstacles are permitted. Baseline geometry cannot be replaced by editing JSON and copying its old revision/hash. The binding is in memory and deliberately does not survive reopen/restart; the native snapshot inspector's geometry interpretation and declared coverage still require engineering review.

## Required Windows acceptance checks

Record the AutoCAD 2027 release/build, .NET build output, Python version, command transcript, synthetic fixture revision, and result hashes locally.

1. Build against the installed Autodesk assemblies and load all preserved commands plus `PIPEFORGEOPTIMIZE`. Repeat the earlier synthetic line, solid, and block workflows.
2. Inspect two or more synthetic pipe sources and a separate surrounding obstacle. Confirm source handles, nested block instance paths, units, global coordinates under a rotated UCS, explicit bounds, and tolerance survive the handoff. Cancel each prompt and verify temporary DXF cleanup.
3. Confirm an ambiguous joint using Connect, Separate, and Skip in separate runs. Cross-system and out-of-tolerance joins must be rejected. Missing fitting/port metadata must remain UNVERIFIED.
4. Exercise Multiple, Systems, and All. Confirm at least two modifiable spans can participate together, the original environment remains present, and an unselected system stays unchanged and cannot acquire an accepted collision. Selecting an object absent from the handoff must stop.
5. Exercise all six strategies with measured comparisons and the candidate/time limits. Hydraulic data omissions must remain explicit. Repeat a no-improvement and an infeasible/blocked scene; neither may fabricate a replacement.
6. Cancel while queued/running and close the progress window. Check terminal cancelled state, no review token or partial approval, no source edit, and a subsequent successful job. Stop the service during polling and verify a clear failure with no source edit.
7. Preview and Clear: no persistent entities may remain. Decline approval and confirm no implementation export. Approve a valid candidate and verify all eight bundle files and coordinate consistency. Simulate a failed download; no incomplete bundle should remain.
8. Move/erase a source or obstacle, add unrelated geometry, switch drawings, and save/reopen between handoff and review in separate runs. Each stale handoff must be rejected, including before persistent save. Edit baseline JSON coordinates, diameters, obstacle geometry, bounds, or source identities while retaining its old revision/hash; these must also be rejected. Reviewed metadata edits and explicit in-tolerance topology confirmations must remain accepted. Reinspect and verify a fresh handoff succeeds.
9. Choose Keep only after review. Confirm original solids are unchanged, only separate review polylines and metadata were added, and UNDO removes the transaction. Reusing the previous handoff afterward must fail because the revision changed.
10. Repeat on installed hardware with an authorized drawing only after synthetic acceptance. Keep confidential CAD, derived exports, and proprietary engineering evidence out of Git and public issues. Record native results separately from Linux build/protocol evidence.
