#!/usr/bin/env sh
set -e

case "${1:-}" in
    api)
        shift
        exec /app/.venv/bin/anigamerplus-api "$@"
        ;;
    scheduler)
        shift
        exec /app/.venv/bin/anigamerplus-scheduler "$@"
        ;;
    worker)
        shift
        exec /app/.venv/bin/dramatiq app.tasks "$@"
        ;;
    admin)
        shift
        exec /app/.venv/bin/anigamerplus-admin "$@"
        ;;
    cli)
        shift
        exec /app/.venv/bin/anigamerplus "$@"
        ;;
    *)
        exec "$@"
        ;;
esac
