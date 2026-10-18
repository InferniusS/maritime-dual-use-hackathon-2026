# 3D pipe proposal iteration

This iteration makes the suggested route visibly different from a thin centerline. The previous version could save a line in AutoCAD and report local checks. The new version can display and save pipe-width review meshes with rounded bends, switch between the original and proposed geometry, and export dimensions and a component schedule.

The target is a repeatable demonstration for the pitch. This remains a routing and review prototype. It does not certify a finished ship piping system or guarantee a solution for every unseen drawing.

## What changed

- **Rounded geometry.** Straight pieces and circular bends use the selected outside diameter and centerline bend radius. The proposed occupied envelope is checked against the supplied obstacles, routing bounds and declared permitted spaces. A result is rejected when the bend cannot fit a short leg or fixed end section.
- **A better comparison objective.** A bounded set of alternatives is ranked by generated bend count, vertical straight runs and rounded centerline length, in that order. Original CAD sample points are not counted as verified physical fittings.
- **Visible AutoCAD output.** `MARITIMEDEMO` loads a labeled example into a new drawing. `MARITIMEVIEW` switches the saved review between Original, Proposal and Both. `MARITIMECAD` can request a rounded proposal in a real drawing. Saved geometry uses separate layers and preserves the source objects.
- **Implementation details.** The DXF includes rounded meshes, a rounded centerline and component length/radius/angle labels. Excel includes component coordinates, straight geometric cut lengths, bend arc lengths, radii and angles. These are proposed geometric components, not selected commercial parts.
- **Readable reports.** The browser shows actual-width geometry. A static isometric diagram and length labels appear when printing, avoiding the blank WebGL plot in the team's earlier PDF. The report keeps the radius assumption and missing engineering evidence visible.

The default radius is the largest of **304.8 mm**, **1.5 times outside diameter**, and any larger radius declared in the rule profile, unless a supported explicit radius is supplied. An explicit radius cannot be below the declared rule value. This is a geometric assumption. It does not infer the nominal pipe size, choose a vendor part or verify material and pressure rating. Gravity-pipe rounded assemblies are blocked until slope through the bends is verified; the earlier drain checks remain available.

## Evidence from controlled examples

All five public examples in `examples/assembly` returned their expected results on the Mac:

| Example | Measured outcome |
| --- | --- |
| `01-congested-room.json` | Rounded length about 17.0767 m to 13.4199 m; four to two rounded bends. The fixed end sections stay in place. |
| `02-collision-repair.json` | Produces a rounded repair for an original route that crosses equipment. A repair may add length. |
| `03-blocked-route.json` | No accepted rounded proposal. The blocked outcome is the expected result. |
| `04-elevation-repair.json` | Accepted rounded route includes two vertical straight runs. |
| `05-already-good.json` | Keeps the valid original route when no better accepted alternative is found. |

The first example removes approximately **3.657 m** and two modeled bends from a deliberately inefficient synthetic route. Both sides use the same geometric bend assumptions, so this comparison is meaningful for that example. It is not a saving measured on the organizer's cooling drawing. The scenarios are public regression fixtures, not unseen evaluation data.

The search has time and candidate limits. A blocked result means this search found no accepted proposal. It does not prove no feasible pipe route exists. A laptop that reaches a limit may return a different valid outcome.

## Real cooling-drawing replay

The final Mac replay extracted the cooling DXF again and checked both selected routes:

| Selected route | Result |
| --- | --- |
| Joined `DB2 / E16 / E6D`, candidate `network:7ef8d54381c02b66` | A rounded review proposal of approximately **3597.484 mm**, one generated bend, 88.9 mm outside diameter and 37 nearby obstacles. The test preserved the original start connection section using a 250 mm maximum; the actual protected section was approximately 108.251 mm. |
| `DB1` | Rounded proposal remains blocked with the 304.8 mm radius assumption. The previous 2558 mm sharp centerline does not establish that real rounded geometry fits. |

The joined route's original sampled polyline was approximately 3679.248 mm. Its sampled representation did not produce a valid rounded baseline under the planner's assumptions. The new result is therefore labeled **review proposal**. Do not claim a verified reduction in physical bends or manufactured pipe length against that original. Compare it with the source model and the actual connections before implementation.

The service checked the supplied envelopes. This replay did not establish that every surrounding ship system, boundary and access area was present. No source CAD objects were replaced.

## What has been verified on each laptop

The team's submitted `report2.pdf` confirms that the previous V3 Challenge profile ran on the Snapdragon. Its original route had one failed rule and 26 review items; its suggested route had one passed rule and 26 review items. The new profile did run even though the saved line looked like the earlier result.

The new rounded geometry, controlled scenarios, reports and exports have been tested on the Mac. The full automated suite passes **671 tests**, and the updated Windows plugin builds with zero warnings and zero errors. **The new 3D commands still need a live AutoCAD 2027 test on the Snapdragon.** A successful build is not that runtime test. Save, layer switching, Undo and opening the Excel file are part of the venue check.

## Test this iteration

Follow [the exact Snapdragon 3D demo guide](3d-demo-snapdragon.md). Update the Python files and DLL together, close and reopen AutoCAD, and keep the local service running.

First run the five scenarios from the existing project folder:

```powershell
.\.venv\Scripts\python.exe scripts\check_assembly.py
```

Expect five `PASS` outcomes and a report folder. Then start the service, load the new DLL, create an empty drawing and run `MARITIMEDEMO`. Open `examples\assembly\01-congested-room.json`. Compare Original, Proposal and Both before saving. Rehearse this complete flow before recording the fallback video.

The real cooling-drawing follow-up should use the intended longer route, with its start and end inspected in CAD. DB1 is useful as a blocked-rounding example, not as the main visible success case. Never reduce the radius simply to force a green result without a justified design value.

## What remains before the full challenge goal

1. **Prove the new venue workflow.** Run the updated plugin and capture a reliable before/after demonstration. This is the immediate task for the pitch.
2. **Model complete connected sections.** Preserve branch ports, intermediate valves, equipment connections and required spaces. The current search works on one selected non-branching route at a time.
3. **Confirm the complete surroundings.** Align structure, equipment, other piping, ventilation and wireways. Identify permitted compartments and access areas. `MARITIMECONTEXT` supports declared context but does not discover or align all of it automatically.
4. **Select actual components.** Confirm nominal sizes, catalog fittings, end connections and connection orientation. Check fabrication lengths, weld placement, support locations and installability against the complete source data.
5. **Complete engineering validation.** Some V3 checks need supplied evidence; others still need implementation. Rounded drain bends, structural penetrations and full system function are not established by a pressure-pipe envelope check.
6. **Evaluate unseen cases.** Keep evaluation examples separate from the public fixtures and report repair, unchanged, blocked and unresolved outcomes honestly.

The visible demonstration is substantially more complete than the centerline-only version. Whole-system engineering readiness remains a separate milestone. A completion percentage would conceal these different requirements.

## AutoCAD and Navisworks

Keep AutoCAD for the pitch. The brief permits showing the result in either AutoCAD or Navisworks, and the team's AutoCAD workflow already runs. Navisworks may help with combined-model review later. Changing applications would not provide the missing catalog, connectivity or engineering checks. The supplied brief and submission template contain no Dragonwing or NPU requirement; any separate event condition should be confirmed with the mentors.

The browser fallback, earlier saved demo and previous centerline commands remain available. Use the [demo readiness assessment](demo-readiness.md) for pitch claim boundaries and the [challenge requirement register](challenge-requirements.md) for the full rule list.
