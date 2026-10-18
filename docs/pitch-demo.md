# The hackathon pitch demo

The main product is an AutoCAD plugin running on the supplied Snapdragon laptop. Python runs the local calculation service on that same laptop. The browser is a report viewer and fallback, not a replacement for the AutoCAD workflow. No training run, remote service or Dragonwing board is needed for this implementation.

This guide targets the **90-second demo moment** supplied by the organizers: inspect the supplied model, identify routing and specification issues, show a useful proposal, and identify the items that still need the designer. The supplied pitch template allows **10 minutes total, with approximately 3 minutes for the demo**. Rehearse the 90-second core, then use the remaining demo time for context or questions. Neither duration is a measured runtime guarantee.

## What to prepare before presenting

1. Update the Python files and prebuilt DLL together using [the Snapdragon update guide](3d-demo-snapdragon.md). Close AutoCAD before replacing its loaded DLL.
2. Start the local Python service and leave its PowerShell window running. The health page at `http://127.0.0.1:8767/api/health` on that laptop must include `drawing_review`. Load the updated plugin with `NETLOAD` in AutoCAD 2027.
3. Save a separate working copy of the supplied drawing. Keep the original available. Use its real units and coordinate system; do not change a unit assumption to make a check pass.
4. Confirm the required structure, equipment, other piping, ducts and wireways are present and aligned. A cooling-only drawing does not establish that a route clears the entire ship. If reviewed compartment or engineering metadata is available, load it with `MARITIMECONTEXT`.
5. Measure a complete rehearsal: drawing extraction, screening, selected-route calculation, display and Save. Record the actual duration on the Snapdragon. A route-search time alone excludes extraction and is not the full demo time.
6. Have the HTML findings report and Excel component schedule ready to open. Record a fallback video after a successful run. Label a recording as a recording, and a previously calculated result as previously calculated.

The existing 3D demonstration has been displayed and saved on the venue laptop, and the team has used Original / Proposal / Both. The newly added drawing review command needs its own live rehearsal. Undo and opening the new Excel output at the venue still need separate checks.

## Run the supplied drawing in AutoCAD

Use `MARITIMEREVIEW` for an overview of a drawing. Use the existing `MARITIMECAD` command when you already know which pipe solid to inspect.

1. Open the working copy of the supplied drawing and select the **Model** tab.
2. At the AutoCAD command line, enter:

   ```text
   MARITIMEREVIEW
   ```

3. Answer the settings in this order. The following values reproduce the cooling-drawing pressure-pipe check; use different units or system type only when the source requires them.

   | Prompt | Repeat-test value |
   | --- | --- |
   | Interpret drawing coordinates | Millimetres |
   | Pipe system | Pressure |
   | Rule profile | Challenge |
   | Clearance in mm | 50.8 |
   | Local search padding in mm | 500 |
   | Search grid step in mm | 250 |

   These are the defaults, so Enter accepts each displayed value. Gravity systems additionally require direction/slope information. A pressure-pipe check does not establish drain or vent functionality.
4. Wait for the drawing review. It screens up to 50 supported inferred sections. Read the coverage statement: the list is not proof that every pipe or branch was recognized. Unsupported and unreviewed geometry must remain visible as unfinished work.
5. Read the listed outcomes:

   | Screened outcome | What it means |
   | --- | --- |
   | Issues found | The screening found a concern worth inspecting. Confirm the finding in the source drawing. |
   | Potential routing improvement | The inferred route has a possible detour. The optimizer still needs to evaluate alternatives. |
   | No obvious detour in screening | The screening did not identify an obvious geometric detour. This is not proof of global optimality or full compliance. |
   | Designer review needed | Interpretation or evidence is insufficient for a reliable conclusion. |
   | Not screened | This item was not evaluated within the current limits. |

   The HTML/Excel review uses equivalent labels, such as "Check flagged geometry / rules". Intended equipment connections can be flagged before they are reviewed; a flag does not automatically prove the source drawing is wrong.

6. Choose a route number. Inspect the cyan route's start, end, and intermediate components, then press Enter to continue. Do not accept a chain that bypasses a valve, equipment port, branch or required space.
7. If endpoint contacts are listed, confirm **Yes** only when they are real intended connections. If Yes is selected, the repeat-test maximum fixed-section length is **250 mm**. This is a bounded preservation setting, not a clearance value or universal correct answer.
8. Review the bend-radius assumption. The selected outer diameter is preserved. A generated rounded bend is not automatically a suitable commercial fitting.
9. Let the selected-route calculation finish. A successful overview does not guarantee an accepted replacement. Read the original and proposed findings before saving.
10. When a rounded proposal is available, choose **3D** if prompted, then use **Original**, **Proposal**, and **Both** to compare. Explicitly type **Save** to retain the review geometry. Use **Save As** to save the drawing file to disk.
11. Run `MARITIMEVIEW` to compare the saved review layers. This does not hide or replace the original source CAD system. The proposal is an additional review model beside the source.
12. Open the reported HTML and Excel links on this laptop. The drawing review provides `route-review.xlsx`; the selected-route result provides its separate component output. Show the relevant failed and unresolved checks, component coordinates, and proposed dimensions. In the generated-component assessment, show measured piece lengths, interface spacing and bend geometry separately from missing fabrication or catalog evidence. A generated component schedule is not a confirmed order list unless suitable catalog parts have been identified.

For the known cooling drawing, the joined **DB2 / E16 / E6D** section produced a rounded review proposal on the Mac. It is a useful first venue check. Open the HTML or Excel drawing review, find those source handles, and use that row's number in AutoCAD. The command-line list does not print all source handles. Do not assume it is route 1 or reuse an old route number. If it falls outside the first 50 listed sections, use `MARITIMECAD` to select that known section directly. The earlier **DB1** section could not produce an accepted rounded proposal with the selected 304.8 mm radius and fixed connections. Do not lower its radius simply to force a green result.

The first Mac drawing review listed 50 of 1416 inferred units and geometry-screened 47. Its reported 1369 unreviewed units are intentional coverage disclosure. All screened units had flags including possible intended contacts, so the overview must not be presented as a list of 47 proven defects.

## The 90-second presentation

Use the actual route and result from rehearsal. The times below allocate presentation time; they do not promise that an arbitrary judge's file can be extracted and optimized in 90 seconds.

| Time | On screen | What to explain |
| --- | --- | --- |
| 0-15 seconds | Supplied drawing in AutoCAD | "We keep the equipment, pipe size and required connections. The drawing provides the geometry we check." Identify the portion and surrounding systems included. |
| 15-35 seconds | Drawing review and coverage | "The review separates concerns, possible detours and items needing a designer." Point to a specific result. State if any parts were not recognized or screened. |
| 35-65 seconds | Selected route and local calculation | "We evaluate alternatives while preserving the declared connections and checking occupied pipe geometry." Run the calculation live if timing permits. If using a recorded or earlier result, say so. |
| 65-80 seconds | Original / Proposal / Both | Show the same camera and scale. State the measured rounded bends, elevation changes and length where a valid comparison exists. A longer repair can still fix a collision. |
| 80-90 seconds | Findings and component output | "These checks passed; these issues need intervention." Show the proposed dimensions and coordinates. Say what remains unresolved rather than calling the entire model compliant. |

The key result is a useful change on a reviewed section of the supplied model. Do not spend the live demo tuning settings, installing packages or reading every warning aloud. Keep the full findings available for the judges.

## If the supplied route is already good or cannot be improved

A legitimate result can be unchanged, blocked or require review. Show the reason. "No better route found by this search" is accurate; "this is the optimal route" requires stronger evidence than the bounded search provides.

For the already-good requirement, keep one example where the selected-route calculation retains the valid route. The overview's "No obvious detour" label alone is weaker evidence: it has not searched for a replacement.

The supplied standards apply to the hackathon output. A missing commercial fitting, unverified required space or unknown drain slope is a remaining challenge limitation, not something solved by calling the product a prototype. If the supplied case cannot meet an implemented check, identify that intervention explicitly.

Do not add an artificial defect to an organizer drawing and imply that the defect was supplied. A deliberately altered copy may be used as a **labeled repair test**, with its untouched baseline retained.

## Portable fallback that already displays on the Snapdragon

Use a new empty Model-space drawing and run:

```text
MARITIMEDEMO
```

Open `examples\assembly\01-congested-room.json`. This is an invented layout with a deliberate detour, not an organizer drawing. The verified Mac result reduces the rounded route from about **17.08 m and four bends** to **13.42 m and two bends**. Use the actual displayed laptop result in the pitch.

Choose **Both**, compare **Original** and **Proposal**, then **Save**. If the scene is hard to see, select **Conceptual** instead of 2D Wireframe and fit the drawing with Zoom Extents. The team's earlier blank display recovered after a tab/view refresh; its exact cause was not established. A saved drawing and fallback recording avoid losing presentation time to display troubleshooting.

Other public examples cover a collision repair, a blocked passage, an elevation change and an already-good route. They test behavior; they do not replace the required demonstration on the provided system.

## Submit the pitch

The supplied template asks the team to:

1. Keep one presentation file and rename it **Team 7 - [solution name] - Pitch**.
2. Remove the instruction slide and replace all guidance text.
3. Share the deck as **Anyone with the link - Viewer**.
4. Paste the same link into **Pitch Deck** at the top of the team's Notion page.
5. Submit by **Sunday at 10:00 AM**, continuing to update that same file rather than sending a new link. The template does not specify a timezone; follow the event's announced deadline.

Cover team and solution, challenge fit, novelty, unique selling points, and actionability/scalability when required for phase 2. Include a short clear product recording or equivalent high-quality product visual. No deck or Notion submission is performed by these repository instructions.

Explain the implemented method as geometric search with constraints. It does not train on uploaded files. Ask the mentors if their AI criterion needs something beyond search-based optimization rather than claiming a learned model or NPU acceleration.
