"""Connected-network routes, independent of the preserved Edge 3.x sessions."""
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pipe_router.network_service import MAX_NETWORK_REQUEST_BYTES, NetworkRequestError, NetworkWorkspace, ROOT


def create_router(workspace=None):
    workspace = workspace or NetworkWorkspace(Path(os.environ.get("PIPEFORGE_NETWORK_OUTPUT", ROOT / "data/generated/network-workspace")))
    def check_host(request: Request):
        authority = request.headers.get('host', '')
        try:
            parsed = urlsplit('//' + authority)
            host = parsed.hostname
            port = parsed.port
        except ValueError:
            raise HTTPException(403, 'Use the configured local workspace host') from None
        allowed = {'localhost', '127.0.0.1', '::1'} | {value.strip().lower() for value in os.environ.get('PIPEFORGE_NETWORK_HOSTS', '').split(',') if value.strip()}
        if (not host or host.lower() not in allowed or port == 0 or parsed.username is not None or parsed.password is not None
                or parsed.path or parsed.query or parsed.fragment or authority.endswith(':') or any(ord(c) <= 32 or ord(c) == 127 for c in authority)):
            raise HTTPException(403, 'Use the configured local workspace host')
    router = APIRouter(tags=["Connected piping networks"], dependencies=[Depends(check_host)])

    @router.get('/connected')
    @router.get('/connected/')
    def companion():
        return FileResponse(ROOT / 'web/connected.html')

    @router.get('/connected/{asset}')
    def companion_asset(asset: str):
        if asset == 'plotly.min.js':
            from plotly.offline import get_plotlyjs
            return Response(get_plotlyjs(), media_type='text/javascript')
        if asset not in ('connected.js', 'connected.css'):
            raise HTTPException(404, 'Unknown companion asset')
        return FileResponse(ROOT / 'web' / asset)

    def call(function):
        try:
            return function()
        except NetworkRequestError as error:
            raise HTTPException(error.status, str(error)) from error
        except (ValueError, TypeError, KeyError) as error:
            raise HTTPException(422, str(error)) from error

    @router.get('/api/network/examples')
    def examples():
        return {'examples': workspace.examples()}

    @router.get('/api/network/examples/{identifier}')
    def example(identifier: str):
        return call(lambda: workspace.example(identifier))

    @router.get('/api/network/jobs/{identifier}')
    def job(identifier: str):
        return call(lambda: workspace.job(identifier))

    @router.get('/api/network/downloads/{identifier}/{filename}')
    def download(identifier: str, filename: str):
        target = call(lambda: workspace.download(identifier, filename))
        return FileResponse(target, filename=target.name, headers={'Cache-Control': 'no-store'})

    @router.post('/api/network/{action:path}')
    async def action(action: str, request: Request):
        origin = request.headers.get('origin')
        # Same-origin browsers only. Native local clients do not send Origin.
        if origin and origin != f'{request.url.scheme}://{request.headers.get("host", "")}':
            raise HTTPException(403, 'Use this local workspace origin')
        if request.headers.get('content-type', '').split(';')[0] != 'application/json':
            raise HTTPException(415, 'Send JSON input')
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > MAX_NETWORK_REQUEST_BYTES:
                raise HTTPException(413, 'Network input exceeds the 2 MB request limit')
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeError, RecursionError) as error:
            raise HTTPException(422, 'Malformed JSON input') from error
        if not isinstance(payload, dict):
            raise HTTPException(422, 'Input must be an object')
        if action == 'inspect' and 'cad_id' in payload:
            identifier = payload['cad_id']
            if not isinstance(identifier, str) or len(identifier) != 32 or any(c not in '0123456789abcdef' for c in identifier):
                raise HTTPException(422, 'Choose a locally imported CAD snapshot')
            snapshot_root = Path(os.environ.get('PIPEFORGE_TEAM_WORKSPACE', ROOT / 'data/generated/workspace'))
            snapshot = snapshot_root / identifier / 'cad-inspection.json'
            if not snapshot.is_file():
                raise HTTPException(404, 'Snapshot not found. Import through the team CAD workspace or open canonical network JSON.')
            if snapshot.stat().st_size > 64 * 1024 * 1024:
                raise HTTPException(413, 'CAD inspection exceeds the local snapshot budget')
            payload['cad'] = json.loads(snapshot.read_text(encoding='utf-8'))
        # Synchronous numerical work runs outside the ASGI event loop.
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(call, lambda: workspace.dispatch(action, payload))

    return router


router = create_router()
