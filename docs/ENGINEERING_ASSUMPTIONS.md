# Engineering assumptions and limits

PIPEFORGE is an engineering decision-support prototype. A shorter route, a collision-free route, a route satisfying implemented rules, and a fully buildable approved design are different claims. Qualified engineers must review proposed changes before construction.

## Source information

No official CAD, organizer engineering standards, approved fitting catalog, piping schedule, or operating data were present in the supplied repository. The eight procedural scenarios are development fixtures, not representations of a real vessel. Replace their illustrative values with project-approved data before assessing a real installation.

## Coordinates and geometry

Coordinates are Cartesian metres, with positive Z upward. Centerline order defines flow direction. Pipe diameters must retain their original values. Connection coordinates and fixed equipment are immutable during route optimization. Other piping systems remain physical obstacles even when hidden in the viewport.

Straight pipe segments use finite-radius envelopes. The obstacle representation is simplified; imported arbitrary CAD solids, insulation, supports, tolerances, thermal movement, installation access and detailed elbow sweeps require additional engineering checks. A centerline export is an implementation aid and does not establish manufacturability.

## Functional and fitting rules

Drainage slope is project-configurable, measured as downward elevation change divided by horizontal travel along each flowing segment. Demonstration slopes are illustrative values, not universal marine requirements. Geometric pitch checks do not establish drainage capacity, venting performance, or freedom from all hydraulic transients.

Development fitting catalog entries and bend allowances are assumptions. Commercial availability, material compatibility, pressure rating, end connections, class approval and detailed fitting envelopes remain unverified unless an authoritative catalog is supplied. An implemented geometry check must never be interpreted as organizer standards compliance.

## Routing and hydraulics

Grid-based routing depends on resolution, bounds and search limits. Failure to find a route means no route was found within the supported search, not a proof of continuous-space infeasibility. Sequential routing of several pipes depends on order and does not prove a globally optimal network design. The selected objective is a design preference, subordinate to hard constraints.

Hydraulic estimates require known bore, flow, fluid density, viscosity, roughness and fitting loss coefficients. Demonstration inputs are explicitly illustrative. Darcy–Weisbach and local loss estimates do not constitute pump sizing or a complete network solution. Unknown inputs must produce an unverified result, rather than fabricated fluid properties.

## CAD interoperability

Use engineering JSON as the semantic exchange model and DXF as an inspectable CAD interchange. Preserve coordinate system, units, pipe identifiers, dimensions and original/proposed centerlines with change instructions. Native DWG editing and Autodesk/Navisworks integration are not established by DXF export. Optional STEP export uses an isolated OpenCascade adapter. Named straight components, known bores, obstacle boxes, meter units, geometric bounds and reference volume are verified by kernel reimport. These are geometric proxies at corners, not detailed manufactured elbow solids or Autodesk application validation. STEP import and proprietary formats remain unsupported.

### Current search implementation

The general pressure-pipe search is direction-aware A* with conservative enlarged edge envelopes, an eight-second limit and a 60,000-state expansion limit. Gravity routing now searches lateral detours with a horizontal travel/pitch budget and uniform signed fall or rise between fixed endpoint elevations. The entire endpoint elevation slab is conservatively projected into horizontal occupancy, then every lifted candidate is checked independently in 3D. Over/under gravity detours, variable-pitch optimization and arbitrary branch networks remain unsupported. Explicit catalog angular tolerance must accommodate the actual sloped bend; the illustrative 0.5-degree entries are unapproved assumptions. A displayed search failure is unresolved feasibility, not mathematical proof that no physical design exists.
