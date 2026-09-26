"""客户端来源(User-Agent → source 列)解析与落库测试。"""

import pytest
from sqlalchemy import select

from app.config.database import Base, async_session_factory, engine
from app.models.request_log import RequestLog
from app.services import proxy_service
from app.services.proxy_service import normalize_source


@pytest.mark.parametrize(
    ("ua", "expected"),
    [
        ("claude-cli/2.0.1 (external, cli)", "Claude Code"),
        ("codex_cli_rs/0.42.0", "Codex"),
        ("Mozilla/5.0 Cursor/1.2", "Cursor"),
        ("aider/0.60.1", "Aider"),
        ("openai-python/1.50.0", "OpenAI SDK"),
        ("python-requests/2.31.0", "Python Requests"),
        ("curl/8.5.0", "cURL"),
    ],
)
def test_normalize_source_known_tools(ua: str, expected: str) -> None:
    assert normalize_source(ua) == expected


def test_normalize_source_unknown_falls_back_to_truncated_ua() -> None:
    ua = "SomeUnknownTool/9.9 " + "x" * 200
    result = normalize_source(ua)
    assert result is not None
    assert result.startswith("SomeUnknownTool/9.9")
    assert len(result) <= 64


@pytest.mark.parametrize("ua", ["", "   ", None])
def test_normalize_source_empty(ua: str | None) -> None:
    assert normalize_source(ua) is None


@pytest.fixture
async def _db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield  # type: ignore[misc]


async def test_log_fields_carry_source(_db: None) -> None:
    """_log_fields 必须把 LogContext.source 写入 request_logs.source 列。"""
    import uuid
    from types import SimpleNamespace

    ctx = proxy_service.LogContext(
        request_id=uuid.uuid4().hex[:12],
        model=SimpleNamespace(id=None, name="test-model"),
        route="cloud",
        confidence=0.5,
        route_reason="test",
        compression=SimpleNamespace(original_tokens=10, compressed_tokens=10),
        excerpt="hello",
        start=0.0,
        source="Claude Code",
    )
    fields = proxy_service._log_fields(ctx, status="success", latency_ms=1)
    assert fields["source"] == "Claude Code"

    await proxy_service._insert_log(**fields)
    async with async_session_factory() as session:
        row = (
            await session.execute(
                select(RequestLog).where(RequestLog.request_id == ctx.request_id)
            )
        ).scalar_one()
    assert row.source == "Claude Code"
