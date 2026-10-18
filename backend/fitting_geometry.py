"""Shared conservative fitting occupancy, in canonical meters.

A tangent circular fillet is inside the triangle formed by its intersection
vertex and two tangent points. Both tangent points are R*tan(turn/2) from
the vertex. A sphere with that radius therefore encloses the entire arc;
pipe radius and the declared connection/tangent allowances expand its sweep.
Keeping at least R retains the established conservative right/acute-angle
enclosure. These are bounds, not manufactured fitting solids.
"""
from dataclasses import dataclass
from math import dist
from .catalog import select_fitting, takeout
from .geometry import angle, segment_distance
from .work_limits import checkpoint


@dataclass(frozen=True)
class FittingEnvelope:
    center: tuple[float, float, float]
    radius: float
    vertex_index: int


def fitting_extent(fitting, turn):
    """Radius about the intersection vertex, excluding pipe radius/clearance."""
    allowances = fitting.connection_length + fitting.minimum_tangent
    return max(fitting.radius + allowances, takeout(fitting, turn))


def fitting_envelopes(pipe):
    envelopes = []
    for index, (a, b, c) in enumerate(zip(pipe.points, pipe.points[1:], pipe.points[2:]), 1):
        if index % 256 == 1:
            checkpoint('fitting envelope extraction')
        turn = angle(a, b, c)
        if turn < 1e-5:
            continue
        fitting = select_fitting(pipe, turn)
        if fitting is not None:
            envelopes.append(FittingEnvelope(tuple(b), pipe.outer_diameter / 2 + fitting_extent(fitting, turn), index))
    return envelopes


def geometry_metadata_missing(pipe):
    """Missing inputs on which this pipe's current physical envelope depends."""
    missing = ['outer_diameter'] if 'outer_diameter' in pipe.dimension_metadata()['missing'] else []
    for a, b, c in zip(pipe.points, pipe.points[1:], pipe.points[2:]):
        turn = angle(a, b, c)
        if turn < 1e-5:
            continue
        fitting = select_fitting(pipe, turn)
        # No matching catalog entry remains an unsupported-fitting failure;
        # unrelated default entries must not conceal that failure as unknown.
        candidates = [fitting] if fitting is not None else []
        for item in candidates:
            missing.extend(f'fittings[{item.id}].{name}' for name in item.metadata_status(('angle', 'radius'))['missing'])
    return sorted(set(missing))


def conservative_pipe_margin(pipe, clearance=0.):
    """AABB expansion enclosing existing straight and supported fitting geometry.

    Retain catalog radii even when the current route is straight, and expand
    further for actual obtuse bends. Useful for revision-cache dependencies.
    """
    radius = pipe.outer_diameter / 2
    extent = max((f.radius + f.connection_length + f.minimum_tangent for f in pipe.fittings), default=0.)
    extent = max(extent, max((e.radius - radius for e in fitting_envelopes(pipe)), default=0.))
    return radius + extent + clearance


def pipe_pair_clearance(pipe, other):
    """Symmetric separation of all straight capsules and fitting spheres."""
    own_segments = list(zip(pipe.points, pipe.points[1:]))
    other_segments = list(zip(other.points, other.points[1:]))
    own_fittings, other_fittings = fitting_envelopes(pipe), fitting_envelopes(other)
    own_radius, other_radius = pipe.outer_diameter / 2, other.outer_diameter / 2
    measured = float('inf')
    for a, b in own_segments:
        checkpoint('independent neighbor separation')
        for index, (c, d) in enumerate(other_segments):
            if index % 256 == 0:
                checkpoint('neighbor separation pairs')
            measured = min(measured, segment_distance(a, b, c, d) - own_radius - other_radius)
    for fitting in own_fittings:
        checkpoint('own fitting separation')
        measured = min(measured, min(segment_distance(fitting.center, fitting.center, c, d) - fitting.radius - other_radius
                                     for c, d in other_segments))
    for fitting in other_fittings:
        checkpoint('surrounding fitting separation')
        measured = min(measured, min(segment_distance(a, b, fitting.center, fitting.center) - own_radius - fitting.radius
                                     for a, b in own_segments))
    for fitting in own_fittings:
        checkpoint('fitting pair separation')
        for other_fitting in other_fittings:
            measured = min(measured, dist(fitting.center, other_fitting.center) - fitting.radius - other_fitting.radius)
    return measured
