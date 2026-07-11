import fastapi

from . import animelist_api, bt_api, config_api, health, logs_ws, progress_ws, snlist_api, tasks_api, tg_api

router = fastapi.APIRouter(prefix='/api')
router.include_router(health.router)
router.include_router(config_api.router)
router.include_router(snlist_api.router)
router.include_router(animelist_api.router)
router.include_router(tasks_api.router)
router.include_router(bt_api.router)
router.include_router(tg_api.router)
router.include_router(progress_ws.router)
router.include_router(logs_ws.router)

__all__ = ['router']
