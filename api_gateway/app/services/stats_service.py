"""统计服务:概览、日志分页查询、Token 节省趋势(文档 6.3 节)。

日期口径使用服务器本地时区(SQLite localtime 修饰符),与仪表盘展示一致。
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.request_log import RequestLog
from app.schemas.stats import DailySavings, LogPage, SavingsTrend, StatsOverview

# 本地时区的"今天",SQLite CURRENT_TIMESTAMP 存的是 UTC
_TODAY = func.date("now", "localtime")
# 日志行的本地日期
_LOG_DATE = func.date(RequestLog.created_at, "localtime")


def _saved_tokens_expr():
    """单行节省 token;任一侧为空时按 0 计。"""
    return func.coalesce(RequestLog.original_tokens, 0) - func.coalesce(
        RequestLog.compressed_tokens, 0
    )


async def overview(db: AsyncSession) -> StatsOverview:
    base = select(RequestLog)
    total_requests = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()

    today_filter = _LOG_DATE == _TODAY
    today_requests = (
        await db.execute(select(func.count()).select_from(RequestLog).where(today_filter))
    ).scalar_one()
    today_errors = (
        await db.execute(
            select(func.count())
            .select_from(RequestLog)
            .where(today_filter, RequestLog.status == "error")
        )
    ).scalar_one()
    avg_latency = (
        await db.execute(
            select(func.avg(RequestLog.latency_ms))
            .select_from(RequestLog)
            .where(today_filter, RequestLog.status == "success")
        )
    ).scalar_one()

    saved_today = (
        await db.execute(select(func.coalesce(func.sum(_saved_tokens_expr()), 0)).where(today_filter))
    ).scalar_one()
    saved_total = (
        await db.execute(select(func.coalesce(func.sum(_saved_tokens_expr()), 0)))
    ).scalar_one()

    route_rows = await db.execute(
        select(RequestLog.route, func.count())
        .where(today_filter, RequestLog.route.is_not(None))
        .group_by(RequestLog.route)
    )
    route_distribution = {route or "unknown": count for route, count in route_rows.all()}

    return StatsOverview(
        today_requests=today_requests,
        today_errors=today_errors,
        today_success_rate=(
            round((today_requests - today_errors) / today_requests, 4)
            if today_requests > 0
            else None
        ),
        today_avg_latency_ms=int(avg_latency) if avg_latency is not None else None,
        total_requests=total_requests,
        tokens_saved_today=int(saved_today),
        tokens_saved_total=int(saved_total),
        route_distribution=route_distribution,
    )


async def query_logs(
    db: AsyncSession,
    *,
    page: int,
    page_size: int,
    route: str | None = None,
    status: str | None = None,
    model_name: str | None = None,
    days: int | None = None,
) -> LogPage:
    stmt = select(RequestLog)
    if route:
        stmt = stmt.where(RequestLog.route == route)
    if status:
        stmt = stmt.where(RequestLog.status == status)
    if model_name:
        stmt = stmt.where(RequestLog.model_name == model_name)
    if days is not None:
        # 最近 N 天含今天:起始日期 = 今天 -(N-1) 天
        stmt = stmt.where(_LOG_DATE >= func.date("now", "localtime", f"-{days - 1} days"))

    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    items = list(
        (
            await db.execute(
                stmt.order_by(RequestLog.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return LogPage(total=total, page=page, page_size=page_size, items=items)


async def savings_trend(db: AsyncSession, days: int) -> SavingsTrend:
    """最近 N 天(含今天)按天聚合的 token 节省数据,无请求的日期补零。"""
    rows = (
        await db.execute(
            select(
                _LOG_DATE.label("day"),
                func.count().label("requests"),
                func.coalesce(func.sum(RequestLog.original_tokens), 0).label("original"),
                func.coalesce(func.sum(RequestLog.compressed_tokens), 0).label("compressed"),
            )
            .where(_LOG_DATE >= func.date("now", "localtime", f"-{days - 1} days"))
            .group_by(_LOG_DATE)
        )
    ).all()
    by_date = {row.day: row for row in rows}

    from datetime import UTC, datetime, timedelta

    today = datetime.now(UTC).astimezone().date()  # 服务器本地日期
    result: list[DailySavings] = []
    for i in range(days - 1, -1, -1):
        date = today - timedelta(days=i)
        key = date.isoformat()
        row = by_date.get(key)
        original = int(row.original) if row else 0
        compressed = int(row.compressed) if row else 0
        result.append(
            DailySavings(
                date=key,
                requests=int(row.requests) if row else 0,
                original_tokens=original,
                compressed_tokens=compressed,
                saved_tokens=original - compressed,
            )
        )
    return SavingsTrend(days=result)
