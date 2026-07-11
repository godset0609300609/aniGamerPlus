# ---------- Stage 1: frontend build (intermediate) ----------
# node:22-slim digest resolved 2026-07-10
FROM node:22-slim@sha256:53ada149d435c38b14476cb57e4a7da73c15595aba79bd6971b547ceb6d018bf AS frontend-build
RUN corepack enable && corepack prepare pnpm@latest --activate
WORKDIR /frontend
COPY frontend/package.json frontend/pnpm-lock.yaml* ./
RUN pnpm install --frozen-lockfile || pnpm install
COPY frontend/ .
RUN pnpm run build

# ---------- Stage 2: nginx serving frontend ----------
# nginx:alpine digest resolved 2026-07-10
FROM nginx:alpine@sha256:54f2a904c251d5a34adf545a72d32515a15e08418dae0266e23be2e18c66fefa AS frontend
COPY --from=frontend-build /frontend/dist /usr/share/nginx/html
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80

# ---------- Stage 3: Python backend ----------
# python:3.14-slim digest resolved 2026-07-10 (Debian 13 "trixie")
FROM python:3.14-slim@sha256:b877e50bd90de10af8d82c57a022fc2e0dc731c5320d762a27986facfc3355c1 AS backend
# ffmpeg version pinned to the current trixie release; bump alongside base
# image digest updates (check `apt-cache madison ffmpeg` in-container).
# 7.1.5-0+deb13u1 is the Debian security-team point release that closes
# DSA-6361-1 (CVE-2025-22921 and friends — heap overflows in the ffmpeg
# demuxers/decoders reachable from an untrusted input file). If a rebuild
# ever finds 7.1.5 unavailable at build time (mirror lag), fall back to the
# latest available 7.1.x patch rather than re-pinning to 7.1.3.
# gcc + libc6-dev are needed because tgcrypto (hydrogram's native crypto
# backend, same package pyrogram used before the hydrogram migration — see
# backend/pyproject.toml) has no prebuilt wheel for Python 3.14 yet and must
# compile its C extension from sdist during `uv sync` below. hydrogram
# itself ships a universal py3-none-any wheel and needs no compiler at all;
# tgcrypto is the sole reason this toolchain is still here. It's a small,
# dependency-free extension (Python.h only), so this is the whole build
# toolchain required. These stay in the final image (single-stage backend
# build); ~20MB overhead, acceptable for now — revisit if/when the backend
# gets a multi-stage build split.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg=7:7.1.5-0+deb13u1 gcc libc6-dev && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY backend/ /app/
COPY docker/docker-entrypoint.sh /app/docker-entrypoint.sh
COPY docker/healthcheck.py /app/healthcheck.py
RUN chmod +x /app/docker-entrypoint.sh
RUN uv sync --frozen --no-dev || uv sync --no-dev

# Run as a non-root user. UID is a build arg (default 1000) so operators can
# match their host uid for the bind-mounted config/logs/bangumi volumes —
# see docker-compose.yml volumes and .env.example for the matching
# `chown -R <uid>:<uid> data logs bangumi` step required on the host.
ARG USER_UID=1000
RUN useradd --system --create-home --home-dir /home/anigamer --uid ${USER_UID} anigamer && \
    mkdir -p /app/config /app/logs /app/bangumi && \
    chown -R anigamer:anigamer /app
ENV HOME=/tmp \
    UV_CACHE_DIR=/tmp/uv-cache
USER anigamer

EXPOSE 5000 5001
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["api"]
