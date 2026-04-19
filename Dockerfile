# ---------- Stage 1: frontend build (intermediate) ----------
FROM node:22-slim AS frontend-build
RUN corepack enable && corepack prepare pnpm@latest --activate
WORKDIR /frontend
COPY frontend/package.json frontend/pnpm-lock.yaml* ./
RUN pnpm install --frozen-lockfile || pnpm install
COPY frontend/ .
RUN pnpm run build

# ---------- Stage 2: nginx serving frontend ----------
FROM nginx:alpine AS frontend
COPY --from=frontend-build /frontend/dist /usr/share/nginx/html
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80

# ---------- Stage 3: Python backend ----------
FROM python:3.14-slim AS backend
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY backend/ /app/
COPY docker/docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh
RUN uv sync --frozen --no-dev || uv sync --no-dev
EXPOSE 5000 5001
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["api"]
