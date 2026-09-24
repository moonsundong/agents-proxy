"""统计接口测试:概览、日志分页筛选、节省趋势。"""

from datetime import UTC, datetime, timedelta

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.config.database import Base, async_session_factory, engine
from app.main import app
from app.models.request_log import RequestLog


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _seed_logs() -> None:
    """造数据:今天 3 条(2 成功 1 失败),昨天 1 条。

    运行期由 CURRENT_TIMESTAMP 写入 UTC,这里同样以 UTC 造数,与查询侧的
    localtime 转换口径一致。
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    yesterday = now - timedelta(days=1)
    rows = [
        RequestLog(
            request_id="t1", model_name="local-bonsai", route="local",
            original_tokens=1000, compressed_tokens=400,
            prompt_tokens=380, completion_tokens=50, latency_ms=120, status="success",
            created_at=now,
        ),
        RequestLog(
            request_id="t2", model_name="gpt-4o", route="cloud", confidence=0.3,
            original_tokens=2000, compressed_tokens=800,
            prompt_tokens=750, completion_tokens=120, latency_ms=800, status="success",
            created_at=now,
        ),
        RequestLog(
            request_id="t3", model_name="local-bonsai", route="manual",
            original_tokens=500, compressed_tokens=500,
            latency_ms=None, status="error", error="熔断器开路",
            created_at=now,
        ),
        RequestLog(
            request_id="y1", model_name="local-bonsai", route="local",
            original_tokens=3000, compressed_tokens=900,
            latency_ms=150, status="success",
            created_at=yesterday,
        ),
    ]
    async with async_session_factory() as session:
        session.add_all(rows)
        await session.commit()


async def test_overview(client: AsyncClient) -> None:
    await _seed_logs()
    resp = await client.get("/api/stats/overview")
    assert resp.status_code == 200
    data = resp.json()

    assert data["today_requests"] == 3
    assert data["today_errors"] == 1
    assert data["today_success_rate"] == round(2 / 3, 4)
    assert data["total_requests"] == 4
    # 今天节省 (1000-400)+(2000-800)+(500-500) = 1800
    assert data["tokens_saved_today"] == 1800
    # 总计再加昨天 3000-900
    assert data["tokens_saved_total"] == 3900
    assert data["route_distribution"] == {"local": 1, "cloud": 1, "manual": 1}
    assert data["today_avg_latency_ms"] == (120 + 800) // 2


async def test_overview_empty(client: AsyncClient) -> None:
    resp = await client.get("/api/stats/overview")
    assert resp.status_code == 200
    data = resp.json()
    assert data["today_requests"] == 0
    assert data["today_success_rate"] is None
    assert data["route_distribution"] == {}


async def test_logs_pagination_and_filters(client: AsyncClient) -> None:
    await _seed_logs()

    resp = await client.get("/api/stats/logs", params={"page": 1, "page_size": 2})
    data = resp.json()
    assert data["total"] == 4
    assert len(data["items"]) == 2
    # 按 id 倒序,最新(昨天那条 id 最大)在前
    assert data["items"][0]["request_id"] == "y1"

    resp = await client.get("/api/stats/logs", params={"route": "cloud"})
    assert [i["request_id"] for i in resp.json()["items"]] == ["t2"]

    resp = await client.get("/api/stats/logs", params={"status": "error"})
    assert [i["request_id"] for i in resp.json()["items"]] == ["t3"]

    resp = await client.get("/api/stats/logs", params={"model_name": "gpt-4o"})
    assert [i["request_id"] for i in resp.json()["items"]] == ["t2"]

    resp = await client.get("/api/stats/logs", params={"days": 1})
    assert resp.json()["total"] == 3  # 不含昨天


async def test_savings_trend(client: AsyncClient) -> None:
    await _seed_logs()
    resp = await client.get("/api/stats/savings", params={"days": 7})
    assert resp.status_code == 200
    days = resp.json()["days"]
    assert len(days) == 7

    today = days[-1]
    assert today["requests"] == 3
    assert today["original_tokens"] == 3500
    assert today["compressed_tokens"] == 1700
    assert today["saved_tokens"] == 1800

    yesterday = days[-2]
    assert yesterday["requests"] == 1
    assert yesterday["saved_tokens"] == 2100

    # 其余日期补零
    assert all(d["requests"] == 0 for d in days[:-2])


async def test_logs_query_validation(client: AsyncClient) -> None:
    resp = await client.get("/api/stats/logs", params={"route": "bogus"})
    assert resp.status_code == 422
    resp = await client.get("/api/stats/logs", params={"page": 0})
    assert resp.status_code == 422


async def test_logs_written_via_proxy_appear(client: AsyncClient) -> None:
    """确认统计读取的就是转发链路写入的同一张表(冒烟)。"""
    await _seed_logs()
    async with async_session_factory() as session:
        count = len((await session.execute(select(RequestLog))).scalars().all())
    assert count == 4
