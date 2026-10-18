# Track 2 requirements

The baseline is the five-page `[n2 Eng] Pipe Layout Optimization V3-.pdf` supplied by the team on October 17. Pages 1 and 2 contain detailed engineering rules. Page 3 contains the success criteria. Pages 4 and 5 list the supplied confidential drawings. The earlier screenshots and presentation remain useful context, but the numeric rules are no longer missing.

Do not commit the supplied drawings or their extracted geometry to GitHub. Keep generated reports containing that geometry local too.

## What the final tool must do

Improve existing piping in its permitted spaces while preserving system function and component sizes. Evaluate alternatives against surrounding structure, equipment and other systems. Minimize direction and elevation changes while meeting the requirements. An existing route may be correct or may need repair.

Page 2 asks for changes in AutoCAD, a scaled and dimensioned view and an Excel parts list with global coordinates. Page 3 also permits precise information that a designer can use to implement the changes. A review centerline alone is not the complete implementation package.

The live demonstration may show the result in AutoCAD or Navisworks. The program must run on the supplied laptop. The brief describes AI optimization but does not require training a new neural model. Explain the actual search method accurately.

## Numeric rules from V3

Page numbers below are PDF page numbers. A rule being listed here does not mean that every part of it is implemented. The result report must distinguish a checked value from missing evidence.

| Rule | Source | Interpretation and evidence needed |
| --- | --- | --- |
| Minimize changes in direction and elevation | Page 1 | Optimization objective. Sampled points along one curved bend must not be counted as separate manufactured bends. |
| Vertical hanger distance at most 15 in / 381 mm | Page 1 | Measured from pipe center to ship structure. The brief permits longer distances but says to minimize them. Treat excess as a preference finding rather than an automatic rejection. Needs support and structure identification. |
| Horizontal hanger length at most 2 in | Page 1 | 50.8 mm by exact conversion. Measured from pipe edge to ship structure. Needs hanger orientation and attachment identification. |
| Shock clearance of 2 in | Page 1 | 50.8 mm by exact conversion. Applies to structure, distributed systems and equipment. Keep contact exceptions explicit and bounded. |
| Walkway headroom of 77 in or 1960 mm | Page 1 | Use the published 1960 mm metric value and record the rounding difference. Requires a walkway surface and a vertical direction. |
| Horizontal valve stems at heights 229 to 1829 mm | Page 1 | Outside these limits is allowed but not recommended. Requires stem orientation and local accessible standing surface. |
| Upward vertical valve stems at heights 150 to 1295 mm | Page 1 | Preference with the same explicit exception. |
| Downward vertical valve stems no higher than 1980 mm | Page 1 | Preference with the same explicit exception. No lower limit is supplied. |
| Maximum pipe length 20 ft / 6096 mm | Page 2 | Measured along the centerline of one fabricated pipe length. A longer connected route may consist of several lengths. Do not reject an entire route just because its total exceeds this value. |
| Welds at least 25 mm apart | Page 2 | Requires actual weld locations and the intended measurement path. A centerline vertex is not automatically a weld. |
| Elbow cutbacks in 5 degree increments | Page 2 | Stated as a manufacturing preference. Needs actual fitting cutback angles. |
| Pipe size 50 mm or smaller: bend radius at least 152.4 mm and straight length between bends at least 101.6 mm | Page 2 | Needs verified size designation, bend radius and tangent-to-tangent straight length. Do not derive the size class directly from measured outside diameter. |
| Pipe size 80 mm: bend radius at least 228.6 mm and straight length at least 177.8 mm | Page 2 | The brief does not provide rules for sizes between the listed categories. |
| Pipe size 100 mm or larger: bend radius at least 304.8 mm and straight length at least 228.6 mm | Page 2 | Same size and measurement requirements. |
| At most 3 bends per pipe length | Page 2 | Needs fabricated length boundaries and real bend identities. A sampled curve may contain many points but remain one bend. |
| Bent pipe should lie generally in one plane | Page 2 | Transport preference. The brief supplies no numerical planarity tolerance. |
| Pipe bends in 5 degree increments | Page 2 | Manufacturing preference. CAD tessellation angles are not manufactured bend angles. |
| Penetrations at least 2 in / 50 mm from stiffeners and other penetrations | Page 2 | Source values differ by 0.8 mm. A conservative 50.8 mm assessment must identify that choice. Needs penetration and stiffener geometry. |
| At least one pipe diameter of direction change between two decks or bulkheads | Page 2 | Straight runs between them are prohibited. Needs identified penetrations and an agreed geometric interpretation of the offset. |
| Pitched pipe slope 1:25 fore and aft | Page 2 | 0.04 fall per horizontal distance. Needs ship axes, flow direction and verified pitch geometry. |
| Pitched pipe slope 1:100 athwartship | Page 2 | 0.01 fall per horizontal distance. The brief does not define tolerance or a formula for diagonal runs. |

## Other rules that need model context

| Requirement | Source | Evidence needed |
| --- | --- | --- |
| Piping must remain inside ship structure | Pages 2 and 3 | Actual hull and compartment geometry. An assumed local search box does not establish this. |
| No clashes with structure, distributed systems or equipment | Pages 1 and 3 | Complete aligned surroundings and checked physical envelopes. |
| Do not route through other pipes, ducts or wireways | Pages 2 and 3 | Include occupied interiors too. A smaller pipe cannot use the hollow interior of a larger one as free space. |
| Keep walkways and passageways navigable | Pages 1 and 2 | Walkway, ladder and access-space identification. Clearance from metal alone is insufficient. |
| No tripping hazards | Page 1 | Perceived passageways and floor levels. |
| Pipe must be installable | Page 2 | Installation sequence and access. The example of a straight pipe through three consecutive frames shows why collision-free final geometry alone is insufficient. |
| Prefer horizontal and vertical routing | Page 2 | Drain and vent pitch is an explicit exception. |
| Minimize local low and high points | Page 2 | Flow direction and continuous centerline. |
| Valves and equipment are already in the correct spaces | Page 2 | Valves may move within the same space. Do not infer permission to move them across compartments. |
| Preserve required intermediate spaces | Page 2 | A route must still pass through every required original space even when there is no valve or equipment there. |
| Preserve component sizes | Page 3 | Verify original component sizes and compare each affected part. |
| Use standard commercially available fittings | Page 3 | Suitable catalog records and physical dimensions. Shape recognition alone does not establish material, pressure rating or availability. |
| Work across piping systems | Page 3 | Pressure and pitched systems plus reviewed cases beyond one drawing or component. |
| Identify issues needing designer intervention | Slide workflow and page 3 implementation alternative | Explain failed checks and missing evidence without labeling them compliant. |

## Current verified foundation

The real cooling drawing has completed the AutoCAD plugin loop on the Snapdragon laptop. The team supplied a screenshot showing review centerline `26B9` saved on `MARITIME_SUGGESTION`. Source objects were not replaced.

That DB1 test used the old provisional 50 mm clearance. Its original length was about 2413.467 mm and its accepted suggestion was 2558 mm. The selected route had 16 inferred points. It was checked against 76 nearby components, using 43 pipe-shaped envelopes and 33 fallback boxes. This is a real drawing test of one route. It is not a proof of a globally shortest route, full-system optimization or compliance with all V3 requirements. Saving the review object is separate from saving the drawing file to disk.

The importer can join some unambiguous same-diameter chains. It stops at branches and uncertain connections. The routing engine preserves selected endpoints and bounded end sections. Supported solids use conservative pipe envelopes. Other solids retain boxes. The browser remains a fallback.

See [the standards iteration](standards-iteration.md) for this iteration's delivered scope and remaining acceptance steps.

## Surrounding files and remaining decisions

The V3 file list includes structure, equipment, ventilation and wireway drawings as well as several piping systems. A cooling-only snapshot cannot establish clearance against all those surroundings. Include the relevant source files in a shared coordinate system and record what was actually included.

The known numeric rules do not need to be requested again. These model-specific details still need evidence:

- Drawing units and alignment between files
- Ship fore-and-aft, athwartship and up directions
- Flow direction and floor levels
- Pipe size designation and fabricated length boundaries
- Fitting, weld, support and valve identities
- Permitted spaces, access volumes and complete surrounding geometry
- Any approved exception and rule tolerance

Continue development with explicit assumptions. Unknown information must remain visible in the result. A missing check is not a pass.
