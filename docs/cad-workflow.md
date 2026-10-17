# Working with the CAD files

We can convert the supplied DWG files to DXF on a Mac. The project now extracts real boundary curves and supported surface patches from those DXFs. This gives us a partial 3D preview of two supplied systems. It does not produce a complete solid model or a verified pipe network.

## Converter used

We installed ODA File Converter 27.9.0.0 for Apple Silicon from the [official ODA download page](https://www.opendesign.com/guestfiles/oda_file_converter?language=en).

The installed app is here on Efe's Mac:

```text
/Users/efe/Applications/ODAFileConverter.app
```

Apple accepted its notarized signature from Open Design Alliance. The developer team ID is `4SVB9F72E4`. No Gatekeeper settings were changed.

The converter is installed locally. It is not part of this repo.

## Convert a file on Mac

1. Keep the original DWG untouched.
2. Put a copy in a separate input folder.
3. Create a different output folder.
4. Run the converter with those two folders.

This is the exact command we tested on Efe's Mac:

```sh
/Users/efe/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter \
  '/Users/efe/Documents/Codex/2026-10-15/okay-so-me-and/tools/cad/test-input' \
  '/Users/efe/Documents/Codex/2026-10-15/okay-so-me-and/tools/cad/test-output' \
  ACAD2018 DXF 0 0 '*.dwg'
```

Replace the app path and folder paths when using another machine. The command processes every matching DWG in the input folder. Use a folder containing only the files you want to convert.

`ACAD2018` selects the output version. `DXF` selects the output format. The first `0` turns off subfolder scanning. The second `0` turns off automatic audit and repair during conversion.

We also tested `ACAD2010` with a separate output folder. That version stores the solid geometry as text ACIS data. It does not turn the solids into pipe centerlines or simple meshes.

## Windows equivalent

Install the Windows version from the same official download page. Then use PowerShell with the actual path to your installed converter:

```powershell
& 'C:\REPLACE_WITH_INSTALLED_ODA_FOLDER\ODAFileConverter.exe' `
  'C:\REPLACE_WITH_INPUT_FOLDER' `
  'C:\REPLACE_WITH_OUTPUT_FOLDER' `
  ACAD2018 DXF 0 0 '*.dwg'
```

Replace all three placeholder paths first. Create the input and output folders before running it. This follows the same documented command format as the Mac command. We have not tested it on a Windows machine yet.

## What we checked in the supplied sample

The source file was `295_U01_Scupper System.dwg`. We converted a copy. The original and the copy had matching SHA256 hashes.

The converted DXF opened successfully in ezdxf. Its structural audit reported no errors and no fixes.

| Check | Result |
| --- | --- |
| Output version | AutoCAD 2018 DXF |
| Output size | 547735 bytes |
| Model space | 18 solids and 2 block references |
| Solids including block definitions | 30 |
| Layers | 6 |
| 3D coordinates | Present |
| DXF unit setting | Unspecified |
| Proxy entities | No `ACAD_PROXY_ENTITY` objects found |
| Custom classes | SCon piping classes are declared |

The older DXF export also contains an ACIS header with a scale value of `25.4`. That does not settle the drawing's units. We currently use the user-approved provisional assumption of millimetres. The importer records that assumption. Changing to inches multiplies the coordinates and radii by `25.4` rather than just changing the label. Engineering measurements remain provisional until the source units are confirmed.

## What conversion does not prove

A clean DXF audit means the file structure passed the checks we ran. It does not prove the pipe design meets the challenge rules.

We have not verified that every custom SCon property survived conversion. We also have not verified pipe sizes or system connections against the original CAD application.

The sample contains curved ACIS solids. Our importer reads their records through ezdxf and reconstructs a supported subset of geometry. Other DXF viewers may still show little or none of the solid geometry. Our preview does not reconstruct every face.

## FreeCAD status on this Mac

FreeCAD 1.1.4 for Apple Silicon is installed at `/Applications/FreeCAD.app`. We downloaded it from the [official release](https://github.com/FreeCAD/FreeCAD/releases/tag/1.1.4). Its published SHA256 matched. Its signature and notarization checks passed. The app launched successfully and completed its first launch setup.

The supplied sample is not yet usable as a complete FreeCAD model. We tested InventorLoader 1.5.1 from the [official FreeCAD add-on catalog](https://www.freecad.org/addons.php?lang=en_EN) in an isolated scratch folder. The GUI test failed while parsing an ACIS header in the 2010 DXF export. No solid model was produced. The test did not modify the original DWG or install the add-on into FreeCAD's normal add-on folder.

That test covered top-level solids only. It would not have established that the geometry inside block references was preserved. A successful file conversion is not yet a verified model import.

A STEP export could help with a full solid modeller later. It is not required for the partial preview importer below. FreeCAD import remains unverified. AutoCAD comparison on the event laptop is still needed to check visual fidelity and component meaning. An empty viewer should not be treated as proof that the model contains no geometry.


## Import the real geometry preview

Run these commands from the project folder after activating its Python setup. These are the exact imports tested on Efe's Mac.

```sh
.venv/bin/python scripts/import_cad.py \
  '/Users/efe/Documents/Codex/2026-10-15/okay-so-me-and/tools/cad/test-output-2010/295_U01_Scupper System.dxf' \
  --source-units mm \
  --output data/generated/cad-import/scupper.json
```

```sh
.venv/bin/python scripts/import_cad.py \
  'data/generated/cad-import/cooling-dxf/542_U02_Fresh Water Cooling Auxiliaries.dxf' \
  --source-units mm \
  --output data/generated/cad-import/cooling.json
```

The API is `pipe_router.cad_import.inspect_cad(path, source_units='mm')`. Supported source unit choices are `mm`, `in` and `m`. All output coordinates and radii are in millimetres. The output records the source file hash and DXF unit header alongside the selected assumption.

The real files are local inputs. They are excluded from Git by the generated-data rule. The original Cooling Auxiliaries DWG is stored at:

```text
/Users/efe/Documents/Codex/2026-10-15/okay-so-me-and/outputs/maritime-dual-use-hackathon-2026/data/generated/cad-import/cooling-input/542_U02_Fresh Water Cooling Auxiliaries.dwg
```

It was downloaded from the organizer's supplied Drive folder. It was converted locally with this command. No CAD file was uploaded to another service.

```sh
/Users/efe/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter \
  '/Users/efe/Documents/Codex/2026-10-15/okay-so-me-and/outputs/maritime-dual-use-hackathon-2026/data/generated/cad-import/cooling-input' \
  '/Users/efe/Documents/Codex/2026-10-15/okay-so-me-and/outputs/maritime-dual-use-hackathon-2026/data/generated/cad-import/cooling-dxf' \
  ACAD2018 DXF 0 0 '*.dwg'
```

## Measured import coverage

These are counts from the actual supplied drawings under the provisional millimetre setting. Block references are expanded into placed instances. Each ACIS body's own transform is applied before its block placement.

| Measurement | Scupper System | Cooling Auxiliaries |
| --- | ---: | ---: |
| Solid instances read | 30 | 1720 |
| Block reference instances | 2 | 63 |
| ACIS faces encountered | 244 | 11720 |
| Supported surface patches displayed | 140 | 5428 |
| Boundary curves displayed | 368 | 15154 |
| Explicit DXF curves displayed | 2 | 421 |
| Candidate runs within individual solids | 22 | 1491 |

Reading every solid instance does not mean every face was reconstructed. Both models contain unsupported face layouts. The cooling model also includes spline and sphere surfaces plus intersection curves that this importer does not support. Counts for omitted types are included in `unsupported_geometry`.

The scupper preview has 76 cylindrical patches and 48 annular patches. It also has 16 bend patches inferred from torus records. Cylinder meshes are derived from full circular boundary edges. A bend's two boundary circles do not resolve which complementary arc is intended. The preview currently selects the shorter arc and labels it as inferred.

These meshes are sampled visual surfaces. They are not watertight collision solids. Preview bounds cover extracted samples only. They are not a conservative envelope around every object in the source drawing.

The cooling import took about 4.7 seconds on this Mac during one observed run. That includes producing the JSON file. This is not a device benchmark. Its complete JSON is about 122 MB with all boundary samples included. A browser viewer should batch geometry and offer reduced detail for the larger system.

## Candidate runs need review

The importer returns geometric axis candidates for supported cylindrical and torus patches. It can join matching axes within a single source solid when they form a simple chain. It rejects branches and mixed-radius chains at this step.

When inner and outer faces have the same axis it keeps the larger geometric radius. That is an envelope estimate. It does not establish the nominal pipe size. A candidate may belong to a fitting or a fastener. Source handles and ACIS record numbers remain attached so a designer can inspect the source.

The extraction output keeps `routes` empty. Unverified runs are in `route_candidates` with `requires_review` set to true. A separate network inspection now joins compatible endpoints across different source solids. Joins require matching inferred diameters and nearly opposing outward tangents. The endpoint tolerance is 1 mm. Ambiguous branches and diameter changes stop joining. It does not identify flow direction or decide which endpoints are equipment ports.

The cooling drawing produced 40 open chains from 115 component candidates. Source placement identities remain attached to every member. The scupper drawing produced no accepted multi-component chains under these checks. This is partial geometric reconstruction rather than a verified piping network.

Real candidate runs are available for inspection. They are not automatically sent to the route solver. Many real segments are angled and some bends are sampled curves. The current synthetic routing examples do not prove that these imported routes can be safely changed. Nearby structure and other piping systems must be included before checking real-world collisions.

## Import validation

The importer tests use independently constructed geometry. Run them with:

```sh
.venv/bin/python -m pytest tests/test_cad_import.py -q
```

The tests cover body placement and block placement. They also check unit rescaling and nonuniform scale handling. Additional cases check malformed input and split ACIS numeric tokens. Candidate chains reject branches and changing radii. Inferred bends stay explicitly marked.

We separately compared the scupper ACAD2010 text ACIS export with the ACAD2018 binary ACIS export. Object counts and bounds matched. The largest sampled point difference was below `0.000001 mm`. That checks consistency between the two converted representations. It does not prove fidelity against the original CAD application.

Local inspection evidence is saved at `data/generated/cad-import/validation.json`. These generated files are excluded from the repository.
