"""Dramatiq async actor that runs one Bilibili download task."""

from __future__ import annotations

import asyncio

import dramatiq
import dramatiq.middleware
import dramatiq_abort

from .. import dramatiq_setup as _setup

_setup.init_broker()


@dramatiq.actor(
    queue_name='downloads',
    max_retries=0,
    time_limit=4 * 60 * 60 * 1000,
)
async def run_bilibili_download(
    task_sn: int,
    *,
    bvid: str = '',
    raw_input: str | None = None,
    resolution: str = '',
    classify: bool = True,
    owner_id: str | None = None,
) -> None:
    """Run one Bilibili download task to completion (or abort).

    ``raw_input`` (fix #20): when the caller couldn't cheaply resolve a
    b23.tv short link to a real bvid on the request path (that requires a
    synchronous HTTP redirect, up to 10s), it defers the whole
    ``parse_bilibili_input`` call — including that resolution — to this
    worker actor instead, passing the raw short link through ``raw_input``
    and leaving ``bvid`` empty. When ``raw_input`` is ``None``, ``bvid`` was
    already resolved by the caller (the common, network-free case).
    """
    from ..core import build_container
    from ..downloader.exceptions import TaskCancelledError

    container = build_container()

    resolved_bvid = bvid
    if raw_input is not None:
        from ..downloader.bilibili.url_parser import parse_bilibili_input

        try:
            resolved_bvid, _aid, _multi = await asyncio.to_thread(parse_bilibili_input, raw_input)
        except Exception as exc:  # noqa: BLE001 — unparseable / unreachable b23 link
            container.logger.error(
                None,
                'BilibiliRunner',
                f'Deferred b23.tv 連結解析失敗 (task_sn={task_sn}): {exc}',
                display=False,
            )
            return

    msg = dramatiq.middleware.CurrentMessage.get_current_message()
    message_id: str | None = msg.message_id if msg is not None else None

    if container.message_id_registry is not None and message_id is not None:
        await container.message_id_registry.set(int(task_sn), message_id)

    try:
        await asyncio.to_thread(
            container.bilibili_runner.run,
            int(task_sn),
            bvid=resolved_bvid,
            resolution=resolution,
            classify=classify,
            owner_id=owner_id,
        )
    except dramatiq_abort.Abort:
        raise TaskCancelledError() from None
    finally:
        if container.message_id_registry is not None:
            await container.message_id_registry.clear(int(task_sn))
