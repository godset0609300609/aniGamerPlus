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
    out.setdefault('use_copyfile_method', False)  # v6.0
    out.setdefault('zerofill', 1)  # v6.0
    out.setdefault('customized_bangumi_name_suffix', '')  # v7.0
    out.setdefault('user_command', 'shutdown -s -t 60')  # v8.0
    out.setdefault('segment_max_retry', 8)  # v9.0
    out.setdefault('faststart_movflags', False)  # v9.0
    out.setdefault('video_filename_extension', 'mp4')  # v17
    out.setdefault('audio_language', False)  # v19
    out.setdefault('telebot_notify', False)
    out.setdefault('telebot_token', '')
    out.setdefault('telebot_use_chat_id', False)
    out.setdefault('telebot_chat_id', '')
    out.setdefault('discord_notify', False)
    out.setdefault('discord_token', '')
    out.setdefault('plex_refresh', False)
    out.setdefault('plex_url', '')
    out.setdefault('plex_token', '')
    out.setdefault('plex_section', '')
    out.setdefault('use_dashboard', True)  # v20
    out.setdefault('ads_time', 25)
    out.setdefault('danmu', False)
    out.setdefault('danmu_ban_words', [])
    out.setdefault('use_mobile_api', False)  # v21.0
    out.setdefault('mobile_ads_time', 25)
    out.setdefault('only_use_vip', False)
    out.setdefault('no_proxy_akamai', False)  # v24.3
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

    # --- coolq_notify + coolq_settings -------------------------------------
    out.setdefault('coolq_notify', False)
    out['coolq_settings'] = _migrate_coolq(out.get('coolq_settings'))

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


def _migrate_coolq(raw: T.Any) -> dict[str, T.Any]:
    """Promote any known legacy coolq shape to the flat v17.2 shape."""
    if not isinstance(raw, dict):
        return {
            'msg_argument_name': 'message',
            'message_suffix': '',
            'query': [],
        }

    out = dict(raw)

    # v21.1 renamed ``user_message`` -> ``message_suffix``.
    if 'user_message' in out:
        out.setdefault('message_suffix', out['user_message'])
        out.pop('user_message', None)

    out.setdefault('msg_argument_name', 'message')
    out.setdefault('message_suffix', '')

    # Old shape: query is a dict, alongside SSL/host/port/api. Build a URL
    # from those pieces and make query a single-element list.
    if 'SSL' in out or 'host' in out or 'api' in out or isinstance(out.get('query'), dict):
        scheme = 'https://' if out.get('SSL') else 'http://'
        host = str(out.get('host', '127.0.0.1'))
        port = str(out.get('port', '5700'))
        api = str(out.get('api', 'send_group_msg'))
        query_dict = out.get('query') or {}
        if not isinstance(query_dict, dict):
            query_dict = {}
        qs = '&'.join(f'{k}={v}' for k, v in query_dict.items())
        url = f'{scheme}{host}:{port}/{api}'
        if qs:
            url = f'{url}?{qs}'
        out['query'] = [url]
        for stale in ('SSL', 'host', 'port', 'api'):
            out.pop(stale, None)

    query = out.get('query')
    if not isinstance(query, list):
        out['query'] = []

    # Keep only the three canonical keys; drop anything else the legacy
    # path may have scribbled in.
    return {
        'msg_argument_name': out.get('msg_argument_name', 'message'),
        'message_suffix': out.get('message_suffix', ''),
        'query': list(out.get('query') or []),
    }


def _as_float(value: T.Any, *, fallback: float) -> float:
    try:
        return float(value)
    except TypeError, ValueError:
        return fallback
