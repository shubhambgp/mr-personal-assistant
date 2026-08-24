FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first, so a code change does not invalidate the wheel layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY etl ./etl
COPY evals ./evals
COPY data ./data

# Non-root. The app writes only its logs and the local-mode Qdrant store; both
# directories are created here so a named volume mounted over them inherits
# this ownership instead of arriving root-owned and unwritable by uid 10001.
RUN useradd --create-home --uid 10001 qorvexa \
    && mkdir -p /app/logs /app/qdrant_data \
    && chown -R qorvexa:qorvexa /app/logs /app/qdrant_data
USER qorvexa

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health').status==200 else 1)"

# --proxy-headers makes uvicorn trust X-Forwarded-For from the reverse proxy, so
# request.client.host is the real client rather than the nginx container — which
# the login rate limiter keys on. forwarded-allow-ips is "*" because in this
# stack only nginx can reach the backend on the compose network; if the backend
# is ever published directly, narrow this to the proxy's address.
# Single worker is REQUIRED: the in-process rate limiter, metrics, triage cache
# and local-mode Qdrant lock are all per-process. Do not add --workers.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
