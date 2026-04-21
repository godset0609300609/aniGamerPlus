"""Promote a legacy ``config.json`` dict to the v17.2 shape.

The legacy ``Config.__update_settings`` is an append-only chain of
``if key not in settings: settings[key] = default`` blocks. We replay that
chain here in a pure-function form so that the pydantic ``AppSettings``
can do final validation with no data loss.

Everything here is deliberately defensive — malformed legacy configs are
still coerced to something valid rather than raised on.
"""

from __future__ import annotations

import typing as T

LATEST_CONFIG_VERSION = 17.2
LATEST_DATABASE_VERSION = 2.0

_DEFAULT_UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/107.0.0.0 Safari/537.36'
)


def migrate(raw: dict[str, T.Any]) -> dict[str, T.Any]:
    """Return a new dict matching the v17.2 schema.

    The input is never mutated. If ``raw`` already claims ``config_version
    >= 17.2`` we still copy it (so callers get fresh ownership), but we do
    NOT rewrite anything.
    """
    # Existing v17.2 config: pass through unchanged (still copy so callers
    # can mutate safely).
    current_version = _as_float(raw.get('config_version'), fallback=1.0)
    if current_version >= LATEST_CONFIG_VERSION:
        return dict(raw)

    out: dict[str, T.Any] = dict(raw)

    # --- FTP sub-dict -------------------------------------------------------
    ftp = dict(out.get('ftp') or {})
    ftp.setdefault('server', '')
    ftp.setdefault('port', '')
    ftp.setdefault('user', '')
    ftp.setdefault('pwd', '')
    ftp.setdefault('tls', True)  # v2.0
    ftp.setdefault('cwd', '')
    ftp.setdefault('show_error_detail', False)  # v2.0
    # Legacy default was 10, later bumped to 15 in config-sample.
    ftp.setdefault('max_retry_num', 15)
    out['ftp'] = ftp

    # --- top-level flat keys ------------------------------------------------
    out.setdefault('upload_to_server', False)  # v2.0
    out.setdefault('use_proxy', False)  # v2.0
    out.setdefault('read_sn_list_when_checking_update', True)  # v2.0
    out.setdefault('multi_upload', 3)  # v2.0
    out.setdefault('read_config_when_checking_update', True)  # v2.0
    out.setdefault('add_bangumi_name_to_video_filename', True)  # v3.0
    out.setdefault('segment_download_mode', True)  # v3.1
    out.setdefault('multi_downloading_segment', 2)  # v3.1
    out.setdefault('save_logs', True)  # v4.0
    out.setdefault('quantity_of_logs', 7)  # v4.0
    out.setdefault('temp_dir', '')  # v4.0
    out.setdefault('lock_resolution', False)  # v4.1
    out.setdefault('ua', _DEFAULT_UA)  # v4.2
    out.setdefault('classify_bangumi', True)  # v5.0
    out.setdefault('classify_season', False)
    out.setdefault('plex_naming', False)
    out.setdefault('zerofill', 1)  # v6.0
    out.setdefault('customized_bangumi_name_suffix', '')  # v7.0
    out.setdefault('segment_max_retry', 8)  # v9.0
    out.setdefault('faststart_movflags', False)  # v9.0
    out.setdefault('video_filename_extension', 'mp4')  # v17
    out.setdefault('audio_language', False)  # v19
    out.setdefault('ads_time', 25)
    out.setdefault('danmu', False)
    out.setdefault('danmu_ban_words', [])
    out.setdefault('use_mobile_api', False)  # v21.0
    out.setdefault('mobile_ads_time', 25)
    out.setdefault('only_use_vip', False)
    out.setdefault('download_cd', 60)  # v24.4
    out.setdefault('parse_sn_cd', 5)  # v24.4
    out.setdefault('parse_cd', 3)  # v24.4
    out.setdefault('check_frequency', 5)
    out.setdefault('download_resolution', '1080')
    out.setdefault('default_download_mode', 'latest')
    out.setdefault('multi-thread', 1)
    out.setdefault('customized_video_filename_prefix', '')
    out.setdefault('customized_video_filename_suffix', '')
    out.setdefault('add_resolution_to_video_filename', True)
    out.setdefault('bangumi_dir', '')

    # --- audio_language_jpn -> drop (superseded by audio_language) ---------
    out.pop('audio_language_jpn', None)

    # --- proxies dict -> scalar proxy --------------------------------------
    # Legacy v20 migration: if 'proxies' is present OR 'proxy' is missing,
    # collapse the dict (``{'1': 'http://...', ...}``) into a single scalar.
    proxies = out.pop('proxies', None)
    if proxies is not None:
        scalar = ''
        if isinstance(proxies, dict):
            # First try the legacy ``"1"`` key which was the primary proxy,
            # then fall back to http/https (PEP standard keys).
            scalar = proxies.get('1') or proxies.get('http') or proxies.get('https') or ''
        elif isinstance(proxies, str):
            scalar = proxies
        # Don't clobber an explicit proxy that already migrated successfully.
        if not out.get('proxy'):
            out['proxy'] = scalar
    out.setdefault('proxy', 'http://user:passwd@example.com:1000')

    # --- dashboard sub-dict ------------------------------------------------
    dashboard = dict(out.get('dashboard') or {})
    dashboard.setdefault('host', '127.0.0.1')
    dashboard.setdefault('port', 5000)
    dashboard.setdefault('SSL', False)
    dashboard.setdefault('BasicAuth', False)
    dashboard.setdefault('username', 'admin')
    dashboard.setdefault('password', 'admin')
    out['dashboard'] = dashboard

    # --- auth sub-dict (v17.3) --------------------------------------------
    auth = dict(out.get('auth') or {})
    auth.setdefault('enabled', False)
    auth.setdefault('client_id', '')
    auth.setdefault('client_secret', '')
    auth.setdefault('redirect_uri', 'http://localhost:8000/api/auth/callback')
    auth.setdefault('bootstrap_admin_ids', [])
    auth.setdefault('session_secret', '')
    out['auth'] = auth

    # --- version stamps ----------------------------------------------------
    out['config_version'] = LATEST_CONFIG_VERSION
    # Don't force database_version downward; preserve what the user has if
    # it's already >= latest, else bump.
    existing_db = _as_float(out.get('database_version'), fallback=1.0)
    out['database_version'] = max(existing_db, LATEST_DATABASE_VERSION)

    return out


def _as_float(value: T.Any, *, fallback: float) -> float:
    try:
        return float(value)
    except TypeError, ValueError:
        return fallback
