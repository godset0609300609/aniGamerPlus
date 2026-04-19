"""Pydantic models for every API payload.

This is the single source of truth for the shape of data exchanged with the
Vue frontend. Every request body, response body, and websocket message is
described here.
"""

from __future__ import annotations

import typing as T

import pydantic

Resolution = T.Literal['360', '480', '540', '720', '1080']
DefaultDownloadMode = T.Literal['all', 'latest', 'largest-sn']
ManualDownloadMode = T.Literal['single', 'latest', 'all', 'largest-sn']


# ---------------------------------------------------------------------------
# Response envelopes
# ---------------------------------------------------------------------------


class SimpleStatus(pydantic.BaseModel):
    """Uniform response body for write endpoints."""

    status: str = 'ok'


class Health(pydantic.BaseModel):
    status: str = 'ok'
    version: str | None = None
    working_dir: str | None = None


class ConfigSchema(pydantic.BaseModel):
    """List of keys the Web UI is allowed to read / write."""

    keys: list[str]


# ---------------------------------------------------------------------------
# Web-visible config
# ---------------------------------------------------------------------------


class WebSettings(pydantic.BaseModel):
    """The subset of ``config.json`` surfaced in the Web UI.

    Legacy ``config.json`` uses a hyphen in ``multi-thread``; pydantic maps
    that to ``multi_thread`` via an alias. ``populate_by_name=True`` lets us
    construct the model from either form, and ``by_alias=True`` on dump
    round-trips back to the original JSON schema.
    """

    model_config = pydantic.ConfigDict(populate_by_name=True)

    bangumi_dir: str = ''
    temp_dir: str = ''
    classify_bangumi: bool = True
    lock_resolution: bool = False
    segment_download_mode: bool = True
    add_bangumi_name_to_video_filename: bool = True
    add_resolution_to_video_filename: bool = True
    download_resolution: Resolution = '1080'
    default_download_mode: DefaultDownloadMode = 'latest'
    check_frequency: int = pydantic.Field(default=5, ge=1)
    multi_thread: int = pydantic.Field(default=1, alias='multi-thread', ge=1)
    multi_downloading_segment: int = pydantic.Field(default=2, ge=1)
    customized_video_filename_prefix: str = ''
    customized_video_filename_suffix: str = ''
    ua: str = ''
    use_mobile_api: bool = False
    danmu: bool = False
    use_proxy: bool = False
    proxy: str = ''
    read_sn_list_when_checking_update: bool = True
    read_config_when_checking_update: bool = True
    save_logs: bool = True
    quantity_of_logs: int = pydantic.Field(default=7, ge=1)
    download_cd: int = pydantic.Field(default=60, ge=0)
    parse_sn_cd: int = pydantic.Field(default=5, ge=0)
    parse_cd: int = pydantic.Field(default=3, ge=0)


# ---------------------------------------------------------------------------
# Cookie (write-only — never returned to the client)
# ---------------------------------------------------------------------------


class CookieUpdateRequest(pydantic.BaseModel):
    """Request body for PUT /config/cookie.

    ``cookie`` is the raw cookie string pasted by the admin. The backend
    writes it verbatim to ``cookie.txt``; it is **never** echoed back.
    """

    cookie: str = pydantic.Field(..., min_length=1, max_length=8192)


# ---------------------------------------------------------------------------
# Manual task
# ---------------------------------------------------------------------------


class ManualTaskRequest(pydantic.BaseModel):
    sn: str | int
    resolution: Resolution = '1080'
    mode: ManualDownloadMode = 'single'
    thread: int = pydantic.Field(default=1, ge=1, le=50)
    classify: bool = True
    danmu: bool = False


# ---------------------------------------------------------------------------
# Task progress (websocket payload)
# ---------------------------------------------------------------------------


class TaskProgressEntry(pydantic.BaseModel):
    sn: int = 0  # numeric task id; 0 for backward-compat payloads that omit it
    rate: float
    status: str
    filename: str
    # Extended metadata / stats (all optional for backward compatibility)
    bangumi_name: str | None = None
    episode: str | None = None
    resolution: str | None = None
    speed_mbps: float | None = None
    eta_seconds: int | None = None
    retries: int = 0
    started_at: str | None = None  # ISO-8601 UTC string
    # Completion timestamp — ISO-8601 UTC string; None while task is active.
    # Set by ProgressBus.finish(); used by the frontend to place the task in
    # the 近期完成 column without waiting for the 60-second DB history poll.
    finished_at: str | None = None
    # Cooldown deadline — ISO-8601 UTC string; None when no cooldown is active.
    # Frontend uses this to display a live "冷卻 Ns" countdown.
    cooldown_until: str | None = None
    # Owner fields (admin view only; None for downloader view)
    owner_id: str | None = None
    owner_username: str | None = None


class TaskProgressSnapshot(pydantic.BaseModel):
    """Mapping of sn (as string) to a progress entry."""

    tasks: dict[str, TaskProgressEntry]


class TaskHistoryEntryOut(pydantic.BaseModel):
    """One row from ``task_history`` returned by ``GET /api/tasks/history``.

    Shape mirrors :class:`TaskProgressEntry` but with ``final_status`` and
    ``finished_at`` always present (required), as these are only emitted for
    completed / interrupted records.
    """

    id: int
    sn: int
    filename: str
    bangumi_name: str | None = None
    episode: str | None = None
    resolution: str | None = None
    final_status: str
    retries: int = 0
    started_at: str | None = None  # ISO-8601 UTC
    finished_at: str  # required — always set for completed rows
    owner_id: str | None = None


# ---------------------------------------------------------------------------
# Anime list (structured sn_list.txt)
# ---------------------------------------------------------------------------


AnimeListMode = T.Literal['single', 'latest', 'all', 'largest-sn']


class AnimeListEntry(pydantic.BaseModel):
    """A structured row from the anime watch list.

    The first block of fields round-trips to disk; the trailing fields are
    derived from ``aniGamer.db`` by the service and ignored on write.
    """

    sn: int
    enabled: bool = True
    mode: AnimeListMode | None = None  # None = fall back to settings default
    tag: str = ''  # category name (the ``@`` line)
    season: int = 1  # series season number; drives S{season:02d}E{ep:02d} filename
    custom_name: str | None = None  # user override for the name used in filenames
    comment: str = ''  # inline ``#`` text (without the ``#`` prefix)

    # Owner fields: None means "assign to the calling user" on write.
    # On read, owner_id is the user_id that owns the entry; owner_username
    # is the human-readable name (admin view only).
    owner_id: str | None = None
    owner_username: str | None = None

    # Read-only, derived fields (set by the service, ignored on write):
    anime_name: str | None = None
    downloaded_episodes: int = 0
    known_episodes: int = 0


class AnimeListPayload(pydantic.BaseModel):
    entries: list[AnimeListEntry]


# ---------------------------------------------------------------------------
# Full config.json schema (v17.2)
# ---------------------------------------------------------------------------


class FtpSettings(pydantic.BaseModel):
    server: str = ''
    port: int | T.Literal[''] = ''
    user: str = ''
    pwd: str = ''
    tls: bool = True
    cwd: str = ''
    show_error_detail: bool = False
    max_retry_num: int = pydantic.Field(default=15, ge=0)


class DashboardSettings(pydantic.BaseModel):
    host: str = '127.0.0.1'
    port: int = pydantic.Field(default=5000, ge=1, le=65535)
    SSL: bool = False
    BasicAuth: bool = False
    username: str = 'admin'
    password: str = 'admin'


class CoolQSettings(pydantic.BaseModel):
    msg_argument_name: str = 'message'
    message_suffix: str = ''
    query: list[str] = pydantic.Field(default_factory=list)


class DiscordAuthSettings(pydantic.BaseModel):
    """Discord OAuth2 authentication configuration (v17.3+)."""

    enabled: bool = False
    client_id: str = ''
    client_secret: str = ''
    redirect_uri: str = 'http://localhost:8000/api/auth/callback'
    bootstrap_admin_ids: list[str] = pydantic.Field(default_factory=list)
    session_secret: str = ''  # auto-generated on first boot


class AppSettings(pydantic.BaseModel):
    """Full config.json schema at v17.2. Extra keys are ignored.

    Always round-trips with ``by_alias=True`` so the legacy ``multi-thread``
    key stays unchanged on disk.
    """

    model_config = pydantic.ConfigDict(populate_by_name=True, extra='ignore')

    # Paths / output
    bangumi_dir: str = ''
    temp_dir: str = ''
    customized_video_filename_prefix: str = ''
    customized_video_filename_suffix: str = ''
    customized_bangumi_name_suffix: str = ''
    video_filename_extension: str = 'mp4'
    zerofill: int = pydantic.Field(default=1, ge=1)
    add_bangumi_name_to_video_filename: bool = True
    add_resolution_to_video_filename: bool = True
    classify_bangumi: bool = True
    classify_season: bool = False
    plex_naming: bool = False

    # Download
    download_resolution: Resolution = '1080'
    lock_resolution: bool = False
    only_use_vip: bool = False
    default_download_mode: DefaultDownloadMode = 'latest'
    use_copyfile_method: bool = False
    multi_thread: int = pydantic.Field(default=1, alias='multi-thread', ge=1, le=5)
    multi_upload: int = pydantic.Field(default=3, ge=1)
    segment_download_mode: bool = True
    multi_downloading_segment: int = pydantic.Field(default=2, ge=1, le=5)
    segment_max_retry: int = pydantic.Field(default=8, ge=-1)
    check_frequency: int = pydantic.Field(default=5, ge=1)
    download_cd: int = pydantic.Field(default=60, ge=0)
    parse_sn_cd: int = pydantic.Field(default=5, ge=0)
    parse_cd: int = pydantic.Field(default=3, ge=0)
    ads_time: int = 25
    mobile_ads_time: int = 25
    faststart_movflags: bool = False
    audio_language: bool = False
    use_mobile_api: bool = False
    read_sn_list_when_checking_update: bool = True
    read_config_when_checking_update: bool = True

    # HTTP
    ua: str = ''
    use_proxy: bool = False
    proxy: str = ''
    no_proxy_akamai: bool = False

    # FTP
    upload_to_server: bool = False
    ftp: FtpSettings = pydantic.Field(default_factory=FtpSettings)

    # Dashboard
    use_dashboard: bool = True
    dashboard: DashboardSettings = pydantic.Field(default_factory=DashboardSettings)

    # Notifications (flat, per v17.2 schema)
    coolq_notify: bool = False
    coolq_settings: CoolQSettings = pydantic.Field(default_factory=CoolQSettings)
    telebot_notify: bool = False
    telebot_token: str = ''
    telebot_use_chat_id: bool = False
    telebot_chat_id: str = ''
    discord_notify: bool = False
    discord_token: str = ''
    plex_refresh: bool = False
    plex_url: str = ''
    plex_token: str = ''
    plex_section: str = ''

    # Danmu
    danmu: bool = False
    danmu_ban_words: list[str] = pydantic.Field(default_factory=list)

    # Misc
    user_command: str = 'shutdown -s -t 60'
    save_logs: bool = True
    quantity_of_logs: int = pydantic.Field(default=7, ge=1)

    # Auth (v17.3+)
    auth: DiscordAuthSettings = pydantic.Field(default_factory=DiscordAuthSettings)

    # Versions (no range — legacy values may be older after migration reads them)
    config_version: float = 17.2
    database_version: float = 2.0

    def web_subset(self) -> WebSettings:
        """Project the 26 keys used by the Web UI."""
        # Construct from alias-form dict so "multi-thread" maps correctly.
        blob = self.model_dump(by_alias=True)
        return WebSettings.model_validate(blob)
