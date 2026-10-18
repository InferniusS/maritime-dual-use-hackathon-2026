# Interactive hypothetical obstacles — Edge 5.1

The connected workspace can add, move and remove explicitly editable **hypothetical review obstacles**. Each operation changes a copied engineering environment, automatically analyzes the complete network, and identifies routes for coordinated reoptimization. Native CAD geometry, source route points, component positions and drawing revisions remain unchanged.

## Edit contract

The pure function is `pipe_router.network_obstacles.edit_obstacle(network, operation)`. The local service exposes it through `POST /api/network/obstacles/edit` with `{ "network": ..., "operation": ... }`.

```json
{
  "action": "add",
  "obstacle_id": "review-wall-1",
  "obstacle": {
    "id": "review-wall-1",
    "kind": "wall",
    "min": [3000, 1200, 1200],
    "max": [4000, 1800, 1800],
    "simulation": true,
    "editable": true,
    "fixed": false,
    "protected": false
  }
}
```

Move with `{ "action": "move", "obstacle_id": "review-wall-1", "translation_mm": [0, 1000, 0] }`. Remove with `{ "action": "remove", "obstacle_id": "review-wall-1" }`. Geometry and translations in these operations are always global WCS millimetres; declared source metre/inch coordinates are normalized first. A move preserves dimensions and translates existing capsule geometry, clipping bounds and supplied floor references. It does not resize an obstacle or modify associated equipment connections.

New obstacles require explicit finite `min` and `max` coordinates. Missing geometry is rejected rather than assigned a guessed size. `kind: "unknown"` or explicitly unverified geometry keeps its supplied conservative bounds active and marks environment coverage UNVERIFIED. Removing an unknown review envelope does not establish that omitted or uncertain industrial geometry is now verified.

## Source protection and permissions

Existing obstacles require both `editable: true` and `fixed: false` before a move or removal. Protected objects cannot change. Any obstacle carrying a CAD source handle or source-instance identity is immutable through this editor, even if other flags claim it is editable. Ship walls, bulkheads, decks, stiffeners and structure remain protected.

The exception is an explicitly requested simulation obstacle created through the editor. The module assigns `origin: "designer-review"` and `environment_edit_created: true`. Editing a hypothetical structural object also requires `simulation: true`, editable/nonfixed/nonprotected flags, no CAD identity, and its recorded creation operation. Supplying those flags on an existing ship wall without a matching creation record does not grant permission. This distinction changes the hypothetical model only; it grants no authorization to move actual ship structure.

Every actual edit increments `environment_revision` independently of `drawing.revision`. The source digest covers the edited environment and its audit metadata, so proposals calculated against an earlier environment become stale. The response sets `proposal_invalidated: true` and `native_cad_modified: false`. The service cancels calculation/review state as appropriate before the UI uses the new source baseline. A zero-distance move is rejected rather than creating a meaningless revision.

Opening a source without an existing `analysis_session_id` assigns an opaque local session ID. Edited snapshots retain that ID and their environment revision. Explicitly reopening the original example or drawing starts an independent analysis session, preserving the original drawing identity and geometry while keeping older results stale. Reinspection of an existing session does not reset its revision ledger. This session ID is application state, not inferred engineering metadata.

`environment_edits` retains active review-object creation proofs plus the latest 64 other changes. There are at most 64 active review-created simulation objects, 128 retained records and 128 KB of audit metadata. Older noncreation records may be trimmed to respect the byte budget. Creation proofs for active hypothetical walls survive a long sequence of moves; a budget failure preserves the original source unchanged.

## Affected routes and automatic analysis

Affected-route detection conservatively checks:

- Finite pipe straights and sampled physical elbow envelopes against the old and new obstacle bounds, including the configured 50.8 mm surface clearance.
- Declared finite component envelopes, including valves, tees, flanges, reducers, elbows and physical supports, and their incident routes.
- Potential direct endpoint corridors through a removed or moved-away blocker, so an existing detour can be considered for shortening.
- One-hop graph neighbors sharing endpoints with affected runs, so incident component changes can be reviewed together.

Missing physical component or fitting dimensions prevent excluding their incident routes from consideration. If an uncertain component has no confirmed incident route, the whole network remains in the review scope. Obstacle capsule geometry is conservatively enclosed by its supplied AABB for affected-route discovery. Consequently the affected set can include routes that ultimately need no change. Detection does not claim a minimal affected set or approve an alternative route.

All surrounding piping systems, fixed components and obstacles remain in the returned network. The existing coordinated optimizer can receive `affected_span_ids` as `selected_span_ids`; unselected geometry remains physical context. Whole-network validation still decides whether a route proposal is acceptable. Changing an obstacle does not itself change pipe routes or fabricate an optimization result.

The function independently validates the original network against its original baseline and the edited network against the **new environment baseline**. This permits deliberate obstacle what-if changes without misrepresenting them as an optimizer illegally changing its fixed source environment. Both reports retain failures, measured clearances and UNVERIFIED requirements.

The response includes:

| Field | Meaning |
|---|---|
| `network` | Complete normalized edited source, retaining all context. |
| `original_validation`, `validation` | Independently calculated before/after engineering reports. |
| `affected_span_ids` | Union of directly affected spans and connected neighbors. |
| `directly_affected_span_ids`, `connected_neighbor_span_ids` | Explicit reason categories for review and selection. |
| `affected_detection` | Conservative method, measured work count, unknown component IDs and reasons by span. |
| `change` | Action, obstacle identity, exact before/after bounds, geometry digests and translation where relevant. |
| `environment_revision`, `source_digest`, `previous_source_digest` | Separate environment version and revision-bound proposal invalidation evidence. |
| `collision_delta` | Independent validation status, hard-failure difference, unique reported collision/clearance pairs, resolved/new pairs and measured minimum surface gaps. |

Pair counts deduplicate repeated fitting/structure findings for the same reported component/obstacle ID set. They count reported physical pairs, not arbitrary sampled arc intersections. An absent measured minimum remains `null`; it is never replaced with a fabricated clearance.

## Computed regressions

```bash
python -m pytest -q tests/test_network_obstacles.py
```

Synthetic tests add a wall across a direct route, calculate a genuine detour with the existing coordinated optimizer and independently check it. A sealed wall case preserves the blocked original and reports an unsuccessful bounded search. Search exhaustion or a time limit does not prove continuous-space infeasibility. Additional regressions cover physical elbow/component affectedness, connected neighbors, capsule/clipping translation, fixed/protected/CAD-source rejection, explicit simulation creation, unknown coverage, revision/digest changes, history budgets and input/source immutability.

These checks do not exercise native Windows AutoCAD. The editor provides hypothetical engineering analysis and CAD-compatible proposals; it never moves an actual wall, removes a native CAD object or grants construction approval.
