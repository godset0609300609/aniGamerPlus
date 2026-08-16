"""Container — the composition root for the FastAPI backend and CLI.

Wires every long-lived collaborator (repos, downloader helpers, scheduler
components) once at startup. Both the dashboard (``app/main.py``) and the
command-line entry point (``app/cli.py``) build a :class:`Container`
first and then hand it to the thing they care about.

Tests substitute individual fields via ``dataclasses.replace(container, ...)``
or by overriding FastAPI dependency resolvers.
"""

from __future__ import annotations

import collections.abc
import dataclasses
import functools
import os
import pathlib
import typing as T

import httpx
import redis
import redis.asyncio

from .auth.discord_oauth import DiscordOAuthClient
from .bt_downloader.landing_worker import LandingWorker
from .bt_downloader.putio_client import PutioClient
from .downloader.anime import Anime
from .downloader.bilibili.runner import BilibiliRunner
from .downloader.bilibili.ytdlp_downloader import YtdlpDownloader
from .downloader.danmu import DanmuRenderer
from .downloader.ffmpeg import FFmpegRunner, resolve_ffmpeg_path
from .downloader.ffmpeg_downloader import FFmpegDownloader
from .downloader.filename import FilenameBuilder
from .downloader.http_client import AniGamerHttpClient
from .downloader.m3u8_client import M3u8Client
from .downloader.metadata import MetadataExtractor
from .downloader.progress import ProgressBus
from .downloader.segment_downloader import SegmentDownloader
from .downloader.uploader_ftp import FtpUploader
from .integrations.my_anime_export import MyAnimeExporter
from .logging_ import Logger
from .persistence.anime_list_repo import AnimeListEntryRepository
from .persistence.bilibili_cookie_repo import BilibiliCookieRepository
from .persistence.bt_feed_entry_repo import BtFeedEntryRepository
from .persistence.bt_feed_repo import BtFeedRepository
from .persistence.bt_filter_repo import BtFilterRepository
from .persistence.cookie_repo import CookieRepository
from .persistence.db import Database
from .persistence.paths import WorkspacePaths
from .persistence.putio_token_repo import PutioTokenRepository
from .persistence.repositories import AnimeRepository
from .persistence.settings_repo import SettingsRepository
from .persistence.sn_list_repo import SnListRepository
from .persistence.task_history_repo import TaskHistoryRepository
from .persistence.task_id_map_repo import TaskIdMapRepository
from .persistence.tg_downloaded_media_repo import TgDownloadedMediaRepository
from .persistence.tg_session_repo import TgSessionRepository
from .persistence.tg_watched_chat_repo import TgWatchedChatRepository
from .persistence.user_repo import UserRepository
from .redis_state import MessageIdRegistry
from .scheduler.cd_counter import DownloadCooldown
from .scheduler.manual_runner import ManualRunner
from .scheduler.queue_ import TaskQueue
from .scheduler.signals import SignalHandler
from .scheduler.update_loop import UpdateLoop
from .scheduler.worker import DownloadWorker
from .services.bt_downloader_service import BtDownloaderService
from .services.bt_manual_dispatch_service import BtManualDispatchService
from .services.bt_probe_service import BtProbeService
from .services.bt_progress_reconciler import BtProgressReconciler
from .services.bt_retention_service import BtRetentionService
from .services.redis_progress_reader import RedisProgressReader
from .services.telegram_live_menu import LiveMenuRegistry
from .services.telegram_live_messages import BtLiveMessageRegistry, LiveMessageRegistry

if T.TYPE_CHECKING:
    from .models import AppSettings
    from .scheduler.watchdog import SchedulerWatchdog
    from .services.telegram_client import TelegramClient
    from .services.telegram_commands import TelegramCommandDispatcher
    from .services.telegram_menu import MenuRenderer
    from .services.telegram_rate_limiter import TelegramRateLimiter
    from .services.tg_service import TgService
    from .tg_downloader.backfill import TgBackfillService
    from .tg_downloader.catchup import TgCatchupService


@dataclasses.dataclass
class Container:
    """Composition root. All long-lived collaborators are wired here; tests
    substitute individual fields via ``dataclasses.replace(container, ...)``.
    """

    paths: WorkspacePaths
    logger: Logger
    settings_repo: SettingsRepository
    sn_list_repo: SnListRepository
    cookie_repo: CookieRepository
    bilibili_cookie_repo: BilibiliCookieRepository
    putio_token_repo: PutioTokenRepository
    database: Database
    anime_repo: AnimeRepository
    user_repo: UserRepository
    anime_list_entry_repo: AnimeListEntryRepository
    task_history_repo: TaskHistoryRepository
    task_id_map_repo: TaskIdMapRepository
    bt_feed_repo: BtFeedRepository
    bt_filter_repo: BtFilterRepository
    bt_feed_entry_repo: BtFeedEntryRepository
    tg_session_repo: TgSessionRepository
    tg_watched_chat_repo: TgWatchedChatRepository
    tg_downloaded_media_repo: TgDownloadedMediaRepository
    bt_downloader_service: BtDownloaderService
    bt_manual_dispatch_service: BtManualDispatchService
    bt_probe_service: BtProbeService
    bt_landing_worker: LandingWorker
    bt_retention_service: BtRetentionService
    oauth_client: DiscordOAuthClient
    progress_bus: ProgressBus
    # Separate ProgressBus instance (in-memory state, shares the same Redis
    # mirror as ``progress_bus`` so both feed the same WS-visible snapshot)
    # feeding BT downloader/landing-worker live-monitor entries. Deliberately
    # NOT the same instance as ``progress_bus``: BT already writes
    # task_history directly (BtDownloaderService/LandingWorker's own
    # task_history_repo calls), so wiring the shared, history_repo-backed
    # ``progress_bus`` here would double-INSERT a task_history row per BT
    # dispatch (once from the direct call, once from ProgressBus.start()'s
    # own persistence). Constructed with ``history_repo=None`` to keep
    # ProgressBus purely a live/in-memory + Redis-mirrored view for BT rows.
    bt_progress_bus: ProgressBus
    http_client: AniGamerHttpClient
    metadata_extractor: MetadataExtractor
    m3u8_client: M3u8Client
    danmu_renderer: DanmuRenderer
    filename_builder: FilenameBuilder
    ffmpeg: FFmpegRunner
    segment_downloader: SegmentDownloader
    ffmpeg_downloader: FFmpegDownloader
    uploader: FtpUploader
    task_queue: TaskQueue
    cooldown: DownloadCooldown
    parse_cooldown: DownloadCooldown
    manual_runner: ManualRunner
    bilibili_runner: BilibiliRunner
    my_anime_exporter: MyAnimeExporter
    signals: SignalHandler
    # Sync client used by RedisProgressMirror (must stay sync — called from
    # sync ProgressBus callbacks on the downloader thread).
    redis_client_sync: redis.Redis | None = None
    # Async client used by RedisProgressReader + MessageIdRegistry.
    redis_client_async: redis.asyncio.Redis | None = None
    redis_progress_reader: RedisProgressReader | None = None
    # Boot-time ghost-task reconciliation — see the class docstring in
    # bt_progress_reconciler.py. None when redis_progress_reader is None
    # (no cross-process mirror = no ghost state to reconcile).
    bt_progress_reconciler: BtProgressReconciler | None = None
    message_id_registry: MessageIdRegistry | None = None
    # Per-(sn, chat_id) live progress message tracker (Redis-backed).
    live_messages: LiveMessageRegistry | None = None
    # Per-(entry_id, chat_id) live BT status message tracker (Redis-backed).
    bt_live_messages: BtLiveMessageRegistry | None = None
    # None when bot_token is empty; instantiated by the API process only.
    telegram_client: TelegramClient | None = None
    # None when bot_token is empty; rate limiter + dispatcher for webhook commands.
    telegram_rate_limiter: TelegramRateLimiter | None = None
    telegram_command_dispatcher: TelegramCommandDispatcher | None = None
    # Per-user menu message-id tracker (Redis-backed); None when Redis unavailable.
    live_menu: LiveMenuRegistry | None = None
    # Menu page renderer for /menu control panel; None when bot_token is empty.
    menu_renderer: MenuRenderer | None = None
    # None when TG_API_ID/TG_API_HASH are not configured (the Telegram User
    # API downloader feature is entirely opt-in via those env vars).
    tg_service: TgService | None = None
    # Runs a single chat's historical backfill scan (app.tasks.tg_backfill_tick
    # dramatiq actor). Same opt-in gate as tg_service — None when
    # TG_API_ID/TG_API_HASH are not configured.
    tg_backfill_service: TgBackfillService | None = None
    # Runs the periodic cursor-based catch-up sweep across every enabled
    # watched chat (app.tasks.tg_poll_tick dramatiq actor, scheduled by
    # ApsScheduler independent of this being None — the actor itself
    # no-ops). Same opt-in gate as tg_service — None when TG_API_ID/
    # TG_API_HASH are not configured.
    tg_catchup_service: TgCatchupService | None = None

    def anime_factory(self, sn: int) -> Anime:
        """Build an :class:`Anime` orchestrator wired with this container's collaborators."""
        settings = self.settings_repo.load()
        uploader: FtpUploader | None = self.uploader if settings.upload_to_server else None
        return Anime(
            int(sn),
            metadata_extractor=self.metadata_extractor,
            m3u8_client=self.m3u8_client,
            segment_downloader=self.segment_downloader,
            ffmpeg_downloader=self.ffmpeg_downloader,
            filename_builder=self.filename_builder,
            danmu_renderer=self.danmu_renderer,
            uploader=uploader,
            progress=self.progress_bus,
            settings=settings,
            paths=self.paths,
            logger=self.logger,
            cooldown=self.cooldown,
        )

    def build_worker(self) -> DownloadWorker:
        """Build a :class:`DownloadWorker` using the container's collaborators."""
        from .tasks.telegram import notify_event_actor

        def _notify_event_send(*, kwargs: dict[str, object]) -> None:
            notify_event_actor.send_with_options(kwargs=kwargs)

        notify_event_send = _notify_event_send if self.telegram_client is not None else None
        return DownloadWorker(
            queue=self.task_queue,
            anime_factory=self.anime_factory,
            anime_repo=self.anime_repo,
            progress=self.progress_bus,
            settings_provider=self.settings_repo.load,
            logger=self.logger,
            notify_event_send=notify_event_send,
            anime_list_repo=self.anime_list_entry_repo,
        )

    def build_update_loop(self, watchdog: SchedulerWatchdog | None = None) -> UpdateLoop:
        """Build an :class:`UpdateLoop` using the container's collaborators.

        Parameters
        ----------
        watchdog:
            Optional pre-built watchdog.  If ``None`` a new
            :class:`~app.scheduler.watchdog.SchedulerWatchdog` is created
            and started automatically so callers don't have to.
        """
        from .scheduler.watchdog import SchedulerWatchdog

        if watchdog is None:
            watchdog = SchedulerWatchdog(self.logger)
            watchdog.start()
        from .tasks.telegram import notify_event_actor

        def _notify_event_send_loop(*, kwargs: dict[str, object]) -> None:
            notify_event_actor.send_with_options(kwargs=kwargs)

        notify_event_send = _notify_event_send_loop if self.telegram_client is not None else None
        return UpdateLoop(
            settings_repo=self.settings_repo,
            sn_list_repo=self.sn_list_repo,
            anime_list_entry_repo=self.anime_list_entry_repo,
            anime_repo=self.anime_repo,
            queue=self.task_queue,
            worker=self.build_worker(),
            metadata_extractor=self.metadata_extractor,
            logger=self.logger,
            cookie_repo=self.cookie_repo,
            progress_bus=self.progress_bus,
            watchdog=watchdog,
            parse_cooldown=self.parse_cooldown,
            notify_event_send=notify_event_send,
        )


@functools.lru_cache(maxsize=1)
def build_container() -> Container:
    """Wire up every collaborator from real dependencies. Call once per process.

    Reads paths from :meth:`WorkspacePaths.detect`, loads :class:`AppSettings`
    once, and runs Alembic baseline migrations on the DB exactly once. The
    result is cached so every service factory / CLI / lifespan call shares
    the same instance; this is what keeps Alembic from re-running (and
    spamming its ``Context impl SQLiteImpl`` log) on each HTTP request.

    Tests never touch this — they construct their own :class:`Container`
    or :class:`FakeContainer` and pass it directly.
    """
    paths = WorkspacePaths.detect()

    # Defer settings_repo's logger dependency by building a preliminary
    # logger, loading settings, then swapping to a logger whose knobs match
    # the user's config.
    preliminary_logger = Logger(
        paths.logs_dir,
        save_logs=False,
        quantity_of_logs=7,
    )
    settings_repo = SettingsRepository(paths, preliminary_logger)
    settings: AppSettings = settings_repo.load()

    logger = Logger(
        paths.logs_dir,
        save_logs=settings.save_logs,
        quantity_of_logs=settings.quantity_of_logs,
    )
    # Keep settings_repo's logger in sync with user config.
    settings_repo = SettingsRepository(paths, logger)

    sn_list_repo = SnListRepository(paths, logger)
    cookie_repo = CookieRepository(paths, logger)
    bilibili_cookie_repo = BilibiliCookieRepository(paths)
    putio_token_repo = PutioTokenRepository(paths)

    database = Database(f'sqlite:///{paths.db_path.as_posix()}', logger)
    database.run_baseline_migrations()
    anime_repo = AnimeRepository(database)
    user_repo = UserRepository(database)
    anime_list_entry_repo = AnimeListEntryRepository(database)
    task_history_repo = TaskHistoryRepository(database)
    task_id_map_repo = TaskIdMapRepository(database)
    bt_feed_repo = BtFeedRepository(database)
    bt_filter_repo = BtFilterRepository(database)
    bt_feed_entry_repo = BtFeedEntryRepository(database)
    tg_session_repo = TgSessionRepository(database)
    tg_watched_chat_repo = TgWatchedChatRepository(database)
    tg_downloaded_media_repo = TgDownloadedMediaRepository(database)

    # Discord OAuth client (shared async HTTP client lifetime = process).
    _oauth_http = httpx.AsyncClient()
    oauth_client = DiscordOAuthClient(settings.auth, _oauth_http)

    from .dramatiq_setup import get_redis_url

    redis_client_sync: redis.Redis | None = None
    redis_client_async: redis.asyncio.Redis | None = None
    redis_progress_mirror = None
    redis_progress_reader = None
    message_id_registry = None
    live_messages = None
    bt_live_messages = None
    live_menu = None
    try:
        redis_url = get_redis_url()
        # socket_connect_timeout bounds the TCP handshake; without it a host
        # that drops the SYN instead of refusing the connection (observed on
        # Linux for e.g. 127.0.0.1:1, vs. Windows' instant ECONNREFUSED)
        # blocks ping() forever instead of hitting the except-fallback below.
        redis_client_sync = redis.Redis.from_url(redis_url, socket_connect_timeout=2, socket_timeout=2)
        # Eagerly ping so a misconfigured Redis fails fast instead of silently
        # losing every mirror publish.  In tests / single-process CLI without
        # Redis available, fall through to None and operate without the mirror.
        redis_client_sync.ping()
        redis_client_async = redis.asyncio.Redis.from_url(redis_url, socket_connect_timeout=2, socket_timeout=2)
        # No async ping here — pinging in async context at module-import time
        # would require an event loop; the FastAPI lifespan can ping if needed.
        from .downloader.redis_progress_mirror import RedisProgressMirror

        redis_progress_mirror = RedisProgressMirror(redis_client_sync)
        redis_progress_reader = RedisProgressReader(redis_client_async)
        message_id_registry = MessageIdRegistry(redis_client_async)
        live_messages = LiveMessageRegistry(redis_client_async)
        bt_live_messages = BtLiveMessageRegistry(redis_client_async)
        live_menu = LiveMenuRegistry(redis_client_async)
    except Exception as exc:  # noqa: BLE001 — connection refused etc.
        logger.info(
            None,
            'Bootstrap',
            f'Redis 不可用，progress mirror 與 cancel registry 將停用: {exc}',
            display=False,
        )
        redis_client_sync = None
        redis_client_async = None

    progress_bus = ProgressBus(history_repo=task_history_repo, mirror=redis_progress_mirror)
    # See the Container.bt_progress_bus field docstring for why this is a
    # separate instance (history_repo=None) rather than reusing progress_bus.
    bt_progress_bus = ProgressBus(mirror=redis_progress_mirror)

    http_client = AniGamerHttpClient(settings, cookie_repo, logger)
    metadata_extractor = MetadataExtractor(http_client, settings, logger)
    m3u8_client = M3u8Client(http_client, settings, settings_repo, logger)
    danmu_renderer = DanmuRenderer(http_client, logger)
    filename_builder = FilenameBuilder(settings)

    ffmpeg = FFmpegRunner(paths.working_dir, logger)
    segment_downloader = SegmentDownloader(http_client, settings, ffmpeg, progress_bus, logger)
    ffmpeg_downloader = FFmpegDownloader(settings, ffmpeg, progress_bus, logger)

    uploader = FtpUploader(settings.ftp, logger)

    task_queue = TaskQueue(
        max_download=settings.multi_thread,
        max_upload=settings.multi_upload,
    )

    def _download_cd_seconds() -> int:
        return settings_repo.load().download_cd

    def _parse_cd_seconds() -> int:
        return settings_repo.load().parse_cd

    cooldown = DownloadCooldown(_download_cd_seconds, logger)
    parse_cooldown = DownloadCooldown(_parse_cd_seconds, logger, label='解析冷卻')

    my_anime_exporter = MyAnimeExporter(http_client, logger)
    signals = SignalHandler(logger)

    # Build TelegramClient and related services for all processes that have a
    # bot_token configured.  The API process uses the client for
    # webhook/send-message; the scheduler process wires notify_event_actor
    # into the worker and update-loop for download-event DMs.
    telegram_client = None
    telegram_rate_limiter = None
    telegram_command_dispatcher = None
    menu_renderer = None

    # Build the optional notify_event_send closure first so ManualRunner can be
    # wired with it regardless of where the telegram block lives.  Set to None
    # when no bot_token is configured so the runner stays a no-op in CLI mode.
    _notify_event_send_for_manual: collections.abc.Callable[..., None] | None = None
    if settings.telegram.bot_token:
        from .tasks.telegram import notify_event_actor as _notify_event_actor_manual

        def _manual_notify_event_send(*, kwargs: dict[str, object]) -> None:
            _notify_event_actor_manual.send_with_options(kwargs=kwargs)

        _notify_event_send_for_manual = _manual_notify_event_send

    def _anime_factory(sn: int) -> Anime:
        # Defer to the container's factory once it's been built; this closure
        # is captured by ManualRunner so it can be re-bound to the final
        # Container instance below.
        return container.anime_factory(int(sn))

    manual_runner = ManualRunner(
        anime_factory=_anime_factory,
        anime_repo=anime_repo,
        settings=settings,
        logger=logger,
        progress_bus=progress_bus,
        metadata_extractor=metadata_extractor,
        parse_cooldown=parse_cooldown,
        notify_event_send=_notify_event_send_for_manual,
        anime_list_repo=anime_list_entry_repo,
    )

    bangumi_dir = pathlib.Path(settings.bangumi_dir) if settings.bangumi_dir else paths.bangumi_dir_default
    ytdlp_downloader = YtdlpDownloader(
        progress_bus=progress_bus,
        cookie_repo=bilibili_cookie_repo,
        bangumi_dir=bangumi_dir,
        logger=logger,
        ffmpeg_location=resolve_ffmpeg_path(),
    )
    bilibili_runner = BilibiliRunner(
        ytdlp_downloader=ytdlp_downloader,
        progress_bus=progress_bus,
        logger=logger,
        settings=settings,
        notify_event_send=_notify_event_send_for_manual,
        anime_list_repo=anime_list_entry_repo,
        task_id_map_repo=task_id_map_repo,
    )

    def _putio_client_factory(token: str) -> PutioClient:
        return PutioClient(oauth_token=token)

    bt_downloader_service = BtDownloaderService(
        bt_feed_repo,
        bt_filter_repo,
        bt_feed_entry_repo,
        _putio_client_factory,
        putio_token_repo,
        settings.bt_downloader,
        logger=logger,
        notify_event_send=_notify_event_send_for_manual,
        task_history_repo=task_history_repo,
        task_id_map_repo=task_id_map_repo,
        progress_bus=bt_progress_bus,
    )
    bt_manual_dispatch_service = BtManualDispatchService(
        bt_feed_entry_repo,
        _putio_client_factory,
        putio_token_repo,
        bt_feed_repo=bt_feed_repo,
        bt_filter_repo=bt_filter_repo,
        notify_event_send=_notify_event_send_for_manual,
        logger=logger,
        task_history_repo=task_history_repo,
        task_id_map_repo=task_id_map_repo,
        progress_bus=bt_progress_bus,
    )
    bt_probe_service = BtProbeService()

    bt_landing_dir = (
        pathlib.Path(settings.bt_downloader.landing_dir)
        if settings.bt_downloader.landing_dir
        else paths.bangumi_dir_default
    )
    bt_landing_worker = LandingWorker(
        _putio_client_factory(putio_token_repo.read()),
        bt_feed_entry_repo,
        bt_landing_dir,
        bt_feed_repo=bt_feed_repo,
        bt_filter_repo=bt_filter_repo,
        notify_event_send=_notify_event_send_for_manual,
        task_history_repo=task_history_repo,
        task_id_map_repo=task_id_map_repo,
        progress_bus=bt_progress_bus,
        logger=logger,
        settings_repo=settings_repo,
    )
    bt_retention_service = BtRetentionService(
        bt_feed_entry_repo,
        task_history_repo,
        settings_repo,
        logger=logger,
    )
    bt_progress_reconciler = BtProgressReconciler(
        bt_feed_entry_repo,
        tg_downloaded_media_repo,
        task_id_map_repo,
        bt_progress_bus,
        progress_bus,
        redis_progress_reader,
        task_history_repo=task_history_repo,
        logger=logger,
    )

    if settings.telegram.bot_token:
        from .services.animelist_service import AnimeListService as _AnimeListService
        from .services.progress_service import ProgressService as _ProgressService
        from .services.task_service import TaskService as _TaskService
        from .services.telegram_client import TelegramClient as _TelegramClient
        from .services.telegram_commands import TelegramCommandDispatcher as _TelegramCommandDispatcher
        from .services.telegram_menu import MenuRenderer as _MenuRenderer
        from .services.telegram_rate_limiter import TelegramRateLimiter as _TelegramRateLimiter

        telegram_client = _TelegramClient(settings.telegram.bot_token)

        def _rate_limit_provider() -> int:
            return settings_repo.load().telegram.rate_limit_per_minute

        telegram_rate_limiter = _TelegramRateLimiter(_rate_limit_provider)

        from .services.telegram_client_cache import resolve_telegram_client as _resolve_client

        def _client_provider() -> _TelegramClient | None:
            return _resolve_client(settings_repo.load().telegram.bot_token)

        _animelist_svc = _AnimeListService(sn_list_repo, anime_repo, anime_list_entry_repo, user_repo)
        _progress_svc = _ProgressService(progress_bus, user_repo, redis_progress_reader, bt_progress_bus)
        _task_svc = _TaskService(
            settings_repo,
            manual_runner,
            progress_bus=progress_bus,
            progress_service=_progress_svc,
            message_id_registry=message_id_registry,
        )

        menu_renderer = _MenuRenderer(
            user_repo=user_repo,
            animelist_service=_animelist_svc,
            task_service=_task_svc,
            progress_service=_progress_svc,
            task_history_repo=task_history_repo,
            settings_provider=settings_repo.load,
            telegram_settings_provider=lambda: settings_repo.load().telegram,
            public_url=settings.telegram.public_url,
        )

        telegram_command_dispatcher = _TelegramCommandDispatcher(
            client_provider=_client_provider,
            user_repo=user_repo,
            animelist_service=_animelist_svc,
            task_service=_task_svc,
            progress_service=_progress_svc,
            rate_limiter=telegram_rate_limiter,
            logger=logger,
            metadata_extractor=metadata_extractor,
            menu_renderer=menu_renderer,
            live_menu=live_menu,
        )

    # Telegram User API downloader (MTProto, via hydrogram) — entirely
    # opt-in via TG_API_ID/TG_API_HASH. Imports are deferred into this
    # block (matching the `if settings.telegram.bot_token:` block above)
    # so processes that don't use the feature never pay hydrogram's import
    # cost (see app/tg_downloader/__init__.py's docstring for why, unlike
    # pyrogram, this no longer needs an event-loop compat shim).
    tg_service = None
    tg_backfill_service = None
    tg_catchup_service = None
    _tg_api_id_raw = os.environ.get('TG_API_ID', '')
    _tg_api_hash = os.environ.get('TG_API_HASH', '')
    if _tg_api_id_raw and _tg_api_hash:
        try:
            _tg_api_id = int(_tg_api_id_raw)
        except ValueError:
            logger.error(
                None, 'Bootstrap', f'TG_API_ID 不是合法整數: {_tg_api_id_raw!r}，Telegram User API 停用', display=False
            )
        else:
            from .services.tg_service import TgService as _TgService
            from .services.tg_service import resolve_bot_username as _resolve_bot_username
            from .tg_downloader.backfill import TgBackfillService as _TgBackfillService
            from .tg_downloader.catchup import TgCatchupService as _TgCatchupService
            from .tg_downloader.client_pool import TgClientPool as _TgClientPool
            from .tg_downloader.downloader import TgDownloadWatcher as _TgDownloadWatcher
            from .tg_downloader.notification_binder import NotificationBinder as _NotificationBinder
            from .tg_downloader.phone_login import PhoneLoginService as _PhoneLoginService
            from .tg_downloader.qr_login import QrLoginService as _QrLoginService

            _tg_client_pool = _TgClientPool(_tg_api_id, _tg_api_hash, tg_session_repo, logger=logger)
            _tg_notification_binder = _NotificationBinder(
                lambda: _resolve_bot_username(settings_repo.load), logger=logger
            )
            _tg_qr_login = _QrLoginService(
                _tg_api_id, _tg_api_hash, tg_session_repo, notification_binder=_tg_notification_binder, logger=logger
            )
            _tg_phone_login = _PhoneLoginService(
                _tg_api_id, _tg_api_hash, tg_session_repo, notification_binder=_tg_notification_binder, logger=logger
            )
            _tg_bangumi_dir = pathlib.Path(settings.bangumi_dir) if settings.bangumi_dir else paths.bangumi_dir_default
            # HIGH-1 (security audit): every TG download must resolve inside
            # this root — defaults to bangumi_dir (unchanged pre-fix
            # behaviour) unless an operator opts into a dedicated root via
            # ANIGAMERPLUS_TG_LANDING_ROOT (mirrors bt_downloader.landing_dir's
            # "empty = use bangumi_dir" convention, just env-configurable
            # instead of a settings.json field since it's a rarely-touched
            # deployment-level knob).
            _tg_landing_root_env = os.environ.get('ANIGAMERPLUS_TG_LANDING_ROOT', '')
            _tg_landing_root = pathlib.Path(_tg_landing_root_env) if _tg_landing_root_env else _tg_bangumi_dir
            _tg_watcher = _TgDownloadWatcher(
                tg_watched_chat_repo,
                tg_downloaded_media_repo,
                _tg_bangumi_dir,
                landing_root=_tg_landing_root,
                task_history_repo=task_history_repo,
                task_id_map_repo=task_id_map_repo,
                progress_bus=progress_bus,
                notify_event_send=_notify_event_send_for_manual,
                logger=logger,
            )
            tg_service = _TgService(
                tg_session_repo,
                tg_watched_chat_repo,
                tg_downloaded_media_repo,
                _tg_client_pool,
                _tg_qr_login,
                _tg_phone_login,
                _tg_notification_binder,
                _tg_watcher,
                logger=logger,
            )
            tg_backfill_service = _TgBackfillService(
                _tg_client_pool,
                tg_watched_chat_repo,
                _tg_watcher,
                logger=logger,
            )
            tg_catchup_service = _TgCatchupService(
                _tg_client_pool,
                tg_watched_chat_repo,
                _tg_watcher,
                logger=logger,
            )

    container = Container(
        paths=paths,
        logger=logger,
        settings_repo=settings_repo,
        sn_list_repo=sn_list_repo,
        cookie_repo=cookie_repo,
        bilibili_cookie_repo=bilibili_cookie_repo,
        putio_token_repo=putio_token_repo,
        database=database,
        anime_repo=anime_repo,
        user_repo=user_repo,
        anime_list_entry_repo=anime_list_entry_repo,
        task_history_repo=task_history_repo,
        task_id_map_repo=task_id_map_repo,
        bt_feed_repo=bt_feed_repo,
        bt_filter_repo=bt_filter_repo,
        bt_feed_entry_repo=bt_feed_entry_repo,
        tg_session_repo=tg_session_repo,
        tg_watched_chat_repo=tg_watched_chat_repo,
        tg_downloaded_media_repo=tg_downloaded_media_repo,
        tg_service=tg_service,
        tg_backfill_service=tg_backfill_service,
        tg_catchup_service=tg_catchup_service,
        bt_downloader_service=bt_downloader_service,
        bt_manual_dispatch_service=bt_manual_dispatch_service,
        bt_probe_service=bt_probe_service,
        bt_landing_worker=bt_landing_worker,
        bt_retention_service=bt_retention_service,
        oauth_client=oauth_client,
        progress_bus=progress_bus,
        bt_progress_bus=bt_progress_bus,
        redis_client_sync=redis_client_sync,
        redis_client_async=redis_client_async,
        redis_progress_reader=redis_progress_reader,
        bt_progress_reconciler=bt_progress_reconciler,
        message_id_registry=message_id_registry,
        live_messages=live_messages,
        bt_live_messages=bt_live_messages,
        http_client=http_client,
        metadata_extractor=metadata_extractor,
        m3u8_client=m3u8_client,
        danmu_renderer=danmu_renderer,
        filename_builder=filename_builder,
        ffmpeg=ffmpeg,
        segment_downloader=segment_downloader,
        ffmpeg_downloader=ffmpeg_downloader,
        uploader=uploader,
        task_queue=task_queue,
        cooldown=cooldown,
        parse_cooldown=parse_cooldown,
        manual_runner=manual_runner,
        bilibili_runner=bilibili_runner,
        my_anime_exporter=my_anime_exporter,
        signals=signals,
        telegram_client=telegram_client,
        telegram_rate_limiter=telegram_rate_limiter,
        telegram_command_dispatcher=telegram_command_dispatcher,
        live_menu=live_menu,
        menu_renderer=menu_renderer,
    )
    return container


__all__ = [
    'Container',
    'build_container',
]
