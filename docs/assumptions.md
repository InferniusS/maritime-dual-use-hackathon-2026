# Prototype assumptions

We are continuing with editable defaults while the source units and detailed engineering requirements remain unconfirmed. Passing these checks means that a result passed the implemented prototype checks with the selected settings. It does not mean that it meets the ship's design standards.

The settings are stored in [rule-profiles.json](../examples/rule-profiles.json). Keep the selected profile and any overrides with every result so another person can reproduce the calculation.

## Units and defaults

Millimeters are the working unit for this prototype. This is our choice for implementation. It is not a finding about the supplied CAD files.

| Setting | Prototype value | Meaning |
| --- | --- | --- |
| Clearance | 50 mm | Gap between the pipe outer surface and modeled obstacles or the permitted routing boundary |
| Grid spacing | 250 mm | Search resolution |
| Turn penalty | 300 mm | Cost added to the route score for a direction change |
| Minimum gravity slope | 0.01 | Assumed vertical fall per horizontal distance in the flow direction |

The slope is 1 percent or 10 mm per horizontal meter. It is a chosen test value. We have not established that it is correct for any supplied drain. Flow is assumed to go from the start connection to the end connection. Positive z points upward.

The clearance is also a chosen test value. It is not presented as the most common industry requirement. The pipe diameter must represent the actual outside envelope used for collision checks. If insulation or other covering is omitted then the check does not reserve room for it.

The grid spacing and turn penalty are algorithm settings. They are not engineering standards. A search that finds no route on this grid does not prove that no physical route exists.

## Changing units later

Changing units is possible but changing a label is not enough. Coordinates and pipe diameters must be interpreted together. Obstacles and every linear rule value need the same interpretation.

If a drawing is later confirmed to be in inches then one source coordinate unit becomes 25.4 mm. Its geometry should be converted to the internal millimeter representation. A separately configured 50 mm clearance should stay 50 mm. It should not be multiplied by 25.4 merely because the source drawing was in inches.

Confirming the units changes physical dimensions and can change the result. Recheck all routes after correcting the scale. Save the source unit choice and conversion factor with the imported data. Keep the original CAD file unchanged.

## Small fitting reference

We recorded four entries from the manufacturer's [Weldbend metric catalog](https://www.weldbend.com/Catalog_63_English_Metric.pdf). The [Weldbend catalog page](https://www.weldbend.com/catalog.php) links this publication. These are STD long radius 90 degree butt-weld elbows. Dimensions below are the published millimeter values.

| Nominal size | Outside diameter | Center to end |
| --- | --- | --- |
| NPS 1 | 33.4 mm | 38 mm |
| NPS 2 | 60.3 mm | 76 mm |
| NPS 3 | 88.9 mm | 114 mm |
| NPS 4 | 114.3 mm | 152 mm |

Nominal pipe size is a size designation. It is not the measured outside diameter. The catalog calls the listed length center to end. We have not stored it as a separately verified bend radius. The source catalog uses rounded millimeter values here.

This is a small dimension reference. It is not the ship's approved parts list. An existing pipe must not be resized to match one of these entries. An unmatched diameter requires a matching fitting record or a review flag.

Selecting a catalog entry does not establish material compatibility or pressure suitability. It also does not establish current stock. A buildable result still needs the fitting's real occupied space checked against surrounding objects. Adjacent fittings need enough space and fabrication allowances too.

The profiles and catalog are data. Listing a rule or fitting here does not establish that the corresponding check has been implemented by every part of the prototype.

## What remains unverified

- The supplied drawings' physical units and shared coordinate system
- The applicable ship standards and any system specific clearance values
- Actual drain flow directions and required slopes
- Approved fittings and the dimensions of fittings already in the model
- Complete nearby structure and other equipment needed for collision checks
- Hydraulic performance and installation access

These unknowns do not prevent prototype development. Reports should show them as assumptions or unchecked items until evidence resolves them.
