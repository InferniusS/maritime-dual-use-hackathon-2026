# Native AutoCAD connector

The C# project implements native AutoCAD commands, read-only drawing capture, asynchronous Edge API requests, live geometry deltas, transient proposal previews and explicit designer decisions. It uses local Autodesk managed assemblies and does not redistribute the AutoCAD SDK.

Target: **regular AutoCAD 2025/2026 on Windows x64, .NET 8**. The user's actual release has not been supplied. Check `ABOUT` first. AutoCAD 2024 and older require a separate .NET Framework target; this project is not compatible with those hosts, AutoCAD LT, AutoCAD for Mac or Plant 3D semantic APIs.

See [Windows installation and acceptance checks](../../docs/edge/AUTOCAD.md). Native compilation, `NETLOAD`, event behavior and graphics are **unverified in the Linux development environment**, which contains neither the .NET SDK nor AutoCAD assemblies/host. Source availability is not evidence of host compatibility.

Quick build on the Windows workstation with AutoCAD installed:

```powershell
dotnet build .\connectors\autocad\PipeForge.AutoCAD\PipeForge.AutoCAD.csproj `
  -c Release '-p:AutoCADDir=C:\Program Files\Autodesk\AutoCAD 2025'
```

Use `NETLOAD` with the built `PipeForge.AutoCAD.dll`, then `PFHELP` and `PFCONNECT`. Copy `model.example.json` to a local file, supply real entity handles and engineering metadata, and use a running Edge server URL. Never paste credentials into the metadata or server URL.

`PFCOMPANION` opens/prints the browser URL for this exact native session. After browser review and approval, `PFREFRESH` fetches matching current-revision proposals/decision state for native transient preview and reviewed export. Pending or stale native edits prevent adoption; no remote model is applied to the DWG.
