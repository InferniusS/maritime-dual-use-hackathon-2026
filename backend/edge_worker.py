"""Standalone CPU engineering worker with bounded work and restricted access."""
from collections import OrderedDict
import hmac
import ipaddress
import json
import logging
import os
import threading
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from .edge_remote import ComputeRequest, PROTOCOL, apply_delta, compute_local, health_payload, model_digest, validate_options
from .work_limits import WorkLimits, WorkLimitExceeded, WorkCancelled, work_scope, check_result, preflight_model

app = FastAPI(title='PIPEFORGE EDGE CPU worker', version='1.1.0')
_capacity = threading.BoundedSemaphore(2)
_models = OrderedDict()
_model_lock = threading.Lock()
_logger = logging.getLogger('uvicorn.error')


def _allowed(request: Request) -> bool:
    """Only actual peer addresses are consulted; forwarded headers are ignored."""
    peer = request.client.host if request.client else ''
    try:
        address = ipaddress.ip_address(peer)
    except ValueError:
        return False
    token = os.environ.get('PIPEFORGE_EDGE_TOKEN', '')
    allowlist = os.environ.get('PIPEFORGE_EDGE_ALLOWLIST', '')
    networks = [ipaddress.ip_network(item.strip(), strict=False) for item in allowlist.split(',') if item.strip()]
    if any(address in network for network in networks):
        return True
    supplied = request.headers.get('authorization', '')
    if token:
        if any(ord(character) < 33 or ord(character) > 126 for character in token):
            return False
        return hmac.compare_digest(supplied.encode('utf-8'), ('Bearer ' + token).encode('utf-8'))
    return address.is_loopback


@app.middleware('http')
async def restricted_access(request: Request, call_next):
    try:
        allowed = _allowed(request)
    except ValueError:
        return JSONResponse({'detail': 'Worker access configuration is invalid'}, status_code=503)
    if not allowed:
        # No headers, credentials or payload are included in access errors.
        return JSONResponse({'detail': 'Worker access denied: configure an access token or permitted client network'}, status_code=403)
    return await call_next(request)


def resolve_model(body):
    if body.model is not None:
        model = body.model
    else:
        with _model_lock:
            base = _models.get(body.base_digest)
        if base is None:
            raise HTTPException(409, 'base_model_missing: resend the full source model')
        model = apply_delta(base, body.delta)
    if model_digest(model) != body.resulting_digest:
        raise ValueError('Source model digest does not match reconstructed geometry')
    preflight_model(model, body.operation)
    with _model_lock:
        _models[body.resulting_digest] = model.model_copy(deep=True)
        _models.move_to_end(body.resulting_digest)
        while len(_models) > 8:
            _models.popitem(last=False)
    return model


@app.get('/api/edge/health')
def health():
    return health_payload()


@app.post('/api/edge/compute')
def compute(body: ComputeRequest):
    if not _capacity.acquire(blocking=False):
        raise HTTPException(429, 'Two engineering computations are already active')
    started = time.perf_counter()
    try:
        # The operator may lower or raise server defaults within the hard schema
        # bounds. A client can only narrow the active server budget.
        server_limits = WorkLimits.model_validate(json.loads(os.environ.get('PIPEFORGE_EDGE_LIMITS', '{}')))
        with work_scope(server_limits):
            with work_scope(body.options.get('limits')):
                model = resolve_model(body)
                options = validate_options(body.operation, model, body.options)
                result = compute_local(body.operation, model, options)
                envelope = dict(protocol=PROTOCOL, request_id=body.request_id, source_revision=body.source_revision,
                                result=result, source_digest=body.resulting_digest, architecture=health_payload()['architecture'],
                                compute_ms=(time.perf_counter() - started) * 1000)
                check_result(envelope)
        _logger.info('edge_compute %s', json.dumps(dict(request_id=body.request_id, operation=body.operation,
                     source_revision=body.source_revision, compute_ms=envelope['compute_ms']), separators=(',', ':')))
        return envelope
    except WorkCancelled as error:
        raise HTTPException(409, dict(code=error.code, message=str(error))) from error
    except WorkLimitExceeded as error:
        raise HTTPException(422, dict(code=error.code, message=str(error), engineering_status='UNRESOLVED')) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    finally:
        _capacity.release()
