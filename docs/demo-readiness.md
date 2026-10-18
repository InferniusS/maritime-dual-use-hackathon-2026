# Demo readiness

This assessment uses the team's October 17, 8:35 PM `report2.pdf`, the five-page V3 challenge brief and the supplied submission template. It records the evidence before the next implementation. It does not mark planned features as completed.

For the subsequent implementation and current test evidence, read [the 3D pipe proposal iteration](assembly-iteration.md). The findings below explain why that iteration was needed.

## What the latest Snapdragon report proves

The Challenge profile ran successfully on the real cooling drawing. The result used a 168.3 mm outer diameter and 50.8 mm clearance. The original route was approximately 2413 mm; the suggestion was 2558 mm. The added length is a clearance repair, not a length saving.

The original failed the modeled clearance check against obstacle `cad-solid-76`, source `DD7`. The suggestion passed the supplied geometric checks. The calculation preserved the declared fixed endpoint sections and evaluated 76 nearby obstacles: 43 pipe envelopes and 33 box envelopes. These are conservative representations, not a complete ship model or exact solid checks.

The rule summary changed from **0 passed, 1 failed, 26 need review** to **1 passed, 0 failed, 26 need review**. This demonstrates the new rule report. It does not demonstrate completion of the 26 missing reviews. The reported 14 to 4 direction changes are changes between sampled line segments, not an observed reduction from 14 physical fittings to 4 fittings.

The report records 0.175 seconds for route search on that computer. It excludes the preceding CAD extraction and preparation. Use a separately measured end-to-end time when discussing designer time or demo speed.

The screenshot of Save confirms a separate review centerline. The existing shaded pipes remain the original geometry, which explains why the model looks largely unchanged. The report confirms the Challenge profile on the venue laptop. Context loading, an Excel file actually opened at the venue, and Undo still need their own checks.

## What the PDF does not show clearly

- The printed 3D chart is blank on page 2. Capture the AutoCAD view or an explicitly rendered static image for the pitch. A browser print of the interactive chart is not adequate visual evidence.
- The rule table was collapsed when printed. The PDF contains the totals but not the 26 individual review findings. Open the HTML table or Excel workbook to inspect them.
- Two consecutive listed points round to the same displayed coordinates. The full result JSON is needed to distinguish a duplicate from rounding. Export geometry should remove zero-length legs before generating physical geometry or a parts schedule.
- The original collision appears three times without a segment label. This can mean several segments touch the same obstacle; it should not be presented as three independent physical clashes.
- The report still describes some selected V3 values as prototype assumptions. The brief supplies 50.8 mm shock clearance; the source units, local search bounds and component identity remain separate assumptions.

## Acceptance for the next demo build

1. **Visible before and after.** Use the same camera and scale. Identify the selected original route and show the suggested occupied pipe shape separately. Clearly distinguish a visual envelope from a manufactured assembly. Show lengths and at least the dimensions needed to reproduce the demonstrated change.
2. **A visible repair case.** Run a deliberately inefficient or obstructed example and show a measurable change. Label artificial damage as a test case. Keep the organizer's original model intact. Also show that a valid case can remain unchanged and that an impossible case is rejected.
3. **Surroundings affect the answer.** Include a reviewed obstacle or protected passageway that changes the chosen path. Record the source, coordinate alignment and units. Show which surrounding systems are included; do not equate an empty region with confirmed free space.
4. **A useful connected section.** Preserve every declared equipment, valve and branch connection used in that demonstration. Report any unresolved connection instead of silently bypassing it. A successful selected section is evidence for that section, not automatic full-system routing.
5. **Designer implementation output.** Produce a dimensioned drawing plus global coordinates and a component schedule. If rows contain only straight route legs or provisional bend geometry, call them a review schedule. A complete parts list requires actual part identities, quantities, sizes and locations.
6. **Repeatable venue run.** Start from a clean AutoCAD session and the exact packaged code. Confirm the original remains available, Save creates the intended objects, Undo works, and the report and workbook open. Record a fallback video after this run succeeds.

The brief prioritizes minimizing direction and elevation changes. CAD route comparisons in the submitted report use polyline length only. That is an implementation gap. A bend-aware objective must distinguish true route corners from sampled points along a curved source pipe; raw sample counts must not masquerade as fitting counts.

## Claims for the pitch

| Supported by the submitted run | Still requires evidence or implementation |
| --- | --- |
| An AutoCAD plugin sends selected geometry to a local routing service on the Snapdragon laptop | Automatic optimization of an entire connected ship system |
| The service produced a route that passed the supplied local geometric checks | All other systems, complete ship boundaries and all access requirements included |
| Selected outer diameter and declared endpoint sections were preserved | Correct part identities, nominal sizes, port directions and branch connectivity automatically identified |
| The V3 report separated a known failure from missing information | Full compliance with every challenge standard |
| A separate review centerline was saved without replacing the source solids | Finished catalog fittings, fabricated pipe pieces and a complete orderable parts list |
| The calculation uses geometric search | A trained model that learns from uploaded drawings, or measured NPU acceleration |

The brief uses the term AI. Explain the implemented method precisely as geometric path search with constraints. If the team calls it search-based AI, explain that meaning and ask the mentors whether it satisfies their intended AI criterion. Do not imply training or learned generalization that is not present.

## Submission and application choices

The supplied template allocates **10 minutes total, approximately 3 minutes for the demo**, and asks for the submission link by **Sunday at 10:00 AM**. It asks for challenge fit, novelty, unique selling points and, for phase 2, actionability and scalability. It contains no Qualcomm AI-processor or Dragonwing requirement. That absence is limited to these supplied files; any separate event requirement still needs checking.

The brief explicitly requires running on the provided laptop rather than specialized equipment, and permits displaying the optimization in **AutoCAD or Navisworks**. The current AutoCAD installation already executes the workflow. Switching viewers does not provide missing routing, parts or engineering logic. Keep AutoCAD for this demo unless the organizers supply a separate requirement or a necessary assembled model that changes the decision. Navisworks remains an optional review tool.

The first priority is a visibly understandable, repeatable demonstration with accurate limits. A successful demo cannot guarantee performance on every unseen faulty drawing. Full challenge completion requires each missing acceptance item to be demonstrated with the actual required data, not merely marked as reviewed.

## Source references

- `[n2 Eng] Pipe Layout Optimization V3-.pdf`, pages 1-3: numerical rules, objectives O1-O3, success criteria and demo conditions. Pages 4-5 list the confidential source drawings.
- `report2.pdf`, pages 1-3: venue run metrics and result. Pages 4-7: assumptions and limits. The PDF is a printed report dated October 17, 2026 at 8:35 PM.
- `Copy of Solvers' Submission Template.pptx`, instruction slide and evaluation slides: pitch duration, submission time and judging topics. The deck is a blank template, not evidence of completed product features.

Keep the supplied drawings and geometry-containing reports local. This document records the assessment without embedding those files or their coordinate tables.
