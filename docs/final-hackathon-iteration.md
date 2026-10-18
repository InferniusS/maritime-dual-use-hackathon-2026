# Hackathon pitching iteration

This iteration makes the AutoCAD workflow follow the organizers' 90-second demonstration more closely: review a supplied drawing, identify where to focus, calculate a proposal, show its dimensions, and identify what needs designer intervention. AutoCAD remains the main product. The existing browser, per-pipe check and synthetic examples remain available.

The word "final" refers to the package being prepared for the pitch. It does not establish that every hackathon requirement or withheld test has been passed.

## Improvement over the previous iteration

The previous iteration established rounded 3D proposals and before/after views. The team has now displayed and saved those meshes inside AutoCAD on the Snapdragon and used `MARITIMEVIEW` to compare them. This is stronger evidence than the earlier Mac-only build and tests. The observed blank view recovered after changing tabs or display style; its precise cause remains unconfirmed. Undo has not yet been demonstrated by the submitted screenshots.

This iteration adds a drawing review entry point, `MARITIMEREVIEW`, so the designer can first inspect the supported inferred sections in the current model. It screens a bounded set, reports its coverage, and separates concerns and possible detours from items needing review. The designer then chooses a route for the existing preview, fixed-connection confirmation, rounded search and Save workflow.

The proposal also receives a separate check of its generated geometry and component dimensions. This distinguishes facts calculated from the new proposal from engineering records that belonged only to the old route. Unknown material, catalog, support, installation and model-context information remains unresolved.

The new generated-component assessment measures:

| Check | What is actually measured |
| --- | --- |
| 6096 mm maximum piece length | The analytic centerline length of each proposed piece, including a bend's arc length. It is not the total connected route length. |
| 25 mm minimum interface spacing | Distances along the proposed centerline between consecutive component interfaces, including fixed ends. This measures the plan; it does not confirm actual weld types or fabrication locations. |
| At most three bends per piece | The explicit proposed straight/bend pieces. A bend component is one planned piece; no multi-component fabrication spool is silently assumed. |
| Five-degree bend increments | Actual generated bend angles, checked as the brief's manufacturing recommendation. |
| Piece planarity | The generated piece against an explicitly stated 0.01 mm prototype tolerance. The brief says pieces should lie generally in one plane but supplies no numeric tolerance. This is a recommendation check. |
| Size-dependent radius and straight length between bends | Actual generated radii and tangent-to-tangent straight lengths, including lengths across straight-piece joins, only when the nominal size has verified source evidence tied to the original route. Outside diameter alone cannot establish the size class. |

A failure of an implemented mandatory generated-geometry check blocks the physical proposal. Preference findings remain visible without being converted into mandatory rejection. The assessment does not establish purchased-part availability, materials, wall thickness, pressure rating, actual welds/spools, installation sequence or support placement.

## How to read the result

- **A screening concern** identifies where to look. It is not a complete design conclusion.
- **A possible improvement** still needs the selected-route search and checks.
- **No obvious detour** means the fast screening did not find one. It does not establish that the route is optimal.
- **An accepted geometric proposal** passed the implemented checks against the supplied geometry. This does not automatically make it fully buildable or compliant with all standards.
- **Needs review** identifies missing evidence or an uncertain interpretation. It is not counted as a pass.
- **Not reviewed** preserves visibility of coverage limits instead of silently omitting unsupported work.

The drawing review is capped at 50 supported inferred sections. Branches and uncertain chains may not be representable as one supported route. The optimizer still changes one selected non-branching section at a time; it does not automatically reroute the entire ship network.

## Requirements and present evidence

Status refers to the available evidence for the whole requirement. A partial item can include working software, but still lacks data, coverage or venue verification needed to claim completion.

| Hackathon requirement | Status | Available evidence and remaining acceptance |
| --- | --- | --- |
| Run on the supplied laptop | Demonstrated for existing workflow | The team has run the local service, plugin and saved 3D comparison on the Snapdragon. The new drawing-review command needs a live run there. |
| Show improvements in AutoCAD or Navisworks | Demonstrated for synthetic example; partial for required supplied-model demo | The saved synthetic 3D scene and view switching are visible in the team's screenshots. A useful rounded proposal on a connected section of the supplied system still needs venue demonstration. |
| Identify routing opportunities and already-good portions | Partial | The new drawing review provides screening outcomes and coverage. Bounded search can retain a valid route. Neither is a proof of global optimality. |
| Identify violations of the supplied specifications | Partial | The V3 rule report separates pass, fail and review. Some checks need explicit model evidence; unsupported or missing checks remain review. |
| Minimize direction and elevation changes | Partial | Supported alternatives are ranked using generated rounded bends, vertical runs and length. This is bounded search for a selected section, not simultaneous optimization of the whole network. |
| Output a model a designer can implement | Partial | Separate 3D review geometry, dimensions, global coordinates and a proposed component schedule are provided. Suitable part identities and unresolved design checks still need confirmation. The source system is preserved rather than automatically replaced. |
| Excel list of piping parts and global coordinates | Partial | The generated component export includes positions and geometric dimensions. It is not a verified orderable parts list without suitable commercial part records. |
| Preserve component sizes and correct connections | Partial | Selected outside diameter and declared endpoints/end sections are preserved. Full nominal-size, valve, equipment and branch semantics are not automatically verified. |
| Remain inside ship structure and the same required spaces | Partial | Explicit routing bounds and declared allowed/required spaces can be checked. An assumed local search box does not prove these correspond to the actual ship compartments. |
| Avoid structure, equipment and other systems, including hollow interiors | Partial | Supplied conservative envelopes are checked, including occupied pipe interiors. Complete aligned surroundings and inaccessible areas must be supplied and reviewed. |
| Produce a buildable, spec-compliant result | Partial | Rounded geometry and generated component checks support review. Catalog suitability, complete installation access, supports and remaining rules cannot be inferred from a green mesh. |
| Standard commercially available fittings | Missing acceptance evidence | No verified suitable supplier catalog is linked to the generated bends. A bend radius or familiar shape alone does not prove availability, material or pressure rating. |
| Functional pitched drains/vents and support across systems | Partial | Existing declared directional slope checks are available. Rounded gravity assemblies remain blocked until pitch through bends is supported and verified. Pressure-only success cannot satisfy this whole requirement. |
| Identify designer intervention | Implemented; needs new venue check | Coverage and findings preserve unresolved issues rather than treating missing evidence as compliant. Show at least one concrete intervention in the live review. |

These partial and missing items belong to the **hackathon requirements themselves**. Product packaging, installation convenience and broad marketplace deployment can be future work; the requirement for a buildable compliant proposal cannot be deferred and simultaneously claimed as achieved.

## Evidence to retain for the judges

1. A clear before/after view of a reviewed section in the supplied drawing, using the same camera and scale.
2. Its actual rounded dimensions and measured change. Only claim a before/after physical saving if both sides are comparable representations.
3. The supplied model/context coverage, selected units, system type and fixed connections.
4. The findings identifying checked passes, failures and missing evidence.
5. The component coordinate workbook and dimensioned CAD output.
6. A full timed run on the Snapdragon and a labeled fallback recording.

The synthetic congested-room case remains useful evidence for a controlled test: approximately 17.08 m and four bends becomes 13.42 m and two bends. It is not a saving measured on the untouched cooling drawing. The known joined DB2 / E16 / E6D source section previously produced a rounded review proposal on the Mac, but its sampled original did not establish a comparable manufactured baseline. DB1 remains a valid example of a blocked rounded route under its tested assumptions.

The new drawing review was replayed against a freshly read cooling DXF on the Mac:

| Measurement | Result |
| --- | --- |
| Inferred review units | 1416 |
| Listed units | 50 |
| Geometry-screened units | 47 |
| Listed units needing further review | 3, in regions too dense for the screening limits |
| Total unreviewed units | 1369, reported explicitly |
| Read and review duration | About 6.82 seconds on the Mac, including about 2.66 seconds of screening |

All 47 screened units had flags that include endpoint contacts and conservative obstacle geometry. This is **not 47 proven specification violations**. Intended connections must be reviewed before those contacts can be treated correctly. The result establishes that the coverage and candidate-selection workflow runs on this source drawing, not that the complete system was checked. These measurements are not a Snapdragon runtime or a 90-second guarantee.

The freshly imported DB2 / E16 / E6D section also produced a rounded proposal: **3597.48 mm of centerline, one bend, two straight pieces, and two proposed joints**, preserving its 88.9 mm outside diameter and the explicitly declared fixed end contacts. The selected-route calculation took about 2.33 seconds on the Mac. Its generated-piece assessment recorded **five passes, no failures, five items needing review, and one not-applicable item**. The source route is sampled geometry, so this does not establish a comparable manufactured original or a proven physical length saving.

Final package verification: **710 automated tests passed**. The AutoCAD 2027 plugin built with **zero warnings and zero errors**, and its bundled DLL and source hashes match the build manifest. All five public demonstration scenarios were replayed: detour improvement, collision repair, blocked passage, elevation change, and an unchanged already-good route. These checks supplement the venue evidence; they do not substitute for running the new command in AutoCAD on the Snapdragon.

## Final venue acceptance

Follow [the pitch demo guide](pitch-demo.md). The new command, the updated report, Save, Undo, and the Excel export must be checked on the Snapdragon. Confirm that switching review views does not alter the source geometry. If the new review has not been rehearsed within the available time, use the last demonstrated workflow and state its scope accurately.

A successful test of the public fixtures does not guarantee the organizer's unseen cases. Run the supplied model and show the actual outcome, including a refusal or review requirement when justified. Keep all supplied drawings and geometry-containing reports local.

## What remains after this package

The immediate work is to verify the new flow at the venue and demonstrate a meaningful supplied-model result. Remaining technical work includes robust network and branch interpretation, verified catalog parts, complete surrounding geometry and spaces, all functional pitched-system geometry, and installation/support checks with actual evidence. These gaps affect challenge completion, even if a useful pitch demonstration succeeds.

For later deployment, improve installation, versioning, larger-model performance and review usability. A move to Navisworks is optional and would not supply the missing routing or engineering capabilities. Keep AutoCAD for the pitch.

## Sources

- The organizer's **90-Second Demo Moment** supplied by the team: judge-provided model, routing/specification identification, buildable compliant output and designer intervention.
- `[n2 Eng] Pipe Layout Optimization V3-.pdf`, pages 1-3: numeric standards, objectives O1-O3, success criteria and laptop constraint. Pages 4-5 identify confidential source drawings.
- `Copy of Solvers' Submission Template.pptx`: presentation format, shared deck link, team Notion location and Sunday 10:00 AM submission instruction.
- Team venue screenshots: existing rounded mesh display, Save and Original / Proposal / Both switching. They do not establish Undo, complete compliance or the new drawing-review runtime.
