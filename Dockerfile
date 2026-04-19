# ---------- Stage 1: build the Vue frontend ----------
FROM node:22-slim AS frontend

WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY frontend/ .
RUN npm run build

# ---------- Stage 2: backend + runtime ----------
FROM python:3.14-slim AS backend

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        g++ gcc make libffi-dev libxml2-dev libxslt-dev zlib1g-dev ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# uv for Python dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY backend/ /app/

# Copy built frontend into the image so the backend can serve it via a
# reverse proxy (or you can mount /app/static into nginx separately).
COPY --from=frontend /frontend/dist /app/static

# Install Python deps into a project-local virtualenv.
RUN uv sync --frozen --no-dev || uv sync --no-dev

EXPOSE 5000
# Default command: the auto downloader. Override with
#   docker run ... uv run uvicorn app.main:app --host 0.0.0.0 --port 5000
# to launch the Web dashboard API instead.
ENTRYPOINT ["uv", "run", "anigamerplus"]
