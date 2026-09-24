from fastapi import APIRouter

from app.routes import cc_switch, compression, health, models, proxy, routing, stats

api_router = APIRouter()
api_router.include_router(models.router)
api_router.include_router(health.router)
api_router.include_router(proxy.router)
api_router.include_router(compression.router)
api_router.include_router(routing.router)
api_router.include_router(stats.router)
api_router.include_router(cc_switch.router)
