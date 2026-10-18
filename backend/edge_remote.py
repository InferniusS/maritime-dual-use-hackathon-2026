"""Optional CPU transport with local validation and a complete local fallback."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import platform
import threading
from collections import OrderedDict
import time
import uuid
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import Model, Pipe, Obstacle
from .validation import analyze, assurance, metrics, validate
from .work_limits import WorkLimits, work_scope, checkpoint, preflight_model, check_result, current_limits, remaining_seconds, WorkLimitExceeded, WorkCancelled

PROTOCOL = 'pipeforge-edge/1'
_client_models: OrderedDict[str, tuple[str, Model]] = OrderedDict()
_client_lock = threading.Lock()


class RemoteOptions(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, strict=True)
    clearance: float = Field(default=.1, ge=0, le=1)
    limits: WorkLimits | None = None


class OptimizationOptions(RemoteOptions):
    mode: Literal['shortest', 'bends', 'balanced', 'hydraulic', 'modification'] = 'balanced'
    resolution: float = Field(default=.5, ge=.2, le=2)
    pipe_ids: list[str] | None = Field(default=None, max_length=100)
    alternatives: bool = False
    multiresolution: bool = False
    try_orders: bool = False

    @model_validator(mode='after')
    def distinct_ids(self):
        if self.pipe_ids is not None and len(self.pipe_ids) != len(set(self.pipe_ids)):
            raise ValueError('Duplicate pipe IDs')
        return self


class ModelDelta(BaseModel):
    model_config = ConfigDict(extra='forbid')
    pipes: list[Pipe] = Field(default_factory=list, max_length=100)
    remove_pipe_ids: list[str] = Field(default_factory=list, max_length=100)
    obstacles: list[Obstacle] = Field(default_factory=list, max_length=1000)
    remove_obstacle_ids: list[str] = Field(default_factory=list, max_length=1000)


class ComputeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    request_id: str = Field(min_length=1, max_length=128)
    source_revision: str | None = Field(default=None, max_length=256)
    operation: Literal['analyze', 'optimize']
    model: Model | None = None
    base_digest: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    resulting_digest: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    delta: ModelDelta | None = None
    options: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode='after')
    def checked_options(self):
        if (self.model is None) == (self.delta is None):
            raise ValueError('Supply exactly one full model or model delta')
        if self.delta is not None and (self.base_digest is None or self.resulting_digest is None):
            raise ValueError('A delta requires base and resulting model digests')
        if self.model is not None and self.resulting_digest is None:
            self.resulting_digest = model_digest(self.model)
        parsed = RemoteOptions if self.operation == 'analyze' else OptimizationOptions
        checked = parsed.model_validate(self.options)
        self.options = _option_values(checked)
        if self.model is not None:
            self.options = validate_options(self.operation, self.model, self.options)
        return self


class ComputeResponse(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    protocol: Literal['pipeforge-edge/1']
    request_id: str
    source_revision: str | None = None
    result: dict[str, Any]
    compute_ms: float = Field(ge=0)
    source_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    architecture: str


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')
    protocol: Literal['pipeforge-edge/1']
    status: Literal['ok']
    architecture: str
    processor: Literal['CPU']
    gpu: Literal[False]
    npu: Literal[False]
    operating_system: str
    python_version: str


class Issue(BaseModel):
    model_config = ConfigDict(extra='allow', allow_inf_nan=False)
    id: str
    pipe_id: str
    object_ids: list[str]
    type: str
    severity: Literal['error', 'warning', 'info']
    location: tuple[float, float, float]
    measured: Any
    required: Any
    explanation: str
    suggested_correction: str
    verification_status: Literal['verified', 'unverified']


class Candidate(BaseModel):
    model_config = ConfigDict(extra='allow', allow_inf_nan=False)
    pipe_id: str
    status: Literal['accepted', 'rejected', 'no_route', 'cancelled']
    reason: str
    before: dict
    after: dict
    validation: list[dict]


class OptimizationResult(BaseModel):
    model_config = ConfigDict(extra='allow', allow_inf_nan=False)
    model: Model
    candidates: list[Candidate]
    issues: list[Issue]
    alternatives: list[dict] = Field(default_factory=list, max_length=20)


def validate_options(operation: str, model: Model, options: dict) -> dict:
    if operation == 'analyze':
        return _option_values(RemoteOptions.model_validate(options))
    if operation != 'optimize':
        raise ValueError('Unknown engineering operation')
    parsed = OptimizationOptions.model_validate(options)
    if parsed.pipe_ids is not None and not set(parsed.pipe_ids) <= {p.id for p in model.pipes}:
        raise ValueError('Unknown pipe ID')
    return _option_values(parsed)


def _option_values(options) -> dict:
    values = options.model_dump(exclude={'limits'})
    if options.limits is not None:
        values['limits'] = options.limits.model_dump()
    return values


def health_payload() -> dict:
    return dict(protocol=PROTOCOL, status='ok', architecture=platform.machine(),
                processor='CPU', gpu=False, npu=False,
                operating_system=platform.system(), python_version=platform.python_version())


def normalized_endpoint(endpoint: str) -> str:
    if not isinstance(endpoint, str):
        raise ValueError('Enter an HTTP or HTTPS worker address')
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Use an HTTP or HTTPS address without credentials, query or fragment')
    # Parsing the port also rejects invalid port values before attempting a connection.
    parsed.port
    return endpoint.rstrip('/')


def _timeout(timeout_s: float) -> float:
    value = float(timeout_s)
    if not math.isfinite(value) or not .01 <= value <= 300:
        raise ValueError('Timeout must be between 0.01 and 300 seconds')
    return value


def _auth_headers(access_token=None) -> dict:
    value = access_token or os.environ.get('PIPEFORGE_REMOTE_TOKEN')
    if value is None or value == '':
        return {}
    if hasattr(value, 'get_secret_value'):
        value = value.get_secret_value()
    if not isinstance(value, str) or any(ord(character) < 33 or ord(character) > 126 for character in value):
        raise ValueError('Remote access token must contain printable ASCII without spaces')
    return {'Authorization': 'Bearer ' + value}


def probe_remote(endpoint: str, timeout_s: float = 2, access_token=None) -> dict:
    """Read only health check; no credentials or cloud service is needed."""
    started = time.perf_counter()
    try:
        address = normalized_endpoint(endpoint)
        with httpx.Client(timeout=_timeout(timeout_s), trust_env=False, follow_redirects=False) as client:
            response = client.get(address + '/api/edge/health', headers=_auth_headers(access_token))
            response.raise_for_status()
            result = HealthResponse.model_validate(response.json()).model_dump()
        return dict(available=True, endpoint=address, latency_ms=_ms(started), **result)
    except (httpx.HTTPError, ValueError, TypeError) as error:
        return dict(available=False, endpoint=endpoint, latency_ms=_ms(started), error=_public_error(error))


def model_digest(model: Model) -> str:
    payload = json.dumps(model.model_dump(mode='json'), sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    return hashlib.sha256(payload).hexdigest()


def apply_delta(base: Model, delta: ModelDelta) -> Model:
    raw = base.model_dump()
    for key, updates, removals in [('pipes', delta.pipes, delta.remove_pipe_ids), ('obstacles', delta.obstacles, delta.remove_obstacle_ids)]:
        existing = {item['id']: item for item in raw[key]}
        update_ids = [item.id for item in updates]
        if len(update_ids) != len(set(update_ids)) or len(removals) != len(set(removals)) or set(update_ids) & set(removals):
            raise ValueError('Duplicate or conflicting delta object IDs')
        if not set(removals) <= set(existing):
            raise ValueError('Delta removes an unknown object')
        for object_id in removals:
            del existing[object_id]
        for item in updates:
            existing[item.id] = item.model_dump()
        raw[key] = list(existing.values())
    return Model.model_validate(raw)


def _model_delta(base: Model, current: Model) -> ModelDelta | None:
    if base.model_dump(exclude={'pipes', 'obstacles'}) != current.model_dump(exclude={'pipes', 'obstacles'}):
        return None
    values = {}
    for key in ('pipes', 'obstacles'):
        before = {item.id: item for item in getattr(base, key)}
        after = {item.id: item for item in getattr(current, key)}
        values[key] = [item for object_id, item in after.items() if before.get(object_id) != item]
        values['remove_pipe_ids' if key == 'pipes' else 'remove_obstacle_ids'] = list(before.keys() - after.keys())
    delta = ModelDelta(**values)
    # Reordering also changes the source digest; use a full snapshot in that case.
    return delta if apply_delta(base, delta) == current else None


def compute_local(operation: str, model: Model, options: dict, cancel=None) -> dict:
    with work_scope(options.get('limits'), cancel=cancel):
        preflight_model(model, operation)
        arguments = {key: value for key, value in options.items() if key != 'limits'}
        if operation == 'analyze':
            result = analyze(model, arguments['clearance'])
        else:
            from .advanced import run_optimization
            result = run_optimization(model=model, cancel=cancel, **arguments)
        check_result(result)
        return result


def _ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


def _public_error(error: Exception) -> str:
    if isinstance(error, httpx.TimeoutException):
        return 'Remote request timed out; remote work may continue'
    if isinstance(error, httpx.HTTPStatusError):
        return f'Remote HTTP status {error.response.status_code}'
    if isinstance(error, httpx.RequestError):
        return 'Remote worker is unreachable'
    return str(error)[:400]


def _same_measurements(actual: Any, expected: Any) -> bool:
    """Allow insignificant floating point differences between CPU architectures."""
    if isinstance(expected, bool) or expected is None or isinstance(expected, str):
        return actual == expected and type(actual) is type(expected)
    if isinstance(expected, (float, int)):
        return isinstance(actual, (float, int)) and not isinstance(actual, bool) and math.isfinite(actual) and math.isclose(actual, expected, rel_tol=1e-7, abs_tol=1e-8)
    if isinstance(expected, dict):
        return isinstance(actual, dict) and set(actual) == set(expected) and all(_same_measurements(actual[key], value) for key, value in expected.items())
    if isinstance(expected, (tuple, list)):
        return isinstance(actual, (tuple, list)) and len(actual) == len(expected) and all(_same_measurements(a, e) for a, e in zip(actual, expected))
    return actual == expected


def _check_issues(issues: Any, model: Model, expected: list) -> None:
    if not isinstance(issues, list):
        raise ValueError('Remote issue list is invalid')
    ids = {p.id for p in model.pipes} | {o.id for o in model.obstacles}
    for raw in issues:
        issue = Issue.model_validate(raw)
        if issue.pipe_id not in {p.id for p in model.pipes} or not set(issue.object_ids) <= ids:
            raise ValueError('Remote issue references unknown geometry')
    if not _same_measurements(issues, expected):
        raise ValueError('Remote engineering issues disagree with local geometry verification')


def _checked_proposal(original: Model, raw: dict, selected: list[str], clearance: float, mode: str) -> dict:
    parsed = OptimizationResult.model_validate(raw)
    proposal = parsed.model
    if proposal.model_dump(exclude={'pipes'}) != original.model_dump(exclude={'pipes'}):
        raise ValueError('Remote proposal changed the fixed model or source identity')
    if [p.id for p in proposal.pipes] != [p.id for p in original.pipes]:
        raise ValueError('Remote proposal changed pipe membership')
    if sorted(c.pipe_id for c in parsed.candidates) != sorted(selected):
        raise ValueError('Remote proposal candidate selection does not match the request')
    candidates = {c.pipe_id: c for c in parsed.candidates}
    result = copy.deepcopy(raw)
    all_rows = []
    from .optimizer import objective
    for old, new in zip(original.pipes, proposal.pipes):
        if old.model_dump(exclude={'points'}) != new.model_dump(exclude={'points'}):
            raise ValueError('Remote proposal changed pipe metadata')
        changed = old != new
        if changed and (not old.editable or old.id not in selected or candidates[old.id].status != 'accepted'):
            raise ValueError('Remote proposal changed a fixed or unselected route')
        if old.id not in selected:
            continue
        # All other final routes are present, while this pipe is compared to its
        # true source. This checks multi-route clashes and source preservation.
        baseline = proposal.model_copy(deep=True)
        baseline.pipes = [old.model_copy(deep=True) if p.id == old.id else p for p in baseline.pipes]
        rows = validate(baseline, proposal, old.id, clearance)
        candidate = candidates[old.id]
        if candidate.status == 'accepted' and any(r['status'] == 'FAIL' for r in rows):
            raise ValueError('Remote optimizer failed independent local route validation')
        entry = next(c for c in result['candidates'] if c['pipe_id'] == old.id)
        if not _same_measurements(entry['before'], metrics(old)) or not _same_measurements(entry['after'], metrics(new)):
            raise ValueError('Remote route measurements disagree with local geometry')
        for row in candidate.validation:
            if row.get('status') not in {'PASS', 'FAIL', 'UNVERIFIED', 'NOT_APPLICABLE'} or not isinstance(row.get('requirement'), str):
                raise ValueError('Remote validation row is invalid')
        entry.update(validation=rows, assurance=assurance(rows), before=metrics(old), after=metrics(new),
                     objective_before=objective(old, old, mode, original), objective_after=objective(new, old, mode, original))
        all_rows.extend(rows)
    local_analysis = analyze(proposal, clearance)
    _check_issues(raw['issues'], proposal, local_analysis['issues'])
    result['model'] = proposal.model_dump()
    result['issues'] = local_analysis['issues']
    result['assurance'] = assurance(all_rows)
    result['validation_coverage'] = {status: sum(r['status'] == status for r in all_rows) for status in ('PASS', 'FAIL', 'UNVERIFIED', 'NOT_APPLICABLE')}
    return result


def verify_remote_result(operation: str, original: Model, raw: dict, options: dict) -> dict:
    if operation == 'analyze':
        local = analyze(original, options['clearance'])
        _check_issues(raw.get('issues'), original, local['issues'])
        if not _same_measurements(raw.get('metrics'), local['metrics']):
            raise ValueError('Remote analysis measurements disagree with local geometry')
        return local
    selected = options['pipe_ids'] if options['pipe_ids'] is not None else [p.id for p in original.pipes if p.editable]
    result = _checked_proposal(original, raw, selected, options['clearance'], options['mode'])
    from .advanced import network_metrics, explain
    alternatives = []
    for alternative in raw.get('alternatives', []):
        if alternative.get('alternatives'):
            raise ValueError('Nested remote alternatives are invalid')
        checked = _checked_proposal(original, alternative, selected, options['clearance'], options['mode'])
        proposal = Model.model_validate(checked['model'])
        measured = network_metrics(proposal, original, options['clearance'])
        if 'metrics' in checked and not _same_measurements(checked['metrics'], measured):
            raise ValueError('Remote alternative metrics disagree with local geometry')
        checked['metrics'] = measured
        checked['explanations'] = explain(original, proposal, checked['candidates'], options['clearance'])
        alternatives.append(checked)
    proposal = Model.model_validate(result['model'])
    for key, measured in [('network_before', network_metrics(original, original, options['clearance'])), ('network_after', network_metrics(proposal, original, options['clearance']))]:
        if key in raw and not _same_measurements(raw[key], measured):
            raise ValueError('Remote network metrics disagree with local geometry')
        result[key] = measured
    ids = [item.get('id') for item in alternatives]
    if any(not isinstance(item, str) for item in ids) or len(ids) != len(set(ids)):
        raise ValueError('Remote alternative identifiers are invalid')
    chosen = raw.get('selected_alternative')
    if chosen is not None and (chosen not in ids or next(item['model'] for item in alternatives if item['id'] == chosen) != result['model']):
        raise ValueError('Remote selected alternative does not match the proposed geometry')
    from .optimizer import objective
    def cost(candidate):
        return sum(objective(next(p for p in candidate.pipes if p.id == pipe_id), next(p for p in original.pipes if p.id == pipe_id), options['mode'], original)['value'] for pipe_id in selected)
    result['network_cost_before'] = cost(original)
    result['network_cost_after'] = cost(proposal)
    dimensions = ['length', 'bends', 'modified_components', 'modification_distance_normalized']
    if alternatives and all(item['metrics']['pressure_loss_pa'] is not None for item in alternatives):
        dimensions.append('pressure_loss_pa')
    elif alternatives and all(item['metrics']['known_pressure_loss_pa'] is not None for item in alternatives):
        dimensions.append('known_pressure_loss_pa')
    for item in alternatives:
        if any(candidate['status'] != 'accepted' for candidate in item['candidates']):
            raise ValueError('Remote alternative is not accepted by independent validation')
        item['network_cost'] = cost(Model.model_validate(item['model']))
        item['nondominated'] = not any(all(other['metrics'][key] <= item['metrics'][key] + 1e-9 for key in dimensions) and any(other['metrics'][key] < item['metrics'][key] - 1e-9 for key in dimensions) for other in alternatives if other is not item)
    result['frontier'] = dict(label='Approximate nondominated set among explored candidates', dimensions=dimensions)
    result['alternatives'] = alternatives
    result['explanations'] = explain(original, proposal, result['candidates'], options['clearance'])
    return result


def _bounded_post(client, endpoint: str, payload: bytes, headers: dict, limit: int):
    request = client.build_request('POST', endpoint, content=payload, headers=headers)
    response = client.send(request, stream=True)
    try:
        advertised = response.headers.get('content-length')
        if advertised is not None and int(advertised) > limit:
            raise ValueError(f'Remote response exceeds {limit} bytes')
        chunks = []
        size = 0
        for chunk in response.iter_bytes(chunk_size=65_536):
            checkpoint('remote response transfer')
            size += len(chunk)
            if size > limit:
                raise ValueError(f'Remote response exceeds {limit} bytes')
            chunks.append(chunk)
        return httpx.Response(response.status_code, headers=response.headers,
                              content=b''.join(chunks), request=request)
    finally:
        response.close()


def _execute_remote_or_local(operation: str, model: Model, options: dict, config: dict, cancel=None) -> dict:
    """Run the configured worker or retain the fully functional local engine.

    HTTP timings measure a round trip. They do not invent one-way transfer
    measurements. A timed-out or cancelled request may still run on the worker.
    """
    started = time.perf_counter()
    original = model.model_copy(deep=True)
    validated = validate_options(operation, original, options)
    execution = dict(location='local', processor='CPU', architecture=platform.machine(), client_architecture=platform.machine(),
                     gpu=False, npu=False, endpoint=config.get('endpoint', ''),
                     serialization_ms=0., roundtrip_ms=None, remote_compute_ms=None,
                     decode_ms=0., local_compute_ms=None, local_validation_ms=0.,
                     network_and_overhead_ms=None, request_transfer_ms=None, response_transfer_ms=None, request_bytes=0, response_bytes=0, payload_mode='local',
                     local_fallback_reason=None, remote_cancel_supported=False,
                     transfer_timing_scope='HTTP round trip minus remote compute; includes transfer, server queue and protocol overhead; one-way transfers are not separately measured')
    if config.get('enabled') and not (cancel and cancel.is_set()):
        try:
            endpoint = normalized_endpoint(config.get('endpoint', ''))
            timeout = _timeout(config.get('timeout_s', 15))
            remaining = remaining_seconds()
            if remaining is not None:
                timeout = max(.001, min(timeout, remaining))
            headers = {'Content-Type': 'application/json', **_auth_headers(config.get('access_token'))}
            request_id = uuid.uuid4().hex
            serialized = time.perf_counter()
            digest = model_digest(original)
            source_revision = str(config.get('source_revision') if config.get('source_revision') is not None else digest)
            execution.update(source_revision=source_revision, source_digest=digest)
            fields = dict(request_id=request_id, source_revision=source_revision, operation=operation,
                          resulting_digest=digest, options=validated)
            full = ComputeRequest(model=original, **fields)
            request = full
            execution['payload_mode'] = 'full'
            with _client_lock:
                cached = _client_models.get(endpoint)
            if cached:
                delta = _model_delta(cached[1], original)
                if delta is not None:
                    candidate = ComputeRequest(base_digest=cached[0], delta=delta, **fields)
                    if len(candidate.model_dump_json(exclude_none=True)) < len(full.model_dump_json(exclude_none=True)):
                        request = candidate
                        execution['payload_mode'] = 'delta'
            payload = request.model_dump_json(exclude_none=True).encode('utf-8')
            execution.update(serialization_ms=_ms(serialized), request_bytes=len(payload), roundtrip_ms=0.)
            with httpx.Client(timeout=timeout, trust_env=False, follow_redirects=False) as client:
                for attempt in range(2):
                    roundtrip = time.perf_counter()
                    try:
                        response = _bounded_post(client, endpoint + '/api/edge/compute', payload, headers, current_limits().max_response_bytes)
                    finally:
                        execution['roundtrip_ms'] += _ms(roundtrip)
                    execution['response_bytes'] += len(response.content)
                    if response.status_code == 409 and request.delta is not None and attempt == 0:
                        serializing_retry = time.perf_counter()
                        request = full
                        payload = full.model_dump_json(exclude_none=True).encode('utf-8')
                        execution['serialization_ms'] += _ms(serializing_retry)
                        execution['request_bytes'] += len(payload)
                        execution['payload_mode'] = 'delta_then_full'
                        continue
                    response.raise_for_status()
                    break
            checkpoint('remote response received')
            if len(response.content) > current_limits().max_response_bytes:
                raise WorkLimitExceeded(f'Remote response exceeds {current_limits().max_response_bytes} bytes')
            decoded = time.perf_counter()
            envelope = ComputeResponse.model_validate_json(response.content)
            execution['decode_ms'] = _ms(decoded)
            execution['remote_compute_ms'] = envelope.compute_ms
            execution['network_and_overhead_ms'] = max(0., execution['roundtrip_ms'] - envelope.compute_ms)
            if envelope.request_id != request_id or envelope.source_revision != request.source_revision or envelope.source_digest != digest:
                raise ValueError('Stale or mismatched remote request identity or source revision')
            if cancel and cancel.is_set():
                raise ValueError('Designer cancelled while remote work was running; remote work may continue')
            verifying = time.perf_counter()
            try:
                check_result(envelope.result)
                result = verify_remote_result(operation, original, envelope.result, validated)
                check_result(result)
            finally:
                execution['local_validation_ms'] = _ms(verifying)
            with _client_lock:
                _client_models[endpoint] = (digest, original)
                _client_models.move_to_end(endpoint)
                while len(_client_models) > 8:
                    _client_models.popitem(last=False)
            execution.update(location='remote', endpoint=endpoint, architecture=envelope.architecture, total_ms=_ms(started))
            return dict(result=result, execution=execution)
        except (WorkLimitExceeded, WorkCancelled):
            raise
        except (httpx.HTTPError, ValueError, TypeError, KeyError, StopIteration) as error:
            execution['local_fallback_reason'] = _public_error(error)
    elif not config.get('enabled'):
        execution['local_fallback_reason'] = 'Remote processing disabled'
    else:
        execution['local_fallback_reason'] = 'Designer cancellation requested before remote dispatch'
    computing = time.perf_counter()
    result = compute_local(operation, original, validated, cancel)
    execution.update(local_compute_ms=_ms(computing), total_ms=_ms(started))
    return dict(result=result, execution=execution)


def execute_remote_or_local(operation: str, model: Model, options: dict, config: dict, cancel=None) -> dict:
    """Keep a single request budget across transport, verification and fallback."""
    started = time.perf_counter()
    validated = validate_options(operation, model, options)
    if cancel and cancel.is_set():
        return dict(result=dict(status='cancelled', analysis_performed=False, model=model.model_dump(),
                                candidates=[], issues=[], assurance=dict(implemented_constraints_valid=False,
                                geometrically_valid=False, requires_engineering_review=True,
                                scope='Cancelled before computation; geometry was not evaluated')),
                    execution=dict(location='local', processor='CPU', architecture=platform.machine(),
                                   client_architecture=platform.machine(), gpu=False, npu=False, cancelled=True,
                                   endpoint=config.get('endpoint', ''), serialization_ms=0., roundtrip_ms=None,
                                   remote_compute_ms=None, decode_ms=0., local_compute_ms=0., local_validation_ms=0.,
                                   network_and_overhead_ms=None, request_transfer_ms=None, response_transfer_ms=None,
                                   request_bytes=0, response_bytes=0, payload_mode='none',
                                   local_fallback_reason='Designer cancellation requested before computation',
                                   remote_cancel_supported=False, total_ms=_ms(started)))
    with work_scope(validated.get('limits'), cancel=cancel):
        preflight_model(model, operation)
        return _execute_remote_or_local(operation, model, validated, config, cancel)
