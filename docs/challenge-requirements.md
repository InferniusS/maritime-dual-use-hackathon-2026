# Confirmed Track 2 requirements

The screenshot supplied by the team already contains the success criteria and minimum requirements. The slide deck adds the intended workflow and scope. These are the development baseline. Do not ask the team to collect these requirements again.

## Sources reviewed

- The user-supplied Notion screenshot of the success criteria and constraints
- The eight-page Piping Layout Optimization presentation supplied by the user
- [Build Resources](https://mindthebridge.notion.site/Build-Resources-0a16d868852445dabb0c25912590de56)
- [Hack Pack](https://app.notion.com/p/mindthebridge/Hack-Pack-a85a519c9a514f599bd0ea1a3435d3f2)

The Notion page text was rechecked in the browser. Its embedded PDFs reported loading errors. This review does not claim to have inspected every linked document or the private team page.

## Requirements and current coverage

| Documented requirement | Current coverage | Work still needed |
| --- | --- | --- |
| Improve an existing layout in its intended spaces | Joined CAD chains can be tested in a local box. A generated detour in real CAD context has been shortened | Preserve real intermediate connections and replace assumed search bounds with permitted routing spaces |
| Keep piping component sizes unchanged | One outer diameter stays fixed within each routing input | Verify all component sizes from real data |
| Stay inside ship structure | Synthetic boxes and assumed local CAD test boxes | Use actual ship boundaries and allowed passages |
| Avoid clashes with structure and other systems | Continuous box checks. CAD candidate tests use boxes around nearby extracted solid samples | Include missing structure and nearby systems. Verify complete physical envelopes |
| Do not route a small pipe inside a larger pipe or duct | Occupied boxes are treated as blocked space | Explicitly represent the occupied envelopes of real pipes and ducts |
| Use standard commercially available fittings | Manufacturer dimensions shown as a reference. Optional bend tangent length check | Select appropriate fittings and check their full physical shapes |
| Preserve system function and correct drain pitch | Gravity checks enforce start-to-end downhill flow and a selected minimum slope | Verify real flow direction and required slope. Add other functional checks |
| Work across piping systems | Pressure and gravity searches exercised on eight synthetic layouts | Demonstrate reviewed real inputs and additional system rules |
| Change the CAD model or provide precise implementation information | Centerline DXF and exact coordinates. CAD candidate result JSON retains source handles and snapshot references | Verify component identity and supply complete fitting changes for CAD implementation |
| Live run on the supplied model with results shown in AutoCAD or Navisworks | Live browser routing with a bridge from selected CAD candidates to provisional tests | Demonstrate a reviewed real run and verify results in the required CAD application |
| Run on the provided laptop | Mac execution tested | Verify the complete workflow on the event laptop |
| Flag issues needing designer intervention | Reports identify unverified chains and endpoint contacts. Bounded unchanged connection stubs require an explicit local assumption. Source references remain attached | Review component identity and resolve unsupported engineering checks |

The challenge describes AI optimization. The reviewed material does not specify training a new model or using a particular neural model. The implementation must explain its optimization approach accurately.

## Hardware decision

The core application must run on the provided laptop. Both public Notion pages state that hardware is optional. Build Resources specifically describes hardware as optional for the piping track.

Use the Mac for development and testing. Prepare the event laptop as the execution and demonstration target. IQ9 may be an optional extension. Do not make it a dependency unless organizers give updated instructions. No board performance benefit has been measured.

## Remaining details to locate

These details are not specified in the reviewed screenshot or presentation. They may be in the referenced standards or another resource. Search the provided materials first. Ask the mentor only for unresolved details.

- Model units and alignment between system drawings
- The actual engineering standards referenced by the brief
- Required numeric drain slopes and flow direction
- Any required additional clearance beyond avoiding physical clashes
- Permitted fitting catalogs or fitting dimensions
- Which source geometry defines the ship boundaries and surrounding obstacles

The team has authorized continuing with provisional units and rules. The current 150 mm and 50 mm clearances are test settings. The default 1 percent drain slope is also provisional. They are not official requirements. Units can be corrected by rescaling all coordinates and lengths and running the checks again.

## Next milestone

The current iteration has a checked three-component route from the real cooling drawing. Its original centerline is 3679.25 mm long. All 36 nearby obstacle boxes remain active at 50 mm provisional clearance. Four start contacts are treated as bounded fixed connections for the local test. Their engineering identities remain unverified.

A clearly labeled artificial detour increases this route to 4179.25 mm. The search reduces it to 3728.30 mm. That is recovery from a generated change. It does not beat the untouched CAD baseline. The unchanged baseline remains the preferred result when tested on its own.

The next milestones are to retain equipment and branch connections through longer routes and use more complete collision geometry. Then provide fitting changes that a designer can implement and verify the result in the required CAD application on the supplied laptop. The present local box and sample-based obstacle boxes do not establish full ship coverage.
