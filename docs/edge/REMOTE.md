# CPU processing locally and on an optional edge host

PIPEFORGE runs without a second device. The companion Python service executes engineering analysis and deterministic routing on CPU. Its optional HTTP worker imports the same engineering engine and has no dependency on the viewer, CAD kernel, AutoCAD, GPU, NPU, neural model, cloud API or secret. The worker reports the architecture and Python version it actually runs on.

An independent Linux x86_64 worker has been exercised over real loopback HTTP. No IQ-9075 address, OS image, Python runtime, package inventory or credentials were supplied. CPython 3.12 ARM64 binary wheel availability has been verified without executing the artifacts. ARM64 installation and runtime, Windows PowerShell execution, Docker image builds, LAN performance and Qualcomm hardware operation remain unverified. The presence of hardware does not establish software compatibility. NPU and GPU execution are not implemented.

## Start the Windows companion

Use Python 3.12 and Node.js 20 or 22 with npm. From the checkout root in PowerShell:

```powershell
.\scripts\setup-local.ps1
.\scripts\start-local.ps1
```

Open `http://127.0.0.1:8000` on the Windows laptop. The setup script installs the existing pinned Python environment into the ignored `.venv` directory and builds the existing viewer using `npm ci`. It does not modify dependency declarations or lockfiles. For API-only use, pass `-SkipViewer`; the browser workspace requires a built `frontend/dist`. Startup is portable and binds the Windows service to loopback. The optional CAD solid-export environment is a separate existing feature and is not required by the edge worker.

## ARM64 artifact verification

The non-executing `pip download --only-binary=:all:` check succeeded for CPython 3.12 / `cp312`, allowing `manylinux_2_28_aarch64`, `manylinux_2_27_aarch64` and `manylinux2014_aarch64` tags. It downloaded 19 wheels into `/tmp/pipeforge-edge-arm64-wheels` without installing or importing them. [Wheel observations](../../deployment/edge/arm64-wheel-observations.json) record filenames, package metadata, sizes and SHA-256 digests. The binaries are not committed.

| Compiled dependency | Downloaded ARM64 wheel tags | Minimum glibc indicated by tags |
| --- | --- | --- |
| NumPy 2.5.4 | `cp312-cp312-manylinux_2_27_aarch64` / `manylinux_2_28_aarch64` | 2.27 |
| SciPy 1.18.1 | `cp312-cp312-manylinux_2_27_aarch64` / `manylinux_2_28_aarch64` | 2.27 |
| Pydantic-core 2.50.1 | `cp312-cp312-manylinux_2_17_aarch64` / `manylinux2014_aarch64` | 2.17 |

The complete downloaded stack requires CPython 3.12 with the `cp312` ABI, 64-bit AArch64 Linux and glibc 2.27 or newer by its wheel tags. NumPy and SciPy metadata both require Python 3.12 or newer; this check specifically verifies Python 3.12 artifacts. A separate manylinux2014-only probe failed to resolve NumPy 2.5.4, so glibc 2.17 support must not be assumed for the pinned stack. These wheels do not establish compatibility with a musl-based Linux image.

Reproduce the artifact check without running ARM code:

```bash
python -m pip download --only-binary=:all: \
  --platform manylinux_2_28_aarch64 --platform manylinux_2_27_aarch64 \
  --platform manylinux2014_aarch64 --implementation cp \
  --python-version 312 --abi cp312 -r requirements-edge.txt \
  --dest /tmp/pipeforge-edge-arm64-wheels
```

On the device, inspect `uname -m`, `getconf GNU_LIBC_VERSION` and `python3 --version` before installation. Successful wheel resolution establishes artifact availability; the device OS, loader, CPU execution, imports, numerical behavior and end-to-end service still need a real hardware smoke test. No IQ-9075 execution has been observed.

## Start a CPU worker on Linux

Clone the same revision on the Linux host. Use a compatible 64-bit Python 3.12 installation. Check the machine before installation:

```bash
uname -m
python3 --version
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip download --only-binary=:all: -r requirements-edge.txt --dest /tmp/pipeforge-edge-wheels
.venv/bin/python -m pip install -r requirements-edge.txt
./scripts/start-edge.sh
```

The wheel download is an explicit compatibility check for that host, including transitive packages. If compatible wheels are unavailable, stop and resolve the supported Python, OS and package combination. Do not disable TLS verification or substitute package hashes to bypass installation failures. Requirements use the same tested engine versions as the main environment. Compatible binary artifacts were successfully resolved for CPython 3.12 ARM64 Linux, including all 19 direct and transitive wheels; actual installation and runtime on the IQ-9075 remain unverified.

The worker defaults to `127.0.0.1:8010`. Requests from outside loopback are denied unless an access token matches or the actual client address belongs to an explicitly configured allowlist. Override `PIPEFORGE_EDGE_HOST`, `PIPEFORGE_EDGE_PORT`, or `PIPEFORGE_PYTHON` when needed. It starts one Uvicorn process with at most two active computations. Extra requests receive HTTP 429 and the companion falls back locally. This service is a CPU process; device-specific Qualcomm SDKs are unnecessary for this path.

An optional container definition is provided at `deployment/edge/Dockerfile`:

```bash
docker build -f deployment/edge/Dockerfile -t pipeforge-edge .
# Provision PIPEFORGE_EDGE_TOKEN securely in the shell environment first.
docker run --rm -p 127.0.0.1:8010:8010 --env PIPEFORGE_EDGE_TOKEN pipeforge-edge \
  python -m uvicorn backend.edge_worker:app --host 0.0.0.0 --port 8010 --no-proxy-headers
```

The upstream Python image is available for multiple architectures; this Dockerfile and its pinned packages still require a build and runtime check on the target. The container copies only the engineering engine and transport modules. Its default command binds container loopback. The explicit command above binds its container interface while publishing only host loopback, and requires a securely provisioned token. To share it on a LAN, deliberately change the published host binding and retain token authentication or configure the actual permitted client network.

## Connect the companion to the worker

For shared LAN operation, configure `PIPEFORGE_EDGE_HOST=0.0.0.0` on the worker and either securely provision `PIPEFORGE_EDGE_TOKEN`, or set `PIPEFORGE_EDGE_ALLOWLIST` to the Windows laptop's actual address/CIDR. For example, `192.168.1.50/32` permits only that illustrative client address; replace it with the real one. Invalid allowlist syntax fails closed. The shell launcher disables proxy-header trust, and the application checks the actual connection peer rather than user-supplied forwarding headers.

Enter the worker's actual local address, such as `http://192.168.1.50:8010`, in the companion's remote endpoint setting, enable remote processing, and run the connection check. Use the address assigned to the device; the example is illustrative. HTTPS is supported when the worker is served behind a trusted TLS endpoint. The companion performs the requests, so browser CORS configuration is unnecessary.

Provide the matching credential through the companion's password input (`access_token`) or securely inject `PIPEFORGE_REMOTE_TOKEN` into the local companion process. The transport sends it only as an HTTP Authorization header. Tokens are absent from saved engineering results, geometry payloads, execution metadata and worker computation logs. Worker health checks use the same access policy; a missing or incorrect credential reports unavailable and computation falls back locally. HTTP is intended for a trusted network; HTTPS retains normal certificate validation.

The backend transport configuration is:

```json
{"enabled":true,"endpoint":"http://192.168.1.50:8010","timeout_s":15,"source_revision":"drawing-revision"}
```

For a standalone health check:

```bash
# Without a token, this works only from loopback or an explicitly permitted client.
curl http://192.168.1.50:8010/api/edge/health
```

Healthy output identifies protocol `pipeforge-edge/1`, status `ok`, the real architecture, `processor: CPU`, `gpu: false` and `npu: false`. Health checks do not perform engineering work. Disabling remote processing keeps all computation local. Unreachable, timed-out, overloaded, malformed or stale responses trigger local fallback with a visible reason. Local analysis honors the configured clearance.

## Geometry, revisions and validation

`POST /api/edge/compute` accepts a request ID, drawing revision, operation (`analyze` or `optimize`), typed model, and an operation-specific options whitelist. Unknown options, invalid dimensions, unknown selected IDs and duplicate selections are rejected. Optimization accepts the existing `mode`, `resolution`, `clearance`, `pipe_ids`, `alternatives`, `multiresolution` and `try_orders` options. Analysis accepts `clearance`. Both operations accept optional bounded `limits` described below.

The first request sends the full model. Both ends hash its canonical JSON. Each side retains at most eight source snapshots. For subsequent changes, the companion sends changed pipes and obstacles plus removals when this payload is smaller; unchanged surroundings remain in the worker's complete reconstructed model. Global metadata or ordering changes use a full snapshot. The worker checks the base and resulting digests and validates the reconstructed model. A missing base after a worker restart triggers exactly one full-snapshot retry. Request and response byte counts include this retry.

Every result echoes request identity, source revision and source digest. A mismatch is rejected. The companion must additionally check that its current session revision still matches before displaying or approving work; this transport check establishes which request was computed.

Remote routing proposals pass local Pydantic model validation and independent geometry validation before use. Source endpoints, pipe dimensions and metadata, edit permissions, selection, source identity, structure and coordinate frame must be preserved. With several changed systems, each source pipe is checked in the final environment containing the other proposed routes. Unchanged routes cannot be moved. Returned issues and measurements are compared with local geometry, and final validation evidence, summaries, costs and metrics are computed locally. Analysis results are also independently recalculated locally. This duplication consumes CPU and is included in measured latency; it provides a conservative acceptance boundary for this prototype.

No remote cancellation endpoint is implemented. A client timeout stops waiting and initiates local fallback within the remaining request budget; already dispatched work may finish on the worker. A pre-cancelled request performs no numerical computation or network request and returns a cancellation envelope with `analysis_performed: false` and invalid assurance flags. In-flight local work checks cancellation and deadlines cooperatively in geometry/search loops. Remote work has its own enforced deadline; cancellation of a companion job does not imply that the remote process was interrupted.

## Timings and observed performance

Each result records serialization time, HTTP round trip, worker computation, JSON decoding, local verification, local fallback computation when applicable, total latency, transmitted bytes and full/delta mode. `total_ms` includes client setup and all verification, so individual reported components are not expected to sum exactly to the total.

`network_and_overhead_ms = max(roundtrip_ms - remote_compute_ms, 0)` combines request transfer, response transfer, worker queuing, and HTTP overhead. It is not a measured one-way network transfer. `request_transfer_ms` and `response_transfer_ms` remain null because this transport has no synchronized one-way tracing. The worker computation timer includes source-cache resolution and digest verification as well as engineering execution. Measurements use monotonic clocks and avoid comparing wall clocks across machines.

Run a local-versus-worker comparison from the checkout root:

```bash
.venv/bin/python deployment/edge/benchmark.py --endpoint http://DEVICE_ADDRESS:8010 --runs 3 --output /tmp/pipeforge-device-benchmark.json
```

On Windows, substitute `.venv\Scripts\python.exe`. Use actual device measurements to select a processing location. The committed historical [raw observations](../../deployment/edge/benchmark-observations.json) record three runs per case against an independent worker on the same Linux x86_64 machine, over loopback. Median milliseconds:

| Case | Local total | HTTP total with local checks | Worker compute | Local verification |
| --- | ---: | ---: | ---: | ---: |
| Inefficient route analysis | 4.14 | 17.95 | 2.85 | 2.72 |
| Inefficient route optimization | 40.35 | 102.01 | 38.33 | 52.33 |
| Congested route optimization, 1 m grid | 120.80 | 176.84 | 107.78 | 53.70 |

Remote execution was slower in these observations. The result includes independent local verification and is not a benchmark of a Windows laptop against a Qualcomm device. Repeated requests used model deltas; initial requests and all per-run timings and byte counts are retained in the artifact.

## Incremental session and localized-edit measurements

The separate historical [session benchmark](../../deployment/edge/session-benchmark.py) compares the local session cache against fresh full analysis, then transmits an initial full model and one localized update through an independently launched HTTP worker. It uses 40 pressure routes with 120 straight segments, no obstacles, 6 m separation and 0.1 m configured clearance. Each source route has a genuine calculated inefficiency finding. One edit offsets a middle straight run by 0.5 m in Y while preserving both endpoints. Their disjoint dependency envelopes intentionally allow reuse of the other 39 routes.

Reproduce it with the main development environment, which includes the session persistence and exchange dependencies:

```bash
.venv/bin/python deployment/edge/session-benchmark.py --pipes 40 --runs 3 \
  --output /tmp/pipeforge-session-benchmark.json
```

Omit `--endpoint` to launch and stop a fresh independent loopback worker automatically, or supply the actual worker address. The [recorded observations](../../deployment/edge/session-benchmark-observations.json) contain all samples, worker health, environment and assertion results. The measured client and worker both used Linux x86_64 and Python 3.12.14. Three-run median milliseconds:

| Local analysis path | Time | Reused / recomputed routes |
| --- | ---: | ---: |
| Fresh full analysis | 1305.16 | 0 / 40 |
| Cold session cache | 1290.82 | 0 / 40 |
| Warm session cache | 0.45 | 40 / 0 |
| Full analysis after the localized edit | 1250.54 | 0 / 40 |
| Incremental analysis after the same edit | 30.63 | 39 / 1 |

The SQLite geometry-patch operation took a separate median 7.44 ms. Analysis timings include model reconstruction and analysis assembly, while SQLite capture and analysis publication are outside those timers. The assertions compare engineering issues and metrics after JSON normalization, accounting for equivalent tuple/list coordinate representations across persistence and HTTP.

The actual HTTP initial request transmitted **20,274 bytes**; the one-edit delta transmitted **866 bytes**. Both responses transmitted **30,097 bytes**, because results still include all route metrics and findings. Full-request and delta-request total latencies were **2626.30 ms** and **2552.97 ms**, including local verification of **1256.24 ms** and **1236.22 ms**, respectively. The worker recomputes full analysis after reconstructing either request. Remote analysis therefore duplicates laptop computation even when its outbound geometry payload is small.

All cold, warm and edited session outputs matched their fresh full-analysis counterparts. Both actual HTTP results matched local analysis, and the localized edit recomputed exactly one route. These observations establish correctness and reuse for this disjoint fixture; densely interacting routes, changed obstacles or global settings may invalidate many or all cached routes. They establish neither Windows laptop performance nor an IQ-9075 speedup.

## Bounded computation and output

Each request can supply a `limits` object. The same ContextVar scope follows local computation, HTTP waiting, independent verification and fallback, so nesting cannot reset its deadline or enlarge its budgets. Separate concurrent jobs do not share mutable budget configuration. The worker also applies operator limits from `PIPEFORGE_EDGE_LIMITS` (a JSON object); a client may narrow those server limits, and all values remain inside hard schema bounds.

| Limit | Default | Largest accepted setting |
| --- | ---: | ---: |
| `deadline_s` | 30 s | 120 s |
| `max_axis_points` | 20,000 | 100,000 |
| `max_grid_cells` | 5,000,000 | 20,000,000 |
| `max_expanded` | 60,000 | 200,000 |
| `max_pair_tests` | 5,000,000 | 50,000,000 |
| `max_issues` | 5,000 | 50,000 |
| `max_result_items` | 100,000 | 500,000 |
| `max_response_bytes` | 8,000,000 | 16,000,000 |

For example, `options: {"clearance": 0.1, "limits": {"deadline_s": 15, "max_response_bytes": 2000000}}` narrows an analysis request. Grid preflight checks per-axis allocations and combined cells before NumPy arrays are constructed. Model preflight estimates cross-pipe, obstacle, self-intersection and fitting work before geometry loops. These estimates are work bounds rather than claimed counts of executed comparisons. Findings are capped before append, and nested result item counts, depth and serialized JSON bytes are checked before returning a response. The client streams responses and stops reading when its byte limit is exceeded.

Exceeded work/output budgets return an explicit unresolved error, rather than truncated findings or a passing compliance result. Cancellation checks run before allocation and at geometry/search checkpoints. Deadlines are cooperative CPU limits, not operating-system process termination; any single uninterruptible library operation can finish before the next checkpoint. Existing finite per-route search limits remain in addition to the overall budget. The earlier benchmark artifacts predate these limits and subsequent engineering changes and are retained as historical evidence; rerun the scripts for current-version timing or device decisions.

## Remaining remote-processing limits

Manual local-network addressing supplies device configuration; automatic device discovery is not implemented. Geometry transport is incremental, while remote engineering analysis is currently full-model computation. The companion's local analysis path provides selective reuse, and remote routing offloads graph search while retaining independent laptop validation. Timings distinguish those costs.

One-way request/return transfer measurements, remote cancellation, on-device ARM64 execution and laptop-versus-device benchmarks remain unavailable. GPU and NPU computation and optional neural inference are not implemented. None of these is a prerequisite for the tested local CPU, local session cache, remote CPU worker or fallback paths.
