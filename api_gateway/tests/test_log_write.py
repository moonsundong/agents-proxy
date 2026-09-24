"""日志写入的取消安全与并发回归测试(2026-09-24 日志黑洞事故)。

事故根因:客户端断连的取消恰好落在 write_log 的 commit 中途 →
aiosqlite 连接带未完结事务泄漏 → 写锁永不释放 → 之后所有日志写
全部 database is locked(请求正常但日志静默丢失)。
"""

import asyncio
import contextlib
import uuid
from collections.abc import AsyncGenerator
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import Base, async_session_factory, engine
from app.models.request_log import RequestLog
from app.services import proxy_service


def _ctx() -> proxy_service.LogContext:
    return proxy_service.LogContext(
        request_id=uuid.uuid4().hex[:12],
        model=SimpleNamespace(id=None, name="test-model"),
        route="cloud",
        confidence=0.5,
        route_reason="test",
        compression=SimpleNamespace(original_tokens=10, compressed_tokens=10),
        excerpt="hello",
        start=0.0,
    )


@pytest_asyncio.fixture
async def _db() -> AsyncGenerator[None, None]:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield


async def _row_count(request_id: str) -> int:
    async with async_session_factory() as session:
        return (
            await session.execute(
                select(func.count())
                .select_from(RequestLog)
                .where(RequestLog.request_id == request_id)
            )
        ).scalar_one()


async def test_write_log_survives_caller_cancellation(
    _db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """调用方在 commit 中途被取消:写入仍应在后台完成(shield + 独立任务)。"""
    started = asyncio.Event()
    release = asyncio.Event()
    real_commit = AsyncSession.commit

    async def slow_commit(self: AsyncSession) -> None:
        started.set()
        await release.wait()
        await real_commit(self)

    monkeypatch.setattr(AsyncSession, "commit", slow_commit)

    ctx = _ctx()
    task = asyncio.create_task(
        proxy_service.write_log(ctx, status="success", latency_ms=1)
    )
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    release.set()

    with contextlib.suppress(asyncio.CancelledError):
        await task

    # 调用方被取消,但屏蔽后的写入任务不受影响,最终必须落库
    for _ in range(100):
        if await _row_count(ctx.request_id):
            break
        await asyncio.sleep(0.02)
    assert await _row_count(ctx.request_id) == 1


async def test_write_log_concurrent_no_lock_error(_db: None) -> None:
    """并发写日志被串行化:不应互相制造 database is locked。"""
    ctxs = [_ctx() for _ in range(10)]
    await asyncio.gather(
        *(proxy_service.write_log(ctx, status="success", latency_ms=1) for ctx in ctxs)
    )
    for ctx in ctxs:
        assert await _row_count(ctx.request_id) == 1
