"""Periodic update loop — port of legacy ``aniGamerPlus.__main__``.

Checks every sn in ``sn_dict``, compares against the DB, enqueues the
appropriate episodes (per the sn's ``mode``), and spawns one worker
thread per newly-queued sn. The loop sleeps ``settings.check_frequency``
minutes between iterations.

This module owns neither the worker pool nor the queue — both are
injected at construction time. Workers run in daemon threads so a
``SIGINT`` / ``SIGTERM`` can unwind them immediately.
"""

from __future__ import annotations

import threading
import time
import typing as T

from ..downloader import exceptions

if T.TYPE_CHECKING:
    from ..downloader.metadata import MetadataExtractor
    from ..downloader.progress import ProgressBus
    from ..logging_ import Logger
    from ..persistence.anime_list_repo import AnimeListEntryRepository
    from ..persistence.cookie_repo import CookieRepository
    from ..persistence.repositories import AnimeRepository
    from ..persistence.settings_repo import SettingsRepository
    from ..persistence.sn_list_repo import SnListRepository
    from .cd_counter import DownloadCooldown
    from .queue_ import TaskInfo, TaskQueue
    from .watchdog import SchedulerWatchdog
    from .worker import DownloadWorker


class UpdateLoop:
    """Periodic check-for-updates loop.

    Equivalent of legacy ``aniGamerPlus`` ``__main__`` + ``check_tasks``.
    """

    def __init__(
        self,
        *,
        settings_repo: SettingsRepository,
        sn_list_repo: SnListRepository,
        anime_list_entry_repo: AnimeListEntryRepository,
        anime_repo: AnimeRepository,
        queue: TaskQueue,
        worker: DownloadWorker,
        metadata_extractor: MetadataExtractor,
        logger: Logger,
        cookie_repo: CookieRepository,
        progress_bus: ProgressBus | None = None,
        watchdog: SchedulerWatchdog | None = None,
        parse_cooldown: DownloadCooldown | None = None,
    ) -> None:
        self._settings_repo = settings_repo
        self._sn_list_repo = sn_list_repo
        self._anime_list_entry_repo = anime_list_entry_repo
        self._anime_repo = anime_repo
        self._queue = queue
        self._worker = worker
        self._metadata_extractor = metadata_extractor
        self._logger = logger
        self._cookie_repo = cookie_repo
        # Optional for backward compatibility with call sites that haven't
        # been updated yet; when present, queue-ingress announces each sn
        # on the progress bus so the monitor shows ``'等待下載'`` before a
        # worker has picked the task up.
        self._progress_bus = progress_bus
        self._watchdog = watchdog
        # Optional: pool-wide gap between successive sn fetches, matching the
        # legacy ``parse_cd`` behaviour.  No cooldown when None.
        self._parse_cooldown = parse_cooldown
        self._stop_event = threading.Event()
        self._sleep = time.sleep  # injectable for tests
        # Guard so the legacy-file warning fires only once per scheduler boot.
        self._legacy_warning_emitted = False

    # ------------------------------------------------------------------ public

    def stop(self) -> None:
        """Request the next ``run_forever`` iteration to exit."""
        self._stop_event.set()

    def check_tasks(self, sn_dict: dict[int, dict[str, str]]) -> None:
        """One pass through ``sn_dict`` — enqueue new eps + spawn workers.

        Picks which episodes to enqueue based on the per-sn ``mode``
        (``single`` / ``latest`` / ``all`` / ``largest-sn``).

        ``sn_dict`` values may contain an ``"owner_id"`` key alongside the
        standard ``"mode"``, ``"tag"``, and ``"season"`` keys.  When present,
        it is forwarded to queue ingress and the progress bus so the spawned
        task is attributed to the correct user.
        """
        settings = self._settings_repo.load()
        upload_to_server = settings.upload_to_server

        self._logger.info(
            None,
            '自動掃描',
            f'掃描追番清單 ({len(sn_dict)} 個項目)',
            display=False,
        )
        # Beat once at scan-start so a single-item list still refreshes the
        # heartbeat even before the parse_cooldown path fires.
        if self._watchdog is not None:
            self._watchdog.beat()

        newly_added = 0
        sn_items = list(sn_dict.items())
        for idx, (sn, info) in enumerate(sn_items):
            # (A) Log "正在檢查 {name}" BEFORE fetching — use the cached name
            # from the anime_list_entries table if available, otherwise fall
            # back to the bare sn identifier.
            cached_name: str | None = None
            try:
                entry = next(
                    (e for e in self._anime_list_entry_repo.list_all() if e.sn == int(sn)),
                    None,
                )
                if entry is not None:
                    cached_name = entry.anime_name or entry.custom_name
            except Exception:  # noqa: BLE001 — best-effort; never block the scan
                pass
            display_name = f'《{cached_name}》' if cached_name else f'sn={sn}'
            self._logger.info(
                sn,
                '更新資訊',
                f'正在檢查{display_name}',
                display=False,
            )

            # (B) Fetch metadata (existing behaviour).
            try:
                metadata = self._metadata_extractor.fetch(int(sn))
            except exceptions.InvalidCookieError as exc:
                self._logger.error(
                    sn,
                    '更新狀態',
                    f'cookie 無效: {exc}; 標記失效',
                    display=False,
                )
                self._cookie_repo.invalidate()
                continue
            except exceptions.NoAvailableStreamError as exc:
                self._logger.error(
                    sn,
                    '更新狀態',
                    f'無影片源: {exc}',
                    display=False,
                )
                continue
            except exceptions.TryTooManyTimeError as exc:
                self._logger.error(
                    sn,
                    '更新狀態',
                    f'抓取失敗: {exc}',
                    display=False,
                )
                continue

            mode = info.get('mode') or settings.default_download_mode
            owner_id: str | None = info.get('owner_id') or None

            # Cache the series name on the list entry so the UI can show it
            # before any episode finishes downloading.
            if owner_id:
                self._anime_list_entry_repo.update_anime_name(
                    sn=int(sn),
                    user_id=owner_id,
                    anime_name=metadata.bangumi_name,
                )

            # Build inverse lookup once per metadata fetch so we can resolve
            # target_sn → episode label without a second fetch inside the loop.
            sn_to_episode: dict[int, str] = {ep_sn: ep for ep, ep_sn in metadata.episode_list.items()}

            target_sns = self._select_target_sns(int(sn), mode, metadata.episode_list)

            for target_sn in target_sns:
                target_episode = sn_to_episode.get(target_sn)
                existing = self._anime_repo.read(target_sn)
                needs_download = (
                    existing is None or existing.status == 0 or (upload_to_server and existing.remote_status == 0)
                )
                if not needs_download:
                    continue
                if self._queue.contains(target_sn):
                    continue

                self._queue.add(
                    target_sn,
                    self._make_task_info(target_sn, info, mode),
                )
                newly_added += 1
                self._logger.info(
                    target_sn,
                    '偵測新集數',
                    f'{metadata.bangumi_name} EP{target_episode or "?"} → 加入下載佇列',
                    display=False,
                )
                self._announce_waiting(
                    target_sn,
                    bangumi_name=metadata.bangumi_name,
                    episode=target_episode,
                    owner_id=owner_id,
                )
                self._spawn_worker(target_sn)

            # (D) Beat the watchdog after each sn so the heartbeat stays fresh
            # even when check_tasks runs longer than 60 s on large scan lists.
            if self._watchdog is not None:
                self._watchdog.beat()

            # Apply parse_cd cooldown after this sn's work, except after
            # the last item — no benefit in sleeping when there's nothing next.
            is_last = idx == len(sn_items) - 1
            if not is_last and self._parse_cooldown is not None:
                self._parse_cooldown.wait()

        self._logger.info(
            None,
            '更新資訊',
            f'本次更新添加了 {newly_added} 個新任務, 目前佇列中共有 {self._queue.size()} 個任務',
            display=False,
        )

    def run_forever(self) -> None:
        """Main loop. Returns only if :meth:`stop` is called."""
        while not self._stop_event.is_set():
            if self._watchdog is not None:
                self._watchdog.beat()
            try:
                settings = self._settings_repo.load()
                sn_dict = self._load_sn_dict_from_db(settings.default_download_mode)
                self.check_tasks(sn_dict)
            except Exception as exc:  # noqa: BLE001 — loop must never die
                self._logger.error(
                    None,
                    '更新循環',
                    f'一輪更新失敗: {exc}',
                    display=False,
                )

            sleep_seconds = int(settings.check_frequency) * 60
            # Poll in 1-second ticks so stop() is responsive, and beat the
            # watchdog every tick so the health endpoint doesn't report the
            # scheduler as degraded while the loop is idle between checks.
            for _ in range(max(1, sleep_seconds)):
                if self._stop_event.is_set():
                    return
                if self._watchdog is not None:
                    self._watchdog.beat()
                self._sleep(1)

    # ------------------------------------------------------------------ helpers

    def _load_sn_dict_from_db(self, default_download_mode: str = 'latest') -> dict[int, dict[str, str]]:
        """Load the scan list from the ``anime_list_entries`` DB table.

        Returns the same ``{sn: {mode, tag, season, owner_id}}`` shape that
        :meth:`check_tasks` expects.  Disabled entries are skipped.

        If the DB is empty and the legacy ``sn_list.txt`` has entries, emits a
        one-time warning (per scheduler boot) suggesting the user re-add their
        entries via the frontend.
        """
        rows = self._anime_list_entry_repo.list_all()
        result: dict[int, dict[str, str]] = {}
        for row in rows:
            if not row.enabled:
                continue
            result[int(row.sn)] = {
                'mode': row.mode or default_download_mode,
                'tag': row.tag or '',
                'season': str(row.season),
                'owner_id': row.user_id or '',
                'custom_name': row.custom_name or '',
            }

        if not result and not self._legacy_warning_emitted:
            # Check if legacy file has entries to surface a migration hint.
            settings = self._settings_repo.load()
            legacy_dict = self._sn_list_repo.parse_legacy(settings.default_download_mode)
            if legacy_dict:
                self._logger.error(
                    None,
                    '追番清單',
                    f'追番清單 DB 目前沒有項目，但偵測到 legacy sn_list.txt'
                    f' 有 {len(legacy_dict)} 筆 — 請在前端追番清單頁面重新加入。',
                    display=False,
                )
            self._legacy_warning_emitted = True

        return result

    def _select_target_sns(
        self,
        root_sn: int,
        mode: str,
        episode_list: dict[str, int],
    ) -> list[int]:
        """Return the list of sns to enqueue for this root sn + mode."""
        episode_sns = list(episode_list.values())
        if not episode_sns:
            return [root_sn]

        if mode == 'all':
            episode_sns.sort()
            return episode_sns
        if mode == 'largest-sn':
            episode_sns.sort()
            return [episode_sns[-1]]
        if mode == 'single':
            return [root_sn]
        # Default ("latest"): the episode rendered furthest-right on the
        # web page, which is the LAST item in insertion order of the dict.
        return [episode_sns[-1]]

    def _make_task_info(
        self,
        sn: int,
        info: dict[str, str],
        mode: str,
    ) -> TaskInfo:
        from .queue_ import TaskInfo

        raw_custom = info.get('custom_name') or ''
        return TaskInfo(
            sn=int(sn),
            tag=info.get('tag', ''),
            mode=mode,
            season=int(info.get('season') or '1'),
            custom_name=raw_custom or None,
        )

    def _announce_waiting(
        self,
        sn: int,
        *,
        bangumi_name: str,
        episode: str | None = None,
        owner_id: str | None = None,
    ) -> None:
        """Seed a ``'等待下載'`` entry on the progress bus.

        Called at queue-ingress so users see queue depth before a worker
        picks the task up. The worker's first ``update_status`` call
        (``'正在解析'``) overwrites this in-place — no ``start()`` collision.

        ``episode`` is the episode label (e.g. ``"1"``) resolved from the
        metadata ``episode_list`` inverse lookup so the card is complete
        immediately — before the Anime worker thread loads its own metadata.

        ``owner_id`` is forwarded to :meth:`~app.downloader.progress.ProgressBus.start`
        so the RBAC layer can filter the entry by the user who added the sn.
        """
        if self._progress_bus is None:
            return
        # Best-effort preview — the Anime orchestrator sets the real title
        # once metadata is loaded on the worker thread.
        existing = self._anime_repo.read(int(sn))
        preview_source = (existing.anime_name if existing is not None else '') or bangumi_name or str(sn)
        preview = f'《{preview_source}》'
        self._progress_bus.start(
            int(sn),
            preview,
            status='等待下載',
            bangumi_name=bangumi_name,
            episode=episode,
            owner_id=owner_id,
        )

    def _spawn_worker(self, sn: int) -> None:
        if self._queue.is_processing(sn):
            return
        self._queue.mark_processing(sn)
        thread = threading.Thread(
            target=self._worker.run,
            args=(sn,),
            daemon=True,
        )
        thread.start()

    # ---- test hooks ---------------------------------------------------

    def _set_sleep(self, fn: object) -> None:
        self._sleep = fn  # type: ignore[assignment]
