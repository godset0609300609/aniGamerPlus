"""Pydantic models for every API payload.

This is the single source of truth for the shape of data exchanged with the
Vue frontend. Every request body, response body, and websocket message is
described here.
"""

from __future__ import annotations

import re
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


class ConfigSchema(pydantic.BaseModel):
    """List of keys the Web UI is allowed to read / write."""

    keys: list[str]


# ---------------------------------------------------------------------------
# Telegram settings (defined early so WebSettings can reference it)
# ---------------------------------------------------------------------------


class TelegramSettings(pydantic.BaseModel):
    """Telegram Bot integration configuration."""

    model_config = pydantic.ConfigDict(extra='ignore')

    enabled: bool = False
    bot_token: str = ''
    webhook_secret: str = ''  # path segment + X-Telegram-Bot-Api-Secret-Token
    public_url: str = ''  # e.g. "https://example.com" — used to build webhook URL
    # The bot's own @handle (no leading '@'), e.g. "aniGamerPlusBot". Used by
    # app.tg_downloader.notification_binder to fire a same-account `/start`
    # from a user's newly-bound Telegram *User* API session, merging the
    # tg_downloader bind with the existing Bot-API notification binding
    # without the user having to do it manually a second time.
    #
    # B-08 (security audit): validated at write time so a malformed value
    # can't silently sit in config.json until it fails at bind-time deep
    # inside notification_binder.py. Optional '@' prefix (matches how
    # app.tg_downloader.notification_binder.NotificationBinder.bind
    # normalizes a stored value that's missing it) and empty string
    # (unconfigured) are both allowed — same shape as _BOT_USERNAME_RE
    # there, just permitting the bare (no '@') form too.
    bot_username: str = pydantic.Field(default='', pattern=r'^(@?\w{4,32})?$')
    notify_on: list[str] = pydantic.Field(
        default_factory=lambda: ['started', 'completed', 'failed', 'cancelled', 'auto_enqueue'],
        min_length=1,
    )
    admin_broadcast: bool = True  # also DM every admin user who is bound + opted-in
    rate_limit_per_minute: int = pydantic.Field(default=30, ge=1, le=300)
    health_alerts: bool = True  # admin disk-low / cookie-expired DMs


class TelegramSettingsPublic(pydantic.BaseModel):
    """Client-facing projection of :class:`TelegramSettings`.

    ``bot_token`` and ``webhook_secret`` are deliberately omitted — they must
    never round-trip through ``GET /api/config`` / ``PUT /api/config``.  Use
    ``PUT /api/config/telegram-bot-token`` and
    ``PUT /api/config/telegram-webhook-secret`` to set them instead (same
    write-only pattern as ``/api/config/cookie``).
    """

    model_config = pydantic.ConfigDict(extra='ignore')

    enabled: bool = False
    public_url: str = ''
    bot_username: str = ''
    notify_on: list[str] = pydantic.Field(
        default_factory=lambda: ['started', 'completed', 'failed', 'cancelled', 'auto_enqueue'],
        min_length=1,
    )
    admin_broadcast: bool = True
    rate_limit_per_minute: int = pydantic.Field(default=30, ge=1, le=300)
    health_alerts: bool = True


# ---------------------------------------------------------------------------
# BT downloader settings (defined early so WebSettings can reference it)
# ---------------------------------------------------------------------------


class BtDownloaderSettings(pydantic.BaseModel):
    """RSS -> keyword filter -> Put.io -> bangumi_dir pipeline configuration."""

    model_config = pydantic.ConfigDict(extra='ignore', populate_by_name=True)

    enabled: bool = pydantic.Field(default=False, alias='enabled')
    poll_interval_seconds: int = pydantic.Field(default=300, ge=60, le=3600, alias='poll-interval-seconds')
    landing_poll_seconds: int = pydantic.Field(default=60, ge=30, le=600, alias='landing-poll-seconds')
    hanzi_convert: bool = pydantic.Field(
        default=True, alias='hanzi-convert'
    )  # normalize 簡體->繁體 before keyword matching
    landing_dir: str = pydantic.Field(default='', alias='landing-dir')  # empty = use bangumi_dir
    # Retention (fix #31): daily housekeeping deletes bt_feed_entry rows
    # older than entry_retention_days (when unmatched, or matched+landed)
    # and task_history rows older than task_history_retention_days. See
    # BtRetentionService / bt_retention_tick.
    entry_retention_days: int = pydantic.Field(default=90, ge=1, alias='entry-retention-days')
    task_history_retention_days: int = pydantic.Field(default=180, ge=1, alias='task-history-retention-days')
    # Put.io storage is finite — once a file has landed locally the remote
    # copy has no further value. When True, LandingWorker deletes the
    # Put.io file right after a successful landing (best-effort; failure is
    # logged but never fails the landing itself). See
    # BtFeedEntryRepository.mark_remote_cleared / mark_remote_removed.
    auto_delete_remote_on_landed: bool = pydantic.Field(default=True, alias='auto-delete-remote-on-landed')


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
    bilibili_concurrent_parts: int = pydantic.Field(default=2, alias='bilibili-concurrent-parts', ge=1, le=5)
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
    telegram: TelegramSettingsPublic = pydantic.Field(default_factory=TelegramSettingsPublic)
    bt_downloader: BtDownloaderSettings = pydantic.Field(default_factory=BtDownloaderSettings, alias='bt-downloader')


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
    source: T.Literal['animad', 'bilibili'] = 'animad'
    bilingual: bool = False


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
    # Discord avatar URL for the owner (admin view only; None for downloader
    # view, or when the owner has no custom Discord avatar set).
    owner_avatar_url: str | None = None
    # Source tracking for multi-platform downloads
    source: str | None = None
    external_id: str | None = None


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
    source: str | None = None
    external_id: str | None = None


# ---------------------------------------------------------------------------
# Bilibili cookie (write-only — never returned to the client)
# ---------------------------------------------------------------------------


class BilibiliCookieUpdateRequest(pydantic.BaseModel):
    """Request body for PUT /config/bilibili-cookie."""

    cookie: str = pydantic.Field(..., min_length=1, max_length=8192)


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
    bilingual: bool = False  # opt-in: download both 日文/中文配音 variants; dub gets [中] suffix
    comment: str = ''  # inline ``#`` text (without the ``#`` prefix)

    # Owner fields: None means "assign to the calling user" on write.
    # On read, owner_id is the user_id that owns the entry; owner_username
    # is the human-readable name (populated for all authenticated callers).
    owner_id: str | None = None
    owner_username: str | None = None

    # Duplicate detection (Feature B).
    # Set when this entry is disabled because another entry has the same anime_name.
    # Points to the id of the earliest entry with the same name.
    duplicate_of_entry_id: int | None = None
    # Resolved fields — populated by the service for the UI tooltip.
    duplicate_of_bangumi_name: str | None = None
    duplicate_of_owner_username: str | None = None

    # Read-only, derived fields (set by the service, ignored on write):
    anime_name: str | None = None
    downloaded_episodes: int = 0
    known_episodes: int = 0


class AnimeListPayload(pydantic.BaseModel):
    entries: list[AnimeListEntry]


# ---------------------------------------------------------------------------
# Put.io token (write-only — never returned to the client)
# ---------------------------------------------------------------------------


class PutioTokenUpdateRequest(pydantic.BaseModel):
    """Request body for PUT /config/putio-token.

    ``token`` is the raw Put.io OAuth bearer token. The backend writes it
    verbatim to ``putio_token.txt``; it is **never** echoed back.

    D-4 (security audit): ``SecretStr`` keeps the token out of repr()/str()
    by accident (e.g. an uncaught-exception traceback that includes local
    variables) — the route handler must explicitly unwrap it via
    ``.get_secret_value()`` to get the plaintext before writing it to disk.
    """

    token: pydantic.SecretStr = pydantic.Field(..., min_length=1, max_length=8192)


# ---------------------------------------------------------------------------
# Telegram bot token / webhook secret (write-only — never returned to the client)
# ---------------------------------------------------------------------------


class TelegramBotTokenUpdateRequest(pydantic.BaseModel):
    """Request body for PUT /config/telegram-bot-token."""

    bot_token: str = pydantic.Field(..., min_length=1, max_length=512)


class TelegramWebhookSecretUpdateRequest(pydantic.BaseModel):
    """Request body for PUT /config/telegram-webhook-secret."""

    webhook_secret: str = pydantic.Field(..., min_length=1, max_length=512)


# ---------------------------------------------------------------------------
# BT downloader — RSS feeds / keyword filters / ingested entries
# ---------------------------------------------------------------------------


class BtFeed(pydantic.BaseModel):
    """A configured RSS/Atom feed source, as persisted in ``bt_feed``."""

    id: int
    name: str
    url: str
    title_key: str = 'title'
    link_key: str = 'link'
    guid_key: str | None = None
    author_key: str | None = None
    enabled: bool = True
    created_at: str
    updated_at: str
    entry_count: int = 0


class BtFeedCreate(pydantic.BaseModel):
    """Request body for creating a new feed."""

    name: str = pydantic.Field(..., min_length=1)
    url: str = pydantic.Field(..., min_length=1)
    title_key: str = 'title'
    link_key: str = 'link'
    guid_key: str | None = None
    author_key: str | None = None
    enabled: bool = True


class BtFeedUpdate(pydantic.BaseModel):
    """Partial update for an existing feed.

    Every field defaults to "unset" — only fields explicitly present in the
    request body are applied (see ``model_dump(exclude_unset=True)`` in
    :class:`~app.persistence.bt_feed_repo.BtFeedRepository`). This lets a
    caller clear a nullable field (e.g. ``guid_key=null``) while leaving
    every other field untouched.
    """

    name: str | None = None
    url: str | None = None
    title_key: str | None = None
    link_key: str | None = None
    guid_key: str | None = None
    author_key: str | None = None
    enabled: bool | None = None


class BtFilter(pydantic.BaseModel):
    """One AND-keyword filter rule.

    ``keywords`` is a plain list on the Python side; the repository
    (de)serialises it to/from ``keywords_json`` in the ``bt_filter`` table.
    ``id`` / ``created_at`` / ``updated_at`` are ``None`` for a not-yet-persisted
    filter submitted by the UI as part of a ``replace_all`` call.
    """

    id: int | None = None
    name: str = ''
    keywords: list[str] = pydantic.Field(default_factory=list)
    enabled: bool = True
    sort_order: int = 0
    created_at: str | None = None
    updated_at: str | None = None


class BtFilterPayload(pydantic.BaseModel):
    filters: list[BtFilter]


class BtFeedEntry(pydantic.BaseModel):
    """One RSS entry ingested from a feed. Read-only from the API's perspective."""

    id: int
    feed_id: int
    guid: str
    title: str
    link: str
    author: str | None = None
    published_at: str | None = None
    fetched_at: str
    matched_filter_id: int | None = None
    dispatched_at: str | None = None
    putio_transfer_id: int | None = None
    putio_status: str | None = None
    local_path: str | None = None
    # ISO-8601 UTC — set once the remote Put.io file has been cleaned up
    # (either auto-deleted after landing, or detected as externally removed
    # by the periodic remote-status-refresh pass). None while still present
    # (or not yet checked) on Put.io's side.
    remote_cleared_at: str | None = None


class BtFeedProbeRequest(pydantic.BaseModel):
    """Request body for POST /api/bt/feeds/probe."""

    url: str = pydantic.Field(..., min_length=1)


class BtProbeResult(pydantic.BaseModel):
    """Result of a feed dry-run probe, used by the "add feed" wizard."""

    available_keys: list[str]
    sample_entries: list[dict[str, T.Any]]


class BtMatchCountRequest(pydantic.BaseModel):
    """Request body for POST /api/bt/filters/match-count."""

    keywords: list[T.Annotated[str, pydantic.StringConstraints(min_length=1, max_length=100)]] = pydantic.Field(
        ..., min_length=1, max_length=50
    )


class BtMatchCountResponse(pydantic.BaseModel):
    """Response body for POST /api/bt/filters/match-count."""

    count: int
    over_cap: bool = False


class BtEntriesPage(pydantic.BaseModel):
    """Response body for GET /api/bt/entries — a paginated slice of feed entries."""

    items: list[BtFeedEntry]
    total: int
    page: int
    size: int


class BtDispatchResponse(pydantic.BaseModel):
    """Response body for POST /api/bt/entries/{entry_id}/dispatch."""

    transfer_id: int
    status: str


# ---------------------------------------------------------------------------
# Telegram User API downloader — per-Discord-user MTProto session, watched
# chats, and download ledger.
# ---------------------------------------------------------------------------

#: 'pending' — QR not yet scanned. 'awaiting_code' — phone flow only, code
#: sent, waiting for the user to submit it (also re-entered after a wrong or
#: expired code). 'awaiting_password' — 2FA enabled, waiting for the account
#: password. 'success'/'failed' — terminal states.
TgLoginStatus = T.Literal['pending', 'awaiting_code', 'awaiting_password', 'success', 'failed']
#: 'no_session' — never bound. 'active' — bound + usable. 'revoked' — user
#: unbound. 'expired' — session no longer valid (e.g. revoked from another
#: device); surfaced so the frontend can prompt a re-bind.
TgSessionStatusValue = T.Literal['no_session', 'active', 'revoked', 'expired']


class TgSessionStatus(pydantic.BaseModel):
    """Response body for GET /api/tg/session — never includes the session string."""

    status: TgSessionStatusValue = 'no_session'
    phone_tail4: str | None = None
    telegram_user_id: int | None = None
    telegram_handle: str | None = None
    last_active_at: str | None = None
    notification_bound: bool = False
    #: Outcome of the most recent notification-bind attempt (one of
    #: ``app.tg_downloader.notification_binder.NotificationBindResult``'s
    #: values), or ``None`` if none has ever been attempted for this session.
    notification_bind_status: str | None = None
    notification_bind_error: str | None = None


class TgQrLoginResponse(pydantic.BaseModel):
    """Response body for POST /api/tg/session/qr-login.

    B-10 (security audit): the raw ``tg://login?token=...`` deep link
    (``qr_code_url``) used to be echoed back alongside the rendered PNG.
    That link *is* the login credential — anyone who opens it while already
    signed in to Telegram on another device completes the bind — so
    returning it as plaintext JSON (logged by proxies, browser devtools,
    etc.) needlessly widened its exposure. The frontend only ever rendered
    the PNG (``qr_code_png_base64``), never this field, so it's dropped
    entirely rather than kept for a hypothetical consumer.
    """

    login_token: str
    qr_code_png_base64: str  # data:image/png;base64,... rendered server-side


class TgRebindNotificationResponse(pydantic.BaseModel):
    """Response body for POST /api/tg/session/rebind-notification."""

    notification_bind_status: str
    notification_bind_error: str | None = None


class TgLoginStatusResponse(pydantic.BaseModel):
    """Response body for the QR/phone login poll + submit endpoints."""

    status: TgLoginStatus
    error: str | None = None
    telegram_handle: str | None = None


class TgPasswordRequest(pydantic.BaseModel):
    # D-4 (security audit): SecretStr keeps the 2FA password out of repr()/
    # str()/logging by accident (e.g. an uncaught-exception traceback that
    # includes local variables) — callers must explicitly unwrap it via
    # ``.get_secret_value()`` to get the plaintext.
    password: pydantic.SecretStr = pydantic.Field(..., min_length=1, max_length=256)


class TgPhoneLoginRequest(pydantic.BaseModel):
    # B-07 (security audit): E.164 — '+' followed by 7-15 digits, no leading
    # zero. Rejects free-form garbage before it ever reaches hydrogram's
    # send_code (which would otherwise surface a much less friendly
    # PhoneNumberInvalid error several network round-trips later). The
    # pattern is enforced by the field_validator below (not a bare
    # ``Field(pattern=...)``) so the 422 carries a clear Traditional-Chinese
    # message instead of pydantic's generic "String should match pattern ...".
    phone: str = pydantic.Field(..., min_length=3, max_length=32)

    @pydantic.field_validator('phone')
    @classmethod
    def _validate_phone_format(cls, v: str) -> str:
        if not re.fullmatch(r'\+[1-9]\d{6,14}', v):
            raise ValueError('phone 格式錯誤，須為 E.164 格式（例如 +886912345678，開頭 + 後接 7-15 位數字）')
        return v


class TgPhoneLoginResponse(pydantic.BaseModel):
    login_token: str
    phone: str


class TgCodeRequest(pydantic.BaseModel):
    # D-4 (security audit): see TgPasswordRequest.password above.
    code: pydantic.SecretStr = pydantic.Field(..., min_length=1, max_length=32)


#: One of 'pending' / 'running' / 'done' / 'failed', or ``None`` if a
#: backfill has never been requested for the chat. See
#: ``app.tg_downloader.backfill.TgBackfillService``.
TgBackfillStatus = T.Literal['pending', 'running', 'done', 'failed']

#: B-05 (security audit): the closed vocabulary ``app.tg_downloader.downloader``
#: actually understands (see its ``_MEDIA_TYPE_...`` mapping) — was a bare
#: ``list[str]``, silently accepting (and persisting) any garbage string
#: that would then just never match any downloaded message.
TgMediaType = T.Literal['video', 'photo', 'document', 'audio']

#: B-06 (security audit): per-item length cap for ``format_whitelist``
#: entries — a file-extension whitelist has no legitimate reason to hold an
#: entry longer than a handful of characters (``mp4``, ``mkv``, ...).
_FormatWhitelistItem = T.Annotated[str, pydantic.StringConstraints(max_length=10)]


class TgWatchedChat(pydantic.BaseModel):
    """One watched Telegram chat, as persisted in ``tg_watched_chat``."""

    id: int
    chat_id: int
    chat_title: str
    media_types: list[str] = pydantic.Field(default_factory=lambda: ['video'])
    size_min_mb: int | None = None
    size_max_mb: int | None = None
    format_whitelist: list[str] | None = None
    save_path: str | None = None
    enabled: bool = True
    created_at: str
    # ---- historical backfill (see app.tg_downloader.backfill.TgBackfillService) ----
    backfill_enabled: bool = False
    backfill_days: int = 7
    backfill_status: TgBackfillStatus | None = None
    backfill_scanned_count: int = 0
    backfill_matched_count: int = 0
    backfill_started_at: str | None = None
    backfill_finished_at: str | None = None
    # ---- periodic catch-up scan cursor (see app.tg_downloader.catchup.TgCatchupService) ----
    # Server-managed, same as the backfill_* fields above — never settable via
    # TgWatchedChatCreate/TgWatchedChatUpdate, only ever written by
    # TgCatchupService.run_one via TgWatchedChatRepository.update_scan_cursor_state.
    # NOTE: tg_watched_chat also has scan_resume_offset_id/scan_pending_cursor
    # columns (revision 0021) that deliberately do NOT appear here. Those two
    # are pure internal bookkeeping for an in-progress multi-tick capped
    # sweep (see TgCatchupService's module docstring) with no observability
    # value of their own beyond what last_scanned_message_id/last_scanned_at
    # already surface — read via TgWatchedChatRepository.get_scan_cursor_state
    # instead, never through this API-facing model.
    last_scanned_message_id: int | None = None
    last_scanned_at: str | None = None


def _reject_save_path_traversal(v: str | None) -> str | None:
    """Reject a ``save_path`` containing a literal ``..`` path segment.

    This is the static, config-independent half of the TG landing-root
    confinement guard (HIGH-1 of the security audit) — bad values are
    rejected here at write time (422) instead of being silently skipped
    later at download time. It cannot catch every escape on its own (an
    *absolute* path outside the landing root, or a symlink that resolves
    outside it, both require knowing the runtime landing-root
    configuration) — that half is enforced at download time by
    ``app.tg_downloader.downloader.TgDownloadWatcher._resolve_save_dir``,
    which resolves the path and confirms it lands inside the configured
    root regardless of how it got there.
    """
    if not v:
        return v
    segments = re.split(r'[\\/]+', v)
    if '..' in segments:
        raise ValueError('save_path 不可包含 ".." 上層目錄跳脫')
    return v


class TgWatchedChatCreate(pydantic.BaseModel):
    chat_id: int
    chat_title: str = pydantic.Field(..., min_length=1)
    media_types: list[TgMediaType] = pydantic.Field(
        default_factory=lambda: T.cast('list[TgMediaType]', ['video']), min_length=1
    )
    size_min_mb: int | None = pydantic.Field(default=None, ge=0)
    size_max_mb: int | None = pydantic.Field(default=None, ge=0)
    # B-06: capped at 20 entries, 10 chars each — an unbounded list/item
    # length had no practical use and let a request body balloon for free.
    format_whitelist: list[_FormatWhitelistItem] | None = pydantic.Field(default=None, max_length=20)
    save_path: str | None = None
    enabled: bool = True
    backfill_enabled: bool = False
    backfill_days: int = pydantic.Field(default=7, ge=1, le=90)

    @pydantic.field_validator('save_path')
    @classmethod
    def _validate_save_path(cls, v: str | None) -> str | None:
        return _reject_save_path_traversal(v)


class TgWatchedChatUpdate(pydantic.BaseModel):
    """Partial update — only fields explicitly present are applied."""

    chat_title: str | None = None
    media_types: list[TgMediaType] | None = None
    size_min_mb: int | None = None
    size_max_mb: int | None = None
    format_whitelist: list[_FormatWhitelistItem] | None = pydantic.Field(default=None, max_length=20)
    save_path: str | None = None
    enabled: bool | None = None
    backfill_enabled: bool | None = None
    backfill_days: int | None = pydantic.Field(default=None, ge=1, le=90)

    @pydantic.field_validator('save_path')
    @classmethod
    def _validate_save_path(cls, v: str | None) -> str | None:
        return _reject_save_path_traversal(v)


class TgAvailableChat(pydantic.BaseModel):
    """One entry in GET /api/tg/chats/available — a chat the user is a member of."""

    chat_id: int
    title: str
    type: str  # 'private' | 'group' | 'supergroup' | 'channel' | 'bot'
    already_watched: bool = False


class TgAvailableChatsResponse(pydantic.BaseModel):
    """Response body for GET /api/tg/chats/available.

    B-09/G-07 (security audit): a Telegram account can be a member of an
    unbounded number of chats — returning every one of them in a single
    response was an easy way to force a large live MTProto fetch and a
    large JSON payload. The listing is now capped server-side at *limit*
    (default/max enforced by the ``limit`` query param on the route); when
    more were available, ``truncated`` is set and the frontend prompts the
    user to narrow down via the picker's search box instead.
    """

    items: list[TgAvailableChat]
    truncated: bool = False
    #: Always equal to ``len(items)`` — kept as its own field (rather than
    #: making the client compute it) so the "顯示前 N 個" copy has an
    #: explicit count to interpolate. When ``truncated`` is True this is a
    #: lower bound on the account's true total dialog count, not an exact
    #: count (fetching the exact total would defeat the point of capping
    #: the fetch).
    total_seen: int = 0


class TgDownloadedMedia(pydantic.BaseModel):
    """One row in ``tg_downloaded_media``. Read-only from the API's perspective."""

    id: int
    chat_id: int
    chat_title: str | None = None
    message_id: int
    file_name: str
    file_size: int
    downloaded_at: str
    local_path: str


class TgDownloadsPage(pydantic.BaseModel):
    """Response body for GET /api/tg/downloads."""

    items: list[TgDownloadedMedia]
    total: int
    page: int
    size: int


class TgRedownloadResponse(pydantic.BaseModel):
    """Response body for POST /api/tg/downloads/{id}/redownload.

    The actual download runs in the background (a dramatiq actor) — this
    response only confirms the job was accepted and queued, not that the
    file has been replaced yet. ``entry_id`` echoes the request so the
    frontend has something to key its per-row pending state on without
    re-parsing the URL it just called.
    """

    entry_id: int
    status: T.Literal['queued'] = 'queued'


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
    multi_thread: int = pydantic.Field(default=1, alias='multi-thread', ge=1, le=5)
    multi_upload: int = pydantic.Field(default=3, ge=1)
    bilibili_concurrent_parts: int = pydantic.Field(default=2, alias='bilibili-concurrent-parts', ge=1)
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

    # FTP
    upload_to_server: bool = False
    ftp: FtpSettings = pydantic.Field(default_factory=FtpSettings)

    # Dashboard
    dashboard: DashboardSettings = pydantic.Field(default_factory=DashboardSettings)

    # Danmu
    danmu: bool = False
    danmu_ban_words: list[str] = pydantic.Field(default_factory=list)

    # Misc
    save_logs: bool = True
    quantity_of_logs: int = pydantic.Field(default=7, ge=1)

    # Auth (v17.3+)
    auth: DiscordAuthSettings = pydantic.Field(default_factory=DiscordAuthSettings)

    # Telegram (v17.4+)
    telegram: TelegramSettings = pydantic.Field(default_factory=TelegramSettings)

    # BT downloader (v17.5+)
    bt_downloader: BtDownloaderSettings = pydantic.Field(default_factory=BtDownloaderSettings, alias='bt-downloader')

    # Versions (no range — legacy values may be older after migration reads them)
    config_version: float = 17.2
    database_version: float = 2.0

    def web_subset(self) -> WebSettings:
        """Project the 26 keys used by the Web UI."""
        # Construct from alias-form dict so "multi-thread" maps correctly.
        blob = self.model_dump(by_alias=True)
        return WebSettings.model_validate(blob)
