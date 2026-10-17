# Maritime AutoCAD plug-in prototype

This separate project is the first AutoCAD 2027 plug-in milestone. It adds the `MARITIMEPREVIEW` command, lets the user select line-based centerlines, and displays a temporary cyan overlay. The preview is the selected baseline only; it does not call the Python router yet and does not write anything into the drawing.

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
3. Enter `MARITIMEPREVIEW`.
4. Select a route made from lines or straight polylines. A temporary cyan centerline overlay appears. Press Enter to clear it.

The overlay is transient graphics, so it is erased when the command finishes and is never saved in the DWG. Do not change AutoCAD's security settings to load it; if `NETLOAD` blocks the DLL, place the build output in a trusted folder or add that specific folder to `TRUSTEDPATHS`.

## Next integration step

After confirming this loads on the target laptop, replace the baseline overlay with an actual route result from the Python engine. That will require mapping selected AutoCAD entities and nearby obstacles into the existing route input schema, then showing the calculated centerline as another transient preview before offering any write-back command.
