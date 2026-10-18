"""Request-scoped CPU, allocation and result limits for engineering work."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import json
import math
import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WorkLimits(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, strict=True, frozen=True)
    deadline_s: float = Field(default=30., ge=.01, le=120)
    max_axis_points: int = Field(default=20_000, ge=8, le=100_000)
    max_grid_cells: int = Field(default=5_000_000, ge=100, le=20_000_000)
    max_expanded: int = Field(default=60_000, ge=1, le=200_000)
    max_pair_tests: int = Field(default=5_000_000, ge=100, le=50_000_000)
    max_issues: int = Field(default=5_000, ge=1, le=50_000)
    max_result_items: int = Field(default=100_000, ge=100, le=500_000)
    max_response_bytes: int = Field(default=8_000_000, ge=1024, le=16_000_000)


DEFAULT_LIMITS = WorkLimits()


class WorkLimitExceeded(ValueError):
    code = 'work_limit_exceeded'


class WorkCancelled(ValueError):
    code = 'cancelled'


@dataclass(frozen=True)
class _WorkContext:
    limits: WorkLimits
    deadline: float
    cancellations: tuple[Any, ...]


_current: ContextVar[_WorkContext | None] = ContextVar('pipeforge_work_context', default=None)


def current_limits() -> WorkLimits:
    context = _current.get()
    return context.limits if context else DEFAULT_LIMITS


def remaining_seconds() -> float | None:
    context = _current.get()
    return max(0., context.deadline - time.monotonic()) if context else None


@contextmanager
def work_scope(limits: WorkLimits | dict | None = None, cancel=None):
    """Nested work cannot extend an existing deadline or enlarge its budgets.

    ContextVar isolation avoids changing another thread or asynchronous task's
    configuration. Worker threads must enter their own scope explicitly.
    """
    parent = _current.get()
    if limits is None:
        checked = parent.limits if parent else DEFAULT_LIMITS
    else:
        checked = limits if isinstance(limits, WorkLimits) else WorkLimits.model_validate(limits)
    if parent:
        checked = WorkLimits(**{key: min(value, getattr(parent.limits, key))
                                for key, value in checked.model_dump().items()})
    deadline = time.monotonic() + checked.deadline_s
    if parent:
        deadline = min(deadline, parent.deadline)
    cancellations = (() if parent is None else parent.cancellations) + (() if cancel is None else (cancel,))
    token = _current.set(_WorkContext(checked, deadline, cancellations))
    try:
        checkpoint('before work')
        yield checked
        checkpoint('after work')
    finally:
        _current.reset(token)


def checkpoint(stage: str = '') -> None:
    context = _current.get()
    if context is None:
        return
    if any(item.is_set() for item in context.cancellations):
        raise WorkCancelled('Engineering work cancelled' + (f' ({stage})' if stage else ''))
    if time.monotonic() >= context.deadline:
        raise WorkLimitExceeded('Engineering computation deadline exceeded' + (f' ({stage})' if stage else ''))


def preflight_model(model, operation: str = 'analyze') -> dict:
    """Bound full-context segment comparisons before any geometry loops.

    Counts are conservative estimates, not a claimed number of executed tests.
    They include directed cross-pipe, self, obstacle and fitting comparisons.
    """
    checkpoint('model preflight')
    segments = [max(0, len(pipe.points) - 1) for pipe in model.pipes]
    total = sum(segments)
    bends = sum(max(0, len(pipe.points) - 2) for pipe in model.pipes)
    cross = sum(count * (total - count) for count in segments)
    own = sum(max(0, (count - 1) * (count - 2) // 2) for count in segments)
    obstacle = total * len(model.obstacles)
    fitting = bends * (total + len(model.obstacles))
    # Clearance evidence and independent checks can revisit the same pairs.
    estimated = 2 * (cross + own + obstacle + fitting)
    if estimated > current_limits().max_pair_tests:
        raise WorkLimitExceeded(f'{operation} model requires an estimated {estimated} pair tests; limit is {current_limits().max_pair_tests}')
    return dict(segments=total, estimated_pair_tests=estimated)


def preflight_grid(model, resolution: float, margin: float = 0., dimensions: int = 3) -> tuple[int, ...]:
    """Check axis allocation and grid size before constructing numpy arrays."""
    checkpoint('before grid allocation')
    if dimensions not in (2, 3) or not math.isfinite(resolution) or resolution <= 0 or not math.isfinite(margin) or margin < 0:
        raise ValueError('Invalid routing grid parameters')
    limits = current_limits()
    counts = []
    cells = 1
    for axis in range(dimensions):
        span = model.bounds.max[axis] - model.bounds.min[axis] - 2 * margin + 1e-8
        quotient = max(0., span) / resolution
        if not math.isfinite(quotient) or quotient > limits.max_axis_points - 2:
            raise WorkLimitExceeded(f'Routing axis exceeds {limits.max_axis_points} allocated points')
        count = max(0, math.ceil(quotient)) + 2
        counts.append(count)
        cells *= count
        if cells > limits.max_grid_cells:
            raise WorkLimitExceeded(f'Routing grid exceeds {limits.max_grid_cells} cells')
    return tuple(counts)


def check_expanded(count: int) -> None:
    checkpoint('routing search')
    if count >= current_limits().max_expanded:
        raise WorkLimitExceeded(f'Routing search reached {current_limits().max_expanded} expanded states')


def append_issue(issues: list, item: dict) -> None:
    checkpoint('issue generation')
    if len(issues) >= current_limits().max_issues:
        raise WorkLimitExceeded(f'Engineering findings exceed {current_limits().max_issues}; analysis remains unresolved')
    issues.append(item)


def check_issue_count(count: int) -> None:
    checkpoint('issue aggregation')
    if count > current_limits().max_issues:
        raise WorkLimitExceeded(f'Engineering findings exceed {current_limits().max_issues}; analysis remains unresolved')


def check_result(result: Any) -> int:
    """Reject oversized outputs instead of silently returning truncated checks."""
    limits = current_limits()
    stack = [(result, 0)]
    items = 0
    while stack:
        value, depth = stack.pop()
        items += 1
        if items % 128 == 0:
            checkpoint('result validation')
        if depth > 64 or items > limits.max_result_items:
            raise WorkLimitExceeded(f'Engineering result exceeds {limits.max_result_items} items or 64 nesting levels')
        if isinstance(value, (dict, list, tuple)) and items + len(stack) + len(value) > limits.max_result_items:
            raise WorkLimitExceeded(f'Engineering result exceeds {limits.max_result_items} items')
        if isinstance(value, dict):
            if isinstance(value.get('issues'), list):
                check_issue_count(len(value['issues']))
            stack.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, (list, tuple)):
            stack.extend((item, depth + 1) for item in value)
    size = 0
    encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    for index, chunk in enumerate(encoder.iterencode(result)):
        if index % 128 == 0:
            checkpoint('result serialization')
        size += len(chunk.encode('utf-8'))
        if size > limits.max_response_bytes:
            raise WorkLimitExceeded(f'Engineering response exceeds {limits.max_response_bytes} bytes; result remains unresolved')
    checkpoint('result complete')
    return size
