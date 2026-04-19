# aniGamerPlus backend (FastAPI)

FastAPI service that wraps the aniGamerPlus downloader (動畫瘋下載器) and
exposes the Web dashboard API consumed by `frontend/`.

## Requirements

- Python 3.14
- [uv](https://docs.astral.sh/uv/) for environment / dependency management

## Setup

```bash
cd backend
uv sync
```

This creates a `.venv/` pinned to Python 3.14 and installs all dependencies from
`pyproject.toml`.

## Run the API server

```bash
# production — starts FastAPI + the background auto-downloader
uv run anigamerplus-server
```

Or directly via uvicorn for development:

```bash
# dev: watch source only — prevents aniGamer.db / logs / config.json reloads
uv run uvicorn app.main:app --reload --reload-dir app --host 0.0.0.0 --port 8000
```

The server reads the same `config.json` / `sn_list.txt` / `cookie.txt` as the
classic CLI (kept in this `backend/` directory). The SQLite schema is brought
up to the current migration head automatically at container construction time
(effectively `uv run alembic upgrade head`); you only need to run Alembic
manually when authoring a new migration.

### Background scheduler

Starting the server also runs the periodic auto-downloader
(`UpdateLoop.run_forever`) on a daemon thread — the same loop
`uv run anigamerplus` runs in the foreground. This matches legacy
behaviour where one process owned both the Flask dashboard and the
`check_tasks` loop; you do **not** need to keep `anigamerplus` running
alongside the server.

Set `ANIGAMERPLUS_DISABLE_SCHEDULER=1` to turn the background scheduler
off (the pytest `client` fixture does exactly this). Useful when you
want to poke at the HTTP API without triggering real downloads, or
when you already run `anigamerplus` on a separate host / container.

### Why `--reload-dir app`?

`uvicorn --reload` / `fastapi dev` watch the whole working directory by
default. At container construction `build_container()` runs Alembic's
`upgrade head`; even when the schema is already at head, opening the
SQLite file touches `aniGamer.db`'s mtime, which triggers the reloader
to restart the process, which opens the DB again, ad infinitum. Scoping
the watcher to `--reload-dir app` keeps it focused on Python source and
avoids the loop. The production launcher (`uv run anigamerplus-server`)
does not use `--reload`, so it's unaffected.

### ffmpeg dependency

The downloader pipeline shells out to `ffmpeg` for segment muxing and
for the audio-only `.m4a` branch. If it's missing,
`FFmpegRunner.run_ffmpeg` raises `FileNotFoundError` with a descriptive
message. Install it one of:

- Windows: `winget install Gyan.FFmpeg` or drop `ffmpeg.exe` into the
  `backend/` directory next to `config.json`.
- macOS: `brew install ffmpeg`.
- Linux: your distro's package manager, e.g. `apt install ffmpeg`.

Official builds and source: <https://ffmpeg.org/download.html>.

### HTTPS (optional)

To enable TLS, set `dashboard.SSL = true` in `config.json` and put your own
`server.crt` / `server.key` pair in `backend/sslkey/`. The folder is
`.gitignore`d; we do **not** ship a default self-signed certificate. For
production, terminate TLS at a reverse proxy (nginx / caddy) instead — it's
more flexible and you keep uvicorn behind it on plain HTTP.

## Run the downloader CLI

```bash
uv run anigamerplus            # automatic mode (reads sn_list.txt)
uv run anigamerplus -s 12345   # single episode
```

## Module layout

The backend follows an object-oriented layout under `app/`:

| Path                            | Purpose                                                        |
| ------------------------------- | -------------------------------------------------------------- |
| `app/core.py`                   | `Container` — composition root wiring every collaborator       |
| `app/cli.py`                    | `anigamerplus` CLI entry point (auto mode, manual mode, `--my_anime`) |
| `app/main.py`                   | `anigamerplus-server` — FastAPI app factory + uvicorn launcher |
| `app/models.py`                 | Pydantic models (`AppSettings`, `WebSettings`, payloads) — single source of truth for every HTTP contract |
| `app/logging_.py`               | Coloured, thread-safe logger                                   |
| `app/settings_id_list.py`       | `WEB_SETTINGS_KEYS` — the 26 config keys the web UI can edit   |
| `app/api/`                      | FastAPI routers: config / sn_list / anime-list / tasks / health / progress ws |
| `app/services/`                 | Service layer sitting between routers and repos; `_factory.py::container_bound` factors out the lazy `build_container()` boilerplate once shared by every `get_*_service` |
| `app/persistence/`              | Repos (`SettingsRepository`, `SnListRepository`, `AnimeRepository`, `CookieRepository`) + SQLAlchemy DB wrapper + Alembic migrations (applied automatically by `Database.run_baseline_migrations()`) |
| `app/downloader/`               | Downloader pipeline: `http_client` / `metadata` / `m3u8_client` / `segment_downloader` / `ffmpeg` (+ `ffmpeg_downloader`) / `filename` / `danmu` / `progress` / `uploader_ftp` / `notifier` / `anime` (orchestrator) |
| `app/scheduler/`                | `TaskQueue`, `DownloadWorker`, `UpdateLoop`, `ManualRunner`, `DownloadCooldown`, `SignalHandler` |
| `app/integrations/`             | `MyAnimeExporter` — scrapes the "My Anime" list                |

## Tests

```bash
uv run pytest
```

`pyproject.toml` sets `filterwarnings = ["error"]` under `[tool.pytest.ini_options]`,
so any `DeprecationWarning` / `ResourceWarning` / Pydantic / SQLAlchemy warning
fails the suite. When introducing dependencies or touching async / resource
lifecycles, treat warnings as errors and fix them at the source rather than
silencing them in the config.

## HTTP API

| Method | Path                       | Purpose                                                   |
| ------ | -------------------------- | --------------------------------------------------------- |
| GET    | `/api/config`              | Read the subset of config exposed to the web UI           |
| PUT    | `/api/config`              | Save config from the web UI                               |
| GET    | `/api/sn_list`             | Plain-text `sn_list.txt` contents                         |
| PUT    | `/api/sn_list`             | Replace `sn_list.txt`                                     |
| GET    | `/api/anime-list`          | Structured "追番清單" view over `sn_list.txt` + DB        |
| PUT    | `/api/anime-list`          | Replace the anime list                                    |
| POST   | `/api/tasks/manual`        | Enqueue a manual download task                            |
| GET    | `/api/health`              | Liveness probe                                            |
| WS     | `/api/ws/tasks_progress`   | Stream the downloader's progress table every second       |

Basic Auth protects every endpoint if `dashboard.BasicAuth` is enabled in
`config.json`; the credentials are the ones configured there.
