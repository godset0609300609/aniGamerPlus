"""Container — the composition root for the FastAPI backend and CLI.

Wires every long-lived collaborator (repos, downloader helpers, scheduler
components) once at startup. Both the dashboard (``app/main.py``) and the
command-line entry point (``app/cli.py``) build a :class:`Container`
first and then hand it to the thing they care about.

Tests substitute individual fields via ``dataclasses.replace(container, ...)``
or by overriding FastAPI dependency resolvers.
"""

from __future__ import annotations

import dataclasses
import functools
import os
import typing as T

import httpx

from .auth.discord_oauth import DiscordOAuthClient
from .downloader.anime import Anime
from .downloader.danmu import DanmuRenderer
from .downloader.ffmpeg import FFmpegRunner
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
from .persistence.cookie_repo import CookieRepository
from .persistence.db import Database
from .persistence.paths import WorkspacePaths
from .persistence.repositories import AnimeRepository
from .persistence.settings_repo import SettingsRepository
from .persistence.sn_list_repo import SnListRepository
from .persistence.task_history_repo import TaskHistoryRepository
from .persistence.user_repo import UserRepository
from .scheduler.cd_counter import DownloadCooldown
from .scheduler.manual_runner import ManualRunner
from .scheduler.queue_ import TaskQueue
from .scheduler.signals import SignalHandler
from .scheduler.update_loop import UpdateLoop
from .scheduler.worker import DownloadWorker

if T.TYPE_CHECKING:
    from .api._scheduler_proxy import SchedulerProxy
    from .models import AppSettings
    from .scheduler.event_sink import DownloadEventSink
    from .scheduler.watchdog import SchedulerWatchdog
    from .services.telegram_client import TelegramClient
    from .services.telegram_commands import TelegramCommandDispatcher
    from .services.telegram_rate_limiter import TelegramRateLimiter


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
    database: Database
    anime_repo: AnimeRepository
    user_repo: UserRepository
    anime_list_entry_repo: AnimeListEntryRepository
    task_history_repo: TaskHistoryRepository
    oauth_client: DiscordOAuthClient
    progress_bus: ProgressBus
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
    my_anime_exporter: MyAnimeExporter
    signals: SignalHandler
    # None = scheduler process (no proxy needed); API process populates this.
    scheduler_proxy: SchedulerProxy | None = None
    # None when bot_token is empty; instantiated by the API process only.
    telegram_client: TelegramClient | None = None
    # None when bot_token is empty; used by the scheduler process to fire DMs.
    event_sink: DownloadEventSink | None = None
    # None when bot_token is empty; rate limiter + dispatcher for webhook commands.
    telegram_rate_limiter: TelegramRateLimiter | None = None
    telegram_command_dispatcher: TelegramCommandDispatcher | None = None

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
        return DownloadWorker(
            queue=self.task_queue,
            anime_factory=self.anime_factory,
            anime_repo=self.anime_repo,
            progress=self.progress_bus,
            settings_provider=self.settings_repo.load,
            logger=self.logger,
            event_sink=self.event_sink,
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

    database = Database(f'sqlite:///{paths.db_path.as_posix()}', logger)
    database.run_baseline_migrations()
    anime_repo = AnimeRepository(database)
    user_repo = UserRepository(database)
    anime_list_entry_repo = AnimeListEntryRepository(database)
    task_history_repo = TaskHistoryRepository(database)

    # Discord OAuth client (shared async HTTP client lifetime = process).
    _oauth_http = httpx.AsyncClient()
    oauth_client = DiscordOAuthClient(settings.auth, _oauth_http)

    progress_bus = ProgressBus(history_repo=task_history_repo)

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

    manual_runner_container: list[ManualRunner] = []

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
    )
    manual_runner_container.append(manual_runner)

    my_anime_exporter = MyAnimeExporter(http_client, logger)
    signals = SignalHandler(logger)

    # Build the scheduler proxy used by the API process.
    # The scheduler process itself leaves this as None.
    scheduler_url = os.environ.get('ANIGAMERPLUS_SCHEDULER_URL', 'http://127.0.0.1:5001')
    scheduler_secret = os.environ.get('ANIGAMERPLUS_INTERNAL_SECRET', '')
    from .api._scheduler_proxy import SchedulerProxy

    scheduler_proxy = SchedulerProxy(
        base_url=scheduler_url,
        secret=scheduler_secret,
        logger=None,  # uses module-level stdlib logger
    )

    # Build TelegramClient and TelegramNotifier / DownloadEventSink for all
    # processes that have a bot_token configured.  The API process uses the
    # client for webhook/send-message; the scheduler process uses the sink to
    # fire download-event DMs from the sync worker thread.
    telegram_client = None
    event_sink = None
    telegram_rate_limiter = None
    telegram_command_dispatcher = None
    if settings.telegram.bot_token:
        from .scheduler.event_sink import DownloadEventSink as _DownloadEventSink
        from .services.animelist_service import AnimeListService as _AnimeListService
        from .services.progress_service import ProgressService as _ProgressService
        from .services.task_service import TaskService as _TaskService
        from .services.telegram_client import TelegramClient as _TelegramClient
        from .services.telegram_commands import TelegramCommandDispatcher as _TelegramCommandDispatcher
        from .services.telegram_notifier import TelegramNotifier as _TelegramNotifier
        from .services.telegram_rate_limiter import TelegramRateLimiter as _TelegramRateLimiter

        telegram_client = _TelegramClient(settings.telegram.bot_token)
        _notifier = _TelegramNotifier(
            client=telegram_client,
            user_repo=user_repo,
            settings=settings.telegram,
            logger=logger,
        )
        event_sink = _DownloadEventSink(_notifier)

        telegram_rate_limiter = _TelegramRateLimiter(settings.telegram.rate_limit_per_minute)

        _animelist_svc = _AnimeListService(sn_list_repo, anime_repo, anime_list_entry_repo, user_repo)
        _progress_svc = _ProgressService(progress_bus, user_repo, scheduler_proxy)
        _task_svc = _TaskService(settings_repo, manual_runner, scheduler_proxy, _progress_svc)

        telegram_command_dispatcher = _TelegramCommandDispatcher(
            client=telegram_client,
            user_repo=user_repo,
            animelist_service=_animelist_svc,
            task_service=_task_svc,
            progress_service=_progress_svc,
            rate_limiter=telegram_rate_limiter,
            logger=logger,
        )

    container = Container(
        paths=paths,
        logger=logger,
        settings_repo=settings_repo,
        sn_list_repo=sn_list_repo,
        cookie_repo=cookie_repo,
        database=database,
        anime_repo=anime_repo,
        user_repo=user_repo,
        anime_list_entry_repo=anime_list_entry_repo,
        task_history_repo=task_history_repo,
        oauth_client=oauth_client,
        progress_bus=progress_bus,
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
        my_anime_exporter=my_anime_exporter,
        signals=signals,
        scheduler_proxy=scheduler_proxy,
        telegram_client=telegram_client,
        event_sink=event_sink,
        telegram_rate_limiter=telegram_rate_limiter,
        telegram_command_dispatcher=telegram_command_dispatcher,
    )
    return container


__all__ = [
    'Container',
    'build_container',
]
