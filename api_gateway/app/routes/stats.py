"""统计与监控接口:/api/stats(文档 6.3 节)。"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.schemas.stats import LogPage, SavingsTrend, StatsOverview
from app.services import stats_service

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/overview", response_model=StatsOverview)
async def get_overview(db: AsyncSession = Depends(get_db)) -> StatsOverview:
    """统计概览:今日请求数、节省 Token 数、路由分布等。"""
    return await stats_service.overview(db)


@router.get("/logs", response_model=LogPage)
async def get_logs(
    db: AsyncSession = Depends(get_db),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    route: str | None = Query(default=None, pattern="^(local|cloud|manual)$"),
    status: str | None = Query(default=None, pattern="^(success|error)$"),
    model_name: str | None = None,
    days: int | None = Query(default=None, ge=1, le=90),
) -> LogPage:
    """请求日志列表:分页 + 路由/状态/模型/时间范围筛选。"""
    return await stats_service.query_logs(
        db,
        page=page,
        page_size=page_size,
        route=route,
        status=status,
        model_name=model_name,
        days=days,
    )


@router.get("/savings", response_model=SavingsTrend)
async def get_savings(
    db: AsyncSession = Depends(get_db),
    days: int = Query(default=7, ge=1, le=90),
) -> SavingsTrend:
    """Token 节省趋势图数据(按天聚合,无请求的日期补零)。"""
    return await stats_service.savings_trend(db, days)
