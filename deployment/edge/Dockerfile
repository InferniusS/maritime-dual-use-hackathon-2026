# Build from repository root: docker build -f deployment/edge/Dockerfile -t pipeforge-edge .
# The upstream Python image supports ARM64; pinned scientific wheels must be verified on the target.
FROM python:3.12-slim
WORKDIR /app
COPY requirements-edge.txt ./
RUN python -m pip install --no-cache-dir -r requirements-edge.txt
COPY backend/__init__.py backend/models.py backend/geometry.py backend/hydraulics.py backend/catalog.py backend/evidence.py backend/spatial.py backend/gravity.py backend/validation.py backend/optimizer.py backend/advanced.py backend/edge_remote.py backend/edge_worker.py backend/work_limits.py ./backend/
EXPOSE 8010
CMD ["python", "-m", "uvicorn", "backend.edge_worker:app", "--host", "127.0.0.1", "--port", "8010", "--workers", "1", "--no-proxy-headers"]
