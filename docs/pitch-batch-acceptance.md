# Real-model repair batch and pitch acceptance

Requirements checked on 18 October 2026 against all five pages of the supplied
Pipe Layout Optimization V3 brief and all seven slides of the submission
template. This document sets acceptance gates; a planned feature is not a passed
test. The detailed rule-by-rule audit is in
[requirement-acceptance-matrix.md](requirement-acceptance-matrix.md).

## What is already demonstrated

The current AutoCAD workflow runs on the Snapdragon, inventories inferred pipe
sections, locates selected sources and processes fresh synthetic source objects
through the ordinary scan-and-optimize pipeline. The user has confirmed the
native collision-repair, already-clear and blocked-endpoint acceptance cases.
The collision-repair screenshot shows a rounded green detour and cyan original
through the wall, after explicitly changing search padding from 500 to 1500 mm.
The contact-review release has native screenshots for four contact inspections,
returning to selection and the corrected blocked-endpoint report. Those tests
verify usability and diagnosis. They do not establish an accepted current-release
repair of the supplied cooling section or the identity of its contacts.

## This batch's stopping tests

| Gate | Required behavior | Automated evidence | Snapdragon evidence |
| --- | --- | --- | --- |
| B1: distinguish route and contact | Selected route, both endpoints and one contact are visually distinguishable from surrounding geometry. Inspection does not approve anything; source is restored on finish/cancel. | Source instance mapping, nested/transformed blocks, temporary-state cleanup, no change to source model. | Inspect two adjacent contacts; correct instance/endpoint visible; cancel and confirm the original view/entities return. |
| B2: diagnose actual connection geometry | Distinguish physical overlap, clearance-only contact and uncertain envelope. Do not turn proximity into an inferred equipment port. | Independent distance/intersection cases, same point in different transforms, unknown envelopes remain uncertain. | A clear intended joint and a nearby non-connecting object can be identified without guessing from a whole-model cyan view. |
| B3: preserve verified connections | Any declared exception has an explicit source/endpoint binding and finite protected extent; preserve the attachment and direction needed by the verified connection. | Connected endpoint and stub unchanged; unrelated adjacent object and midpoint collision still block; no automatic global obstacle exemption. | Show the original and proposed attachment in the same view. Record any reviewer declaration in the report. |
| B4: handle original curves honestly | A sampled existing arc is not counted as many manufactured elbows. Reference reconstruction failures do not masquerade as candidate failures. | Sample-density invariance, tangent continuity, radius/endpoint preservation, deliberately non-circular samples rejected or left unclassified. | Original bend representation agrees with the input. Any displayed savings uses a comparable physical baseline; otherwise savings is unavailable. |
| B5: repair and recheck | A repairable case has a continuous rounded proposal with source endpoints/OD retained and no new collision in the included context. | Full finite diameter, bend sweep/chord-error allowance, pressure straight-axis rules and fixed attachments checked after generation, including transformed/fresh input. | Accepted proposal visible in AutoCAD; inspect both ends and tightest passage. Record included context and remaining rules. A green color is not the clearance test. |
| B6: report each outcome | Repairable, unchanged, blocked and ambiguous cases produce distinct evidence and no rejected geometry is saved. | All four outcomes; no better candidate is not called globally optimal; batch IDs trace to source sections. | Show each result; blocked/ambiguous remain unresolved and locatable. |
| B7: deliver a designer-readable result | Accepted result has scaled geometry, dimensions and a matching Excel/global-coordinate package; save/reopen works. | Export component IDs, units, endpoint coordinates and accepted-only part rows reconcile with analytic geometry. | Original/Proposal/Both; dimensions On; workbook row checked against model; save a copy, reopen, then confirm the comparison still appears. |

Do not force a specific section to become green. If the selected real section is
not repairable under the resolved constraints, retain its blocked/ambiguous
result and demonstrate a separately identified repairable real section. A
deliberately edited test copy must be labelled as such. An invented fixture is
useful regression evidence but is not a live run on the organizer's model.

## The brief's explicit minimum requirements

The six requirements below are from V3 page 3. They are not replaced by the
ability to export a mesh or by the submission template's field-MVP roadmap slide.

| Minimum | Evidence needed for the claim | Current limitation / release gate |
| --- | --- | --- |
| Model stays inside ship structure | Identified ship/allowed-space envelope, aligned units/frame, full proposed volume inside it. | Local search bounds do not establish containment. Include and validate relevant structural/space context. |
| Component sizes stay unchanged | Source-bound size identity and before/after checks for affected pipes, fittings, valves and reducers. | Inferred outside diameter is retained for supported sections; it is not a full nominal-size/component inventory. |
| Standard commercially available fittings | Actual available fitting/standard records with compatible size, angle, radius, mating ends and occupied volume. | Generated bends are geometric envelopes and `catalog_verified` is false. Do not claim a purchasable parts list until each affected fitting has a supported match. |
| No small pipe inside another pipe or HVAC duct | Filled occupied/interior keep-outs for included pipes and ducts, not just shell-surface intersection. | Relevant external systems must be included; unread/unknown interior geometry cannot be declared clear. |
| Functional system and correct pitched drainage | Preserved required connections/components; known flow and ship axes; continuously correct final pitch, including bends. | Rounded gravity proposals are currently blocked. Pressure geometry success does not satisfy the pitched-system minimum. |
| No clash with structure, equipment or distributed systems | Finite-volume clearance against complete, aligned relevant context, including fittings and accepted changes. | Collision tests cover supplied approximations only. A cooling-only scene is not complete surrounding-model evidence. |

The other page-3 must-haves also require operation across piping systems and
conformance to the detailed standards on pages 1-2. The full matrix retains the
support, access, manufacturing, penetration, installation and required-space
gates. Unimplemented mandatory checks remain requirements gaps even when the
report correctly flags them for designer intervention.

## Minimum visible outputs and native demo

- **O1, V3 pages 2-3:** show a routing improvement in AutoCAD, or the information
  necessary for a designer to easily implement it. The page-3 alternative means
  destructive replacement is not mandatory; an unlabelled envelope alone does
  not establish sufficient implementable component information.
- **O2, V3 page 2:** to-scale visualization complete with dimensions. Demonstrate
  actual dimensions and units on the accepted output, not only a colored route.
- **O3, V3 page 2:** an Excel list of piping parts and their global coordinates.
  Reconcile it with the displayed model and distinguish geometric review pieces
  from commercially identified parts.
- **Live demo, V3 page 3:** run on the supplied laptop and pre-prepared piping
  system, and show optimizations and standards met in AutoCAD **or** Navisworks.
  No Navisworks migration is required by the brief.
- **Supplied 90-second flow:** distinguish already-good sections, improvements
  within required spaces, violations and unresolved designer-intervention areas.
  It requests a buildable, spec-compliant result; report acceptance under only
  implemented geometric checks must not be described as that complete result.

Describe the deployed optimizer accurately as local search and rule/geometry
checking. The brief calls for an AI-based tool; do not invent a trained model,
LLM inference stage or organizer approval of the architecture. An AI coding
assistant is not evidence of runtime AI functionality.

## Pitch-file submission checklist

From template slide 1 and the five content slides:

1. Keep the same copied deck and link. Rename it `Team 7 - <solution name> - Pitch`.
2. Remove the instruction slide and all placeholder team details.
3. Cover title/team, challenge fitness, innovation, unique selling points, and
   actionability/scalability when this is the second pitch (that slide is marked
   phase 2 only).
4. Include a short working-prototype video or high-resolution rendering,
   architecture diagram or deployment image. Label synthetic or edited tests.
5. Rehearse to **10 minutes total, approximately 3 minutes for the demo**. The
   supplied 90-second flow is the desired story, not a replacement time limit.
6. Verify the deck is viewable by anyone with the link and that the same link is
   in the team's Notion **Pitch Deck** field. The deadline stated is **Sunday
   10:00 AM**; the template does not specify a time zone.

These source instructions define the required submission format. This audit
does not itself change sharing, submit the deck, or certify organizer acceptance.

## Release evidence to retain

Keep the exact commit/DLL hash, matching Python service version, Windows/AutoCAD
version, input hashes and unit/frame settings, included context inventory,
automated results, native run recording, dimensions screenshot, Excel output and
save/reopen result. Check the release archive excludes supplied CAD and generated
real-model geometry/reports; those assets stay local. Confidential organizer
drawings are not public regression fixtures.

Mark each gate as **implemented, automated-tested, native-tested, unresolved or
not applicable with a reason**. Do not aggregate those different states into a
percentage-complete or a full-compliance statement.

## Repair-batch implementation evidence

Release `repair-batch-20261018` passed 842 automated tests and a zero-warning
Windows plugin cross-build on the Mac. New implementation covers:

- Contact classification against the actual checker envelope, bounded fixed-path
  evidence, unchanged source attachment stubs and no automatic approval.
- Strict sampled circular-arc recognition for proposal seeds at the declared new
  radius. No invented original fitting count or fabricated-piece savings.
- Challenge pressure free-straight horizontal/vertical screening; oblique free
  rises fail, and missing/unconfirmed ship orientation remains needs_review.
- Magenta selected-path overlay, contact Next/Previous navigation, source evidence
  in HTML/Excel, release matching and native acceptance scripts.

The supplied section 17 replay remains blocked. Its source bend has 13 samples
consistent with a 114 mm geometric arc; this does not establish a commercial
fitting. Required proposal radius remained 304.8 mm. The local real drawing scan
processed 1,416 inferred sections in 29.39 s. Conditional tests of sections 16/27
with unverified contact assumptions did not establish an accepted repair either.
No contact-free section was found in that inventory, which includes components
whose pipe identity is unverified. These are unresolved extraction/connection and
constraint gates, not successful optimized-model evidence.

Four fresh, invented CAD HTTP cases passed: collision repair, already clear,
blocked endpoint and translated block. The user has now exercised the first
three native creation scripts and confirmed their expected outcomes, after
using padding 1500 mm. The user reports capturing photos and downloading reports.
Independent inspection of the complete new report set, Excel-versus-CAD
measurements and save/reopen/Undo evidence remain outstanding. This does not
establish a repaired organizer model or full brief compliance.

## Next real-model batch evidence

Release label: `real-model-batch-20261018`. Native runtime testing of the new
release remains required even though the earlier three synthetic cases passed.
Follow [real-model-batch-snapdragon.md](real-model-batch-snapdragon.md).

- Combined reports distinguish **batch item** from a source scan number. A
  source scan number is a client cross-reference, not geometry identity. The
  immutable source hash, candidate ID and source handles identify the result.
- Every accepted geometric piece has numeric global XYZ, OD, analytic length,
  bend radius/angle/center/normal and a matching component ID in the review DXF.
  The workbook's **Export reconciliation** sheet checks identity, continuity,
  source endpoints, OD and length totals. Inconsistent artifacts are withheld
  with an explicit export finding; recorded solver outcomes are not rewritten.
- **Search settings** records the active padding, clearance, grid, radius and
  bounds. The report gives the diagnosis and next action when available; search
  bounds do not establish ship containment. A search limit never proves that
  a physical reroute is impossible.
- These changes advance OUT-01/02/03/05/06 evidence quality. They do not implement
  catalog-backed fittings, complete surrounding-system inventory, rounded gravity
  output, full topology, or remaining support/access/installation checks.

Automated evidence: `tests/test_system_export_evidence.py`, including fresh
coordinated geometry with bends, retained source labels, mismatched export
rejection and numeric coordinate/metric reconciliation. The end-to-end acceptance
gate remains a real-model run, inspection and saved evidence on the Snapdragon.

The next real-model inspection candidate is source group
`CD,D0,D2,D3,D4,D8,D9,DA,DC` in the organizer's
`642_U02_Fire-Fighting and Deck Wash` drawing. A local conditional probe produced
a straight 1739.503 mm geometric proposal at unchanged 88.9 mm OD while retaining
137.251 mm source stubs at each end. It assumed 14 endpoint contacts were intended
connections; that assumption has not been verified in AutoCAD. The source's
sampled length is about 1850.453 mm, but a comparable rounded baseline is absent,
so no physical saving is certified. This is a test candidate, not a closed
real-model repair gate or proof of a new algorithmic success. Follow the source
and connection inspection steps in the new guide, without automatic ALL
approval. Its generated geometry and reports remain local, outside the release.

The importer now removes only redundant or fully contained tube envelopes before
the existing per-solid representation limit. Conservative occupied coverage is
retained; unsupported surfaces still require a fallback or review. Tests cover
duplicate axes, clipped extents, containment and noncontained counterexamples.
This reduces unnecessary broad-box approximations on some complex supported
solids, without claiming exact CAD collision geometry or semantic port identity.


## Integrated real-model batch validation

Release `real-model-batch-20261018`: 888 automated tests passed in 24.25 seconds;
Windows plugin cross-build completed with zero warnings/errors. The updated
report was inspected in a local browser, including active settings, fixed
connections and distinct source-scan/batch identifiers. A replay of the user's
native invented collision result remains blocked at padding500 with a specific
search-cross-section diagnosis, and returns a rounded proposal at padding1500
with exact endpoints, diameter and clearance unchanged.

The new DLL's native views and real-model behavior still require a Snapdragon
run. Conditional fire/deckwash output is not verified connection evidence or a
completed real-model repair gate. Full engineering compliance remains open.
