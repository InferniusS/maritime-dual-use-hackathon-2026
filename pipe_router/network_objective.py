"""Dimensionless network objectives, separate from engineering acceptance.

All scores compare against one immutable source network. Hydraulic estimates
are Darcy-Weisbach straight-pipe friction plus supplied bend K coefficients;
they are not a network flow solution or a pump-head calculation. Missing
hydraulic evidence is omitted explicitly, never replaced with guessed fluid,
wall thickness, flow or fitting data.
"""
from __future__ import annotations

import math

STRATEGIES = {
    "balanced": {"length": .50, "bends": .22, "elevation": .06, "hydraulic": .10, "modifications": .08, "constructability": .04},
    "length": {"length": 1.},
    "bends": {"length": .05, "bends": .95},
    "hydraulic": {"length": .03, "bends": .02, "hydraulic": .95},
    "minimal_modifications": {"length": .08, "bends": .02, "modifications": .90},
    # Feasibility remains a hard gate. This strategy ranks feasible alternatives
    # by the amount of intervention, then their geometric complexity.
    "feasibility": {"length": .10, "bends": .10, "modifications": .60, "constructability": .20},
}
TERMS = ("length", "bends", "elevation", "hydraulic", "modifications", "constructability")


def validate_weights(objective_weights):
    if not isinstance(objective_weights, dict) or not objective_weights or set(objective_weights) - set(TERMS):
        raise ValueError("objective_weights must name supported network objective terms")
    for value in objective_weights.values():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1000:
            raise ValueError("objective_weights must be finite values between 0 and 1000")
    weights = {term: float(objective_weights.get(term, 0.)) for term in TERMS}
    if not any(weights.values()):
        raise ValueError("objective_weights must include a positive weight")
    return weights


def resolve_objective(strategy=None, objective_weights=None):
    if strategy is not None and not isinstance(strategy, str):
        raise ValueError("strategy must be a supported name")
    strategy = {"modification": "minimal_modifications", "modifications": "minimal_modifications", "balanced_engineering": "balanced"}.get(strategy, strategy) or "balanced"
    if strategy not in STRATEGIES:
        raise ValueError("strategy must be length, bends, hydraulic, minimal_modifications, balanced or feasibility")
    weights = {term: STRATEGIES[strategy].get(term, 0.) for term in TERMS}
    if objective_weights is not None:
        weights = validate_weights(objective_weights)
    return {"strategy": strategy, "weights": weights}


def route_length(points):
    return sum(math.dist(a, b) for a, b in zip(points, points[1:]))


def direction_changes(points):
    count = 0
    for a, b, c in zip(points, points[1:], points[2:]):
        u, v = [y-x for x, y in zip(a, b)], [y-x for x, y in zip(b, c)]
        la, lb = math.sqrt(sum(x*x for x in u)), math.sqrt(sum(x*x for x in v))
        if la and lb and sum(x*y for x, y in zip(u, v))/(la*lb) < 1-1e-8:
            count += 1
    return count


def hydraulic_estimate(network):
    """Return an aggregate segment-loss estimate only with explicit evidence."""
    rows, total, complete = [], 0., True
    for span in network["spans"]:
        data = span.get("hydraulics", {})
        missing = []
        values = {}
        for key in ("inner_diameter_mm", "density_kg_m3", "dynamic_viscosity_pa_s", "volumetric_flow_m3_s", "roughness_mm"):
            value = data.get(key) if isinstance(data, dict) else None
            positive = key != "roughness_mm"
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or (value <= 0 if positive else value < 0):
                missing.append(key)
            else:
                values[key] = float(value)
        if not isinstance(data, dict) or not isinstance(data.get("source"), str) or not data.get("source", "").strip():
            missing.append("source")
        bends = direction_changes(span["points"])
        k = data.get("bend_loss_coefficient") if isinstance(data, dict) else None
        if isinstance(k, bool) or not isinstance(k, (int, float)) or not math.isfinite(k) or k < 0:
            missing.append("bend_loss_coefficient")
        if values.get("inner_diameter_mm", 0) > span["outer_diameter_mm"]:
            missing.append("inner_diameter_not_larger_than_outer_diameter")
        if values.get("roughness_mm", 0) >= values.get("inner_diameter_mm", math.inf):
            missing.append("roughness_smaller_than_inner_diameter")
        if missing:
            complete = False
            rows.append({"span_id": span["id"], "status": "UNVERIFIED", "missing_or_invalid": missing, "pressure_loss_pa": None})
            continue
        try:
            diameter = values["inner_diameter_mm"]/1000.
            flow = values["volumetric_flow_m3_s"]
            velocity = 4*flow/(math.pi*diameter*diameter)
            reynolds = values["density_kg_m3"]*velocity*diameter/values["dynamic_viscosity_pa_s"]
            if reynolds < 2300:
                friction = 64/reynolds
                regime = "laminar"
            else:
                roughness_ratio = values["roughness_mm"]/values["inner_diameter_mm"]
                friction = .25/(math.log10(roughness_ratio/3.7 + 5.74/reynolds**.9)**2)
                regime = "turbulent_approximation" if reynolds >= 4000 else "transition_approximation"
            loss = (friction*route_length(span["points"])/1000./diameter + bends*k)*values["density_kg_m3"]*velocity**2/2
            finite = all(math.isfinite(value) and value > 0 for value in (loss, velocity, reynolds, friction))
        except (ArithmeticError, ValueError):
            finite = False
        if not finite:
            complete = False
            rows.append({"span_id": span["id"], "status": "UNVERIFIED", "missing_or_invalid": ["finite_hydraulic_result"], "pressure_loss_pa": None})
            continue
        total += loss
        rows.append({"span_id": span["id"], "status": "ESTIMATED", "pressure_loss_pa": loss, "velocity_m_s": velocity,
                     "reynolds_number": reynolds, "darcy_friction_factor": friction, "regime": regime, "source": data["source"]})
    return {"status": "ESTIMATED" if complete else "UNVERIFIED", "aggregate_segment_loss_pa": total if complete else None, "spans": rows,
            "scope": "Sum of segment friction and declared bend losses at supplied fixed flows; not solved network pressure, flow balance, pump head, valve loss or compliance"}


def _geometric(network, reference):
    refs = {s["id"]: s for s in reference["spans"]}
    nodes = {n["id"]: n for n in reference["nodes"]}
    length = sum(route_length(s["points"]) for s in network["spans"])
    bends = sum(direction_changes(s["points"]) for s in network["spans"])
    up = network.get("coordinate_system", {}).get("up", [0, 0, 1])
    up_length = math.sqrt(sum(v*v for v in up)) or 1.
    elevation = sum(abs(sum((y-x)*z/up_length for x, y, z in zip(a, b, up))) for s in network["spans"] for a, b in zip(s["points"], s["points"][1:]))
    changed = [s for s in network["spans"] if s["id"] in refs and s["points"] != refs[s["id"]]["points"]]
    modified = sum(route_length(s["points"]) for s in changed)
    moved = sum(n["id"] in nodes and n["position"] != nodes[n["id"]]["position"] for n in network["nodes"])
    # This is a declared geometric intervention/complexity proxy, not labour,
    # fabrication cost, a weld schedule or commercial fitting availability.
    replaced_length = sum(route_length(refs[s["id"]]["points"]) for s in changed)
    intervention = len(changed) + moved + replaced_length/max(1., sum(route_length(s["points"]) for s in reference["spans"]))
    complexity = bends + len(network["spans"]) + moved
    return {"length": length, "bends": bends, "elevation": elevation, "modifications": intervention, "constructability": complexity,
            "modified_length_mm": modified, "changed_span_count": len(changed), "moved_node_count": moved}


def network_metrics(network, reference=None, *, strategy=None, objective_weights=None, validation=None):
    reference = reference or network
    config = resolve_objective(strategy, objective_weights)
    values, source_values = _geometric(network, reference), _geometric(reference, reference)
    hydraulic, source_hydraulic = hydraulic_estimate(network), hydraulic_estimate(reference)
    values["hydraulic"], source_values["hydraulic"] = hydraulic["aggregate_segment_loss_pa"], source_hydraulic["aggregate_segment_loss_pa"]
    # Stable source-only scales keep all alternatives comparable. A zero bend
    # or elevation baseline uses one per span / baseline length, respectively.
    scales = {"length": max(1., source_values["length"]), "bends": max(1., source_values["bends"], len(reference["spans"])),
              "elevation": max(1., source_values["elevation"], .1*source_values["length"]),
              "hydraulic": max(1., source_values["hydraulic"] or 0.),
              "modifications": max(1., 2*len(reference["spans"]) + len(reference["nodes"])),
              "constructability": max(1., source_values["constructability"])}
    unavailable = [term for term in TERMS if values[term] is None or source_values[term] is None]
    effective = {term: weight if term not in unavailable else 0. for term, weight in config["weights"].items()}
    fallback = False
    if not any(effective.values()):
        effective["length"], fallback = 1., True
    total = sum(effective.values())
    effective = {term: weight/total for term, weight in effective.items()}
    terms = {term: {"raw": values[term], "normalization_scale": scales[term], "normalized": values[term]/scales[term] if values[term] is not None else None,
                    "weight": effective[term], "contribution": effective[term]*values[term]/scales[term] if values[term] is not None else 0.,
                    "status": "UNVERIFIED" if term in unavailable else "ESTIMATED" if term == "hydraulic" else "GEOMETRIC_PROXY" if term in ("modifications", "constructability") else "CALCULATED"} for term in TERMS}
    preferences = sum(c.get("status") == "FAIL" and c.get("severity") == "preference" for c in (validation or {}).get("checks", []))
    preference_cost = .001*preferences
    return {"length_mm": values["length"], "direction_changes": values["bends"], "fitting_requirements": values["bends"],
            "elevation_change_mm": values["elevation"], "modified_length_mm": values["modified_length_mm"], "changed_span_count": values["changed_span_count"],
            "moved_node_count": values["moved_node_count"], "objective": sum(term["contribution"] for term in terms.values())+preference_cost,
            "objective_units": "dimensionless source-normalized score", "objective_strategy": config["strategy"], "objective_weights": config["weights"],
            "effective_objective_weights": effective, "objective_terms": terms, "normalization_scales": scales,
            "unverified_objective_terms": [term for term in unavailable if config["weights"][term] > 0], "hydraulic_evidence": hydraulic,
            "objective_fallback": "length because no requested term has sufficient evidence" if fallback else None,
            "preference_failures": preferences, "preference_contribution": preference_cost,
            "objective_notes": ["Hard engineering constraints gate acceptance separately from this score.",
                                "Direction changes are geometric fitting requirements, not verified commercial fitting quantities.",
                                "Modification and constructability terms are geometric proxies, not verified fabrication or installation cost."]}
