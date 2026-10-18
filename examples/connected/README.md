# Synthetic connected networks

These six invented global-WCS millimetre scenes contain no production CAD geometry. Source identifiers beginning `SYN-` identify synthetic entities, not handles in an actual AutoCAD drawing. The permitted-space, component-body and fitting dimensions are explicit demonstration inputs. The provisional fitting catalog is not a ship-approved fabrication catalog.

| Scene | Purpose | Computed outcome |
| --- | --- | --- |
| A — Already good | Straight route between fixed equipment ports | `NO IMPROVEMENT FOUND`; 7000 mm preserved |
| B — Collision repair | Original route crosses a structural block | `IMPROVED`; 7000 → 9000 mm to clear the block |
| C — Connected branch | Three equipment ports, two valves, tee, five connected spans | `IMPROVED`; 13500 → 10500 mm by moving valve A within its permitted space; two spans change |
| D — Blocked | Full-cross-section sealed bulkhead | `NO FEASIBLE ROUTE FOUND`; no proposed geometry |
| E — Multiple systems | Retained 100 mm OD firemain crosses cooling line | `IMPROVED`; 12000 → 13000 mm total network length; firemain preserved |
| F — Directional drainage | Rotated ship axes, reverse storage direction, explicit flow | `NO IMPROVEMENT FOUND`; 200 mm fall over 4000 mm athwartship run exceeds 40 mm required |

The outputs are calculated by `optimize_network` and independently validated. They are not prewritten proposals. The collision-repair examples become longer because collision and clearance are hard constraints. A search failure means no accepted proposal was found within the documented finite search; it is not a proof that every continuous route is impossible.

The support layout, installation-access coverage, fabrication weld information and approval authority are intentionally incomplete. Missing information remains `UNVERIFIED`, including for proposed routes with zero known hard failures. The standards profile records the supplied challenge transcription and unresolved interpretations; it does not claim independent review of original Fincantieri documents.

Run the regression scenarios and repeatable local CPU benchmark from the repository root:

```bash
python -m pytest -q tests/test_connected_routing.py
python scripts/benchmark_connected.py --repeats 3 --output /tmp/pipeforge-connected-benchmark.json
```

The benchmark reports cold and warm time, revision-triggered cache invalidation, separately traced Python allocation, and whole-process high-water RSS where available. RSS includes imports and preceding scenarios. These measurements do not establish performance on a physical IQ-9075 or AutoCAD laptop.
