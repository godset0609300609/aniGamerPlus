#!/usr/bin/env sh
set -e

case "${1:-}" in
    api)
        shift
        exec uv run anigamerplus-api "$@"
        ;;
    scheduler)
        shift
        exec uv run anigamerplus-scheduler "$@"
        ;;
    admin)
        shift
        exec uv run anigamerplus-admin "$@"
        ;;
    cli)
        shift
        exec uv run anigamerplus "$@"
        ;;
    *)
        # Pass through arbitrary commands (e.g. `uv run pytest`, `sh`, etc.)
        exec "$@"
        ;;
esac
