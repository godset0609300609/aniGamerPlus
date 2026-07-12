"""Dramatiq actor registry.

Importing this package bootstraps the dramatiq broker (idempotent) so any
``app.tasks.*`` actor modules can decorate against the right instance.
The Dramatiq worker entry point (``dramatiq app.tasks ...``) imports this
package, which then imports each actor module to register it.
"""

from __future__ import annotations

from ..dramatiq_setup import init_broker

init_broker()

# Import each actor module so `dramatiq app.tasks ...` finds them all on
# worker startup.  Order doesn't matter — each module re-runs init_broker()
# (no-op) before its @actor decorator runs.
from ..services import telegram_health_monitor as _health  # noqa: F401, E402
from ..services import telegram_progress_publisher as _publisher  # noqa: F401, E402
from . import auto_scan as auto_scan  # noqa: F401, E402
from . import bilibili_download as bilibili_download  # noqa: F401, E402
from . import bt_feed_tick as bt_feed_tick  # noqa: F401, E402
from . import bt_landing_tick as bt_landing_tick  # noqa: F401, E402
from . import bt_remote_refresh_tick as bt_remote_refresh_tick  # noqa: F401, E402
from . import bt_retention_tick as bt_retention_tick  # noqa: F401, E402
from . import download as download  # noqa: F401, E402
from . import telegram as telegram  # noqa: F401, E402
from . import tg_backfill_tick as tg_backfill_tick  # noqa: F401, E402

__all__: list[str] = [
    'auto_scan',
    'bilibili_download',
    'bt_feed_tick',
    'bt_landing_tick',
    'bt_remote_refresh_tick',
    'bt_retention_tick',
    'download',
    'telegram',
    'tg_backfill_tick',
]
