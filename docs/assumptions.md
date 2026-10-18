# Units and rule assumptions

The team supplied the five-page Pipe Layout Optimization V3 brief on October 17. It contains numeric engineering rules. The full rule register is in [challenge-requirements.md](challenge-requirements.md). Earlier prototype defaults remain useful for reproducing past tests, but they are not substitutes for the V3 requirements.

A result passing implemented checks means only that the supplied geometry and metadata passed those checks. Missing ship geometry, part identities or installation information must remain visible as review items.

## Units

Millimeters are the internal working unit. They are an interpretation of source coordinates until the drawing units are confirmed. Every result should record the source unit selection and conversion factor.

Changing units requires rescaling coordinates, outside diameters and all geometry together. It is not a label change. For an inch drawing, one source coordinate unit becomes 25.4 mm. A separately configured 50.8 mm shock clearance stays 50.8 mm after conversion. Recheck the result after correcting source units.

Nominal size and outside diameter are separate values. Use the actual outside envelope for collision checks. Do not choose a V3 bend-size category by guessing nominal size from outside diameter. Insulation and coverings need their own occupied envelope when present.

## V3 values and interpretation choices

| Item | V3 value | Interpretation |
| --- | --- | --- |
| Shock clearance | 2 in = 50.8 mm | Replaces the old 50 mm value for V3 assessments. The old DB1 result must not be relabeled as a V3 result. |
| Headroom | 77 in or 1960 mm | Use the brief's published 1960 mm metric value. Exact conversion of 77 in is 1955.8 mm, so record the rounding. |
| Penetration clearance | 2 in or 50 mm | The brief contains a 0.8 mm mismatch. A conservative assessment may use 50.8 mm but must state that choice. |
| Fore-and-aft pitch | 1:25 = 0.04 | Needs declared ship direction and flow direction. |
| Athwartship pitch | 1:100 = 0.01 | Applies to that direction. It is not a universal drain slope. |
| Fabricated pipe length | At most 6096 mm | Applies to one pipe length, not necessarily an entire connected route. |
| Bends per fabricated length | At most 3 | Requires actual bend and length identities. Tessellation points do not supply these. |

The pitch wording does not specify a numerical tolerance or a diagonal interpolation formula. Mixed-direction or uncertain cases need an explicit interpretation. The bend-size categories cover 50 mm or smaller, exactly 80 mm and 100 mm or larger. The brief does not state whether these refer to nominal sizes and does not fill the gaps between those categories. Unmatched or unverified sizes must not silently inherit a rule.

Some V3 limits permit exceptions. Vertical hanger distance above 381 mm is allowed but discouraged. Valve accessibility heights also allow exceptions. Five-degree angle increments and near-planar bent pipe are stated as preferences. Keep these findings separate from strict failures. Do not silently approve an exception or rewrite a preference as a prohibition.

## Prototype settings retained for replay

The legacy profiles are in [rule-profiles.json](../examples/rule-profiles.json). Keep the selected profile and overrides with each result so earlier tests remain reproducible.

| Setting | Historical value | Meaning |
| --- | --- | --- |
| Clearance | 50 mm | Provisional gap used in the successful DB1 test. It is 0.8 mm below the V3 shock requirement. |
| Grid spacing | 250 mm | Search resolution, not an engineering standard. |
| Turn penalty | 300 mm | Prototype score adjustment, not a physical dimension requirement. |
| Minimum gravity slope | 0.01 | Generic synthetic-test assumption. It cannot represent both ship directions in V3. |
| Local search padding | 500 mm in the DB1 replay | Search extent, not evidence of the hull or allowed compartment boundary. |
| Maximum fixed end section | 250 mm in the DB1 replay | User-selected bound for preserving intended end contacts. It does not verify their engineering identities. |

Positive z was treated as up in those prototypes. Flow was treated as start to end. These are model assumptions that need confirmation for each source system. Search failure on one finite grid does not prove that no physical route exists. A valid suggestion is not a proof of global optimality.

## Small fitting reference

Four entries from the manufacturer's [Weldbend metric catalog](https://www.weldbend.com/Catalog_63_English_Metric.pdf) remain available as a reference. The [catalog page](https://www.weldbend.com/catalog.php) links the publication. These are STD long radius 90 degree butt-weld elbows with the published rounded metric dimensions.

| Nominal size | Outside diameter | Center to end |
| --- | --- | --- |
| NPS 1 | 33.4 mm | 38 mm |
| NPS 2 | 60.3 mm | 76 mm |
| NPS 3 | 88.9 mm | 114 mm |
| NPS 4 | 114.3 mm | 152 mm |

The catalog's center-to-end dimension is not stored as a separately verified bend radius. These entries are not the ship's approved parts list. Do not resize an existing pipe to fit the reference. A matching size alone does not establish material compatibility, pressure suitability or current availability.

A fabricated bend and a purchased elbow need distinct records. Buildability also needs their full occupied shapes, straight lengths, weld locations and installation allowances. An Excel export of centerline points does not by itself become a parts list.

## Model context and evidence

The plugin's successful real drawing test establishes local operation on the Snapdragon. It does not establish complete surrounding model coverage. A cooling-system drawing alone cannot represent all ship structure, ventilation, wireways, other piping and access spaces.

Record source files, their units, alignment and role. Identify supported pipe envelopes and fallback geometry. Unknown or incomplete surroundings must not be described as complete. Keep approved local end-contact assumptions bounded to unchanged original sections. Never remove a whole obstacle merely because it touches a connection.

Remaining evidence includes real flow directions, ship axes, allowed spaces, component identities, fabricated lengths, approved fittings, supports, welds and access areas. Hydraulic, structural and installation analysis remain separate from geometric route checking.
