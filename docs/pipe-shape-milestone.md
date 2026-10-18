# Pipe-shape milestone

Historical milestone from October 17 2026.

**Venue update later on October 17:** the team confirmed that the pipe-shape result displayed in AutoCAD on the Snapdragon and saved real review centerline `26B9`. That closes the real-drawing preview and Save checkpoint below. Its 50 mm settings remain historical. The current [standards iteration](standards-iteration.md) uses the V3 Challenge profile with 50.8 mm clearance and has a separate venue checklist.

The AutoCAD connection is working. The next problem was the geometry used for collision checks. Treating a long bent component as one large box could block empty space around it. On the venue `DB1` test that stopped the search at the end of a preserved connection section.

## What changed

Fully supported components now use conservative pipe-shaped envelopes. Straight cylindrical ends stay bounded. Curved sections have extra coverage between samples. Ambiguous bends use a complete ring. Components with unsupported or incomplete geometry keep their sample boxes.

The search and the final route check use the same shapes. So do end-contact detection and the calculation of fixed connection sections. Pipe radius and the selected clearance are applied once. A contact at the boundary counts as a collision.

The result now names the source object when an endpoint prevents the search from starting. The reports retain the obstacle geometry and the source handles. No original pipe solids are replaced.

## Results measured on the Mac

These tests use the organizer cooling DXF. All values are provisional millimetres. Clearance is 50 mm and local search padding is 500 mm. Grid spacing is 250 mm. Accepted end sections must exit their listed connection objects within a maximum of 250 mm.

Every listed end contact was accepted as an explicit test assumption. That is not automatic verification that the objects are connected fittings.

| Selected route | Original | Returned route | Meaning |
| --- | ---: | ---: | --- |
| `DB1` | 2413.4666 mm | 2558.0000 mm | Original fails the selected clearance near `DD7`. Replacement passes the implemented checks |
| Joined `DB2` / `E6D` / `E16` | 3679.2478 mm | 3679.2478 mm | Valid original retained |
| Same joined route with an artificial 250 mm offset | 4179.2478 mm | 3728.3048 mm | Removes 450.9430 mm of the added detour |

`DB1` keeps 76 nearby obstacles. There are 43 pipe envelopes and 33 fallback boxes. The search expands 123 states. The returned route adds 144.5334 mm to repair the selected clearance failure. It is not a length saving.

The joined route keeps 37 obstacles. There are 31 pipe envelopes and 6 fallback boxes. Its artificial detour result remains 49.0570 mm longer than the untouched baseline. The test does not claim that the organizer's original route was inefficient.

A separate `DC1` test stops during preparation. The accepted end connection cannot be exited within the 250 mm cap. The program reports that limit instead of silently removing the component or extending the allowed connection length.

The reported direction changes count line segments in a sampled centerline. They are not verified counts of elbows. For example the 14 changes in `DB1` describe a sampled curve between straight legs.

## What has been verified on each computer

On the Snapdragon the plugin loaded, ran the small line test, read the real cooling drawing and saved the real DB1 suggestion as review centerline `26B9`. The earlier connection failure was resolved.

At this milestone, all 471 automated tests passed on the Mac and the Windows DLL built without warnings or errors. The pipe-shape calculations above ran on the Mac and the DB1 display and Save were later verified at the venue. Current installation steps and health capabilities are in the [Snapdragon guide](snapdragon-guide.md#update-an-existing-working-setup). The test count and clearance values here describe this historical milestone.

## Repeat several selected routes

`scripts/check_real_routes.py` imports a DXF once and saves a separate result for each selected candidate. Use `--candidate` more than once to check several routes. It writes JSON and a review DXF and an HTML report under `data/generated/real-route-checks` by default. Every run creates a fresh subfolder with a UTC timestamp and a unique suffix. The script prints that folder when it finishes so a failed rerun cannot be confused with an older successful report.

`--assume-end-contacts` explicitly accepts the listed contacts for a local test. Review those objects in CAD before using that option. `--detour-mm 250` also tests an artificial detour when the baseline is valid.

For command options on Windows:

```powershell
.\.venv\Scripts\python.exe scripts\check_real_routes.py --help
```

The latest replay results are in `data/generated/real-route-checks/run-20261018T010133.433259Z-072730ebf9`. Earlier development replays are in `data/generated/pipe-shape-milestone`. Generated drawings and reports stay outside Git. The earlier portable example remains in `examples/real-cad-demo` as a fallback.

## What remains

We still change one selected route at a time. Branches and intermediate equipment connections are not fully modeled. The local box is not a verified ship boundary. The selected clearance and drain rules are not certified engineering standards. Commercial fittings and their complete volumes still need review.

The output is a review centerline. It does not build a replacement pipe assembly. The real-drawing preview and Save milestone is now complete. The next work is tracked in the [standards and model context iteration](standards-iteration.md).
