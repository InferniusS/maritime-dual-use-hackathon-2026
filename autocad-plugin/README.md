# AutoCAD 2027 plugin — build from source

This snapshot distributes C# source and project configuration only. It contains no prebuilt plugin DLL, Autodesk assemblies, drawing, screenshot, or saved drawing-derived result. Run the Python service and use synthetic geometry for the initial checks.

## Requirements

- AutoCAD 2027 for Windows with managed .NET plugin support
- .NET 10 SDK for building `net10.0-windows` x64 output
- Python dependencies installed from the root `requirements.txt`

AutoCAD for Mac cannot load this Windows managed plugin. Cross-compilation does not verify native AutoCAD execution.

## Build

From the repository root:

```powershell
dotnet build autocad-plugin/Maritime.AutoCAD.csproj -c Release
```

The project downloads the pinned `AutoCAD.NET` 26.0.0 compile-reference package from NuGet. Autodesk runtime assemblies are not bundled. Alternatively, build against an installed AutoCAD 2027 managed SDK:

```powershell
dotnet build autocad-plugin/Maritime.AutoCAD.csproj -c Release -p:AutoCADManagedDir="C:\Program Files\Autodesk\AutoCAD 2027"
```

The directory must contain `AcCoreMgd.dll`, `AcDbMgd.dll`, and `AcMgd.dll`. The output is:

```text
autocad-plugin/bin/Release/net10.0-windows/Maritime.AutoCAD.dll
```

A different AutoCAD release requires an appropriate target and SDK. This project targets 2027; [the separate legacy connector](../connectors/autocad/README.md) targets 2025/2026.

## Start the local service

From PowerShell in the repository root, after installing the full Python requirements:

```powershell
.\.venv\Scripts\python.exe scripts\run_web.py
```

The plugin and companion use the service on `http://127.0.0.1:8767` on the same computer. `/api/health` reports capabilities. The browser need not be open for native requests.

## Load and test

1. Open AutoCAD 2027 for Windows and create a new synthetic test drawing.
2. Enter `NETLOAD` and select the newly built DLL from `bin/Release/net10.0-windows`.
3. Run `MARITIMEPREVIEW` on a simple line. Confirm a temporary overlay appears and clears.
4. Use `MARITIMEOPTIMIZE` with a synthetic line/polyline route and a synthetic obstacle box. Inspect the original/proposed comparison. Saving a suggestion adds a separate review centerline; it does not replace the source assembly.
5. Follow the [5.1 industrial-network workflow](../docs/edge/AUTOCAD_5_1.md) and [native acceptance checklist](../docs/edge/AUTOCAD_5.md) before using commands on authorized project data.

Use a trusted project folder while retaining normal AutoCAD security controls. Restart AutoCAD before loading a rebuilt assembly if an earlier version is already loaded.

## Commands

| Command | Purpose |
| --- | --- |
| `MARITIMEPREVIEW` | Temporary overlay for selected line geometry |
| `MARITIMEOPTIMIZE` | Route check using selected line/polyline and obstacle extents |
| `MARITIMECAD` | Inspect selected supported pipe solid/block and review a candidate route |
| `MARITIMECONTEXT` | Load, inspect, or clear reviewed context for the active drawing session |
| `PIPEFORGENETWORK` | Create an explicit selected-network handoff with source identity, revision, units, and declared routing bounds |
| `PIPEFORGECONNECTIONS` | Review ambiguous endpoint connections explicitly |
| `PIPEFORGEOPTIMIZE` | Optimize multiple traced pipes, selected systems, or the complete reviewed network; show cancellable progress, preview, and explicitly approve an instruction bundle |
| `PIPEFORGEREVIEW` | Revalidate a reviewed result, inspect coordinates, and preview optional separate review overlays |

`MARITIMECONTEXT` accepts a reviewed context object or a wrapper with `model_context` and optional `engineering`. The synthetic [context template](../examples/model-context-template.json) illustrates structure. `scripts/build_model_context.py` can inspect local aligned DXF exports when processing is authorized; it does not align drawings, infer allowed compartments, or certify coverage.

Endpoint proximity alone does not establish engineering connectivity. The connected workflow needs reviewed system IDs, port identities, dimensions, ship axes, movement constraints, and fitting evidence. Missing information remains UNVERIFIED. Source edits invalidate older handoffs and results.

`PIPEFORGEOPTIMIZE` consumes the reviewed handoff, keeps all declared pipes and obstacles in validation, and limits permitted modifications to the selected spans. It offers balanced, length, bends, hydraulic, minimal-modification, and feasibility strategies, plus candidate and time budgets. Hydraulic ranking needs supplied hydraulic metadata. Cancellation reaches the local engine; a cancelled job cannot be approved. Approval defaults to No, temporary previews default to Clear, and persistent review additions default to Discard. Export creates a fresh local bundle folder; it never replaces source solids. Source identity and the live drawing revision are checked again before preview, approval, download completion, and saving review entities. An immutable in-session extraction binding rejects edited original geometry even when a handoff copies its earlier snapshot hash and revision; reviewed metadata and explicit in-tolerance endpoint confirmations remain supported. Reopening requires fresh inspection.

## Output and remaining verification

Review exports contain coordinates, component schedules, instructions, and generated centerline overlays. They are review packages rather than new manufactured pipe solids or engineering approvals. Use the responsible engineer’s approved standards and catalogs.

Build and protocol checks are described in [the implementation record](../docs/edge/V4_0.md). Native command display, save, Undo, and installed hardware behavior require the Windows acceptance checks. This source snapshot makes no new claim that those native runtime checks have passed.
