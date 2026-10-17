# Maritime AutoCAD plug-in prototype

This separate project is an AutoCAD 2027 plug-in prototype. `MARITIMEPREVIEW` displays selected line-based centerlines as a temporary cyan overlay. `MARITIMEOPTIMIZE` sends one connected route and user-selected obstacle geometry to the local Python router, then displays its returned suggestion as a temporary green overlay. Neither command writes geometry into the drawing.

The first version reads `LINE`, lightweight polylines, and 3D polylines. Straight polyline segments are previewed. Bulged (curved) lightweight-polyline segments are skipped and reported. This keeps the first check focused on whether the plug-in loads and can inspect/render geometry inside the Snapdragon laptop's AutoCAD 2027 installation.

## Build on the AutoCAD laptop

Install the .NET 10 SDK. AutoCAD 2027 supplies the .NET 10 runtime, but the SDK is needed to compile this project. The project references the three managed AutoCAD assemblies from the default AutoCAD 2027 install folder:

```text
C:\Program Files\Autodesk\AutoCAD 2027
```

If AutoCAD is installed elsewhere, pass the folder containing `AcCoreMgd.dll`, `AcDbMgd.dll`, and `AcMgd.dll`:

```powershell
dotnet build .\autocad-plugin\Maritime.AutoCAD.csproj -p:AutoCADManagedDir="D:\Autodesk\AutoCAD 2027"
```

Otherwise, from the repository root:

```powershell
dotnet build .\autocad-plugin\Maritime.AutoCAD.csproj
```

## Load and try it

1. Open a drawing in AutoCAD 2027.
2. Enter `NETLOAD` and select `autocad-plugin\bin\Debug\net10.0-windows\Maritime.AutoCAD.dll`.
3. For `MARITIMEPREVIEW`, select route lines or straight polylines. A temporary cyan centerline overlay appears. Press Enter to clear it.
4. To use `MARITIMEOPTIMIZE`, first install and start the Python workspace on this same Windows laptop. From a second PowerShell window in the repository folder, run:

   ```powershell
   .\scripts\setup_windows.ps1
   .\.venv\Scripts\python.exe scripts\run_web.py
   ```

   Keep that window open. The plug-in calls `http://127.0.0.1:8767/api/run` on the same computer.
5. In AutoCAD, enter `MARITIMEOPTIMIZE`. Select the connected route (one or more connected `LINE`/straight polyline entities), select obstacle entities, then enter pipe outside diameter, clearance, grid step, search-bound padding, and drawing units (`mm`, `m`, or `in`). Press Enter to clear the green suggestion.

Obstacle entities are approximated by their axis-aligned extents. The search bounds come from the selected route and obstacle extents, expanded by the padding you enter. The optimizer only checks the geometry you select and the prototype's routing rules. Verify units, selected objects, dimensions, route, and result before using any suggestion. Curved route segments and closed routes are not supported. The overlay is transient graphics, so it is erased when the command finishes and is never saved in the DWG. Do not change AutoCAD's security settings to load it; if `NETLOAD` blocks the DLL, place the build output in a trusted folder or add that specific folder to `TRUSTEDPATHS`.

## Next integration step

This is an initial end-to-end integration. Next, improve obstacle extraction and provide a reviewed write-back workflow only after the preview and engineering assumptions have been validated on representative drawings.
