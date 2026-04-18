# syntax=docker/dockerfile:1.7
#
# Greenroom backend image — Flask API + Celery tasks.
#
# Two build targets:
#   dev   — runs Flask's dev server, source is volume-mounted from host
#   prod  — runs gunicorn with the app factory
#
# Build context MUST be the repo root (`.`). The compose file and any
# CI job should set `context: .` + `dockerfile: backend/Dockerfile`.

# ---- base ----------------------------------------------------------------
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      gcc \
      libpq-dev \
      curl \
 && rm -rf /var/lib/apt/lists/*

# Install Python deps first so source edits don't bust the layer cache.
# The editable install needs at least an empty package on disk to succeed;
# the real source is copied in the next step.
COPY backend/pyproject.toml /app/backend/pyproject.toml
RUN mkdir -p /app/backend \
 && touch /app/backend/__init__.py \
 && pip install -e /app/backend

# Now copy the full source tree.
COPY backend /app/backend

EXPOSE 5001

# ---- dev -----------------------------------------------------------------
# Source is mounted from the host at /app/backend so edits reload live.
FROM base AS dev

CMD ["flask", "--app", "backend.app:create_app", "run", \
     "--host", "0.0.0.0", "--port", "5001", "--debug"]

# ---- prod ----------------------------------------------------------------
# Shell form so ${PORT} expands — Railway injects PORT per web service and
# the fallback keeps local `docker run` working without env plumbing.
FROM base AS prod

CMD gunicorn --bind 0.0.0.0:${PORT:-5001} --workers 2 --access-logfile - --error-logfile - "backend.app:create_app()"
