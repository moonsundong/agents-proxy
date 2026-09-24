"""转发引擎测试:MockTransport 模拟上游,进程内 ASGI 测试(不起真实服务)。"""

import asyncio
import json
from collections.abc import AsyncGenerator
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.clients import headroom_client, llm_client
from app.config.database import Base, async_session_factory, engine
from app.config.settings import settings
from app.main import app
from app.models.request_log import RequestLog
from app.services import compression_service, proxy_service


@pytest_asyncio.fixture
async def client(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[AsyncClient, None]:
    """重建表 + 注入 Mock 上游 + 假的 headroom 压缩,避免触发真实模型下载。"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    proxy_service.reset_breakers()
    compression_service.invalidate_cache()
    monkeypatch.setattr(settings, "llm_retry_backoff", 0.001)

    # 假压缩:替换消息并报告 token 节省,验证压缩结果被真正发往上游
    def fake_compress(messages, **_kwargs):
        return SimpleNamespace(
            messages=[{"role": "user", "content": "[compressed]"}],
            tokens_before=1000,
            tokens_after=400,
            tokens_saved=600,
            compression_ratio=0.6,
            transforms_applied=["SmartCrusher"],
        )

    monkeypatch.setattr(headroom_client, "compress", fake_compress)
    monkeypatch.setattr(headroom_client, "HEADROOM_AVAILABLE", True)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    llm_client.set_client(None)
    compression_service.invalidate_cache()


def _mock_upstream(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _ok_completion(content: str = "hello", usage: dict | None = None) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": usage or {"prompt_tokens": 10, "completion_tokens": 5},
        },
    )


_SSE_BODY = (
    'data: {"id":"x","choices":[{"delta":{"content":"Hel"}}]}\n\n'
    'data: {"id":"x","choices":[{"delta":{"content":"lo"}}]}\n\n'
    'data: {"id":"x","choices":[],"usage":{"prompt_tokens":12,"completion_tokens":7}}\n\n'
    "data: [DONE]\n\n"
)


async def _seed_model(client: AsyncClient, **overrides) -> dict:
    payload = {
        "name": "local-bonsai",
        "type": "local",
        "base_url": "http://upstream.test",
        "model_id": "bonsai-27b",
        "context_window": 65536,
        "is_default": True,
    }
    payload.update(overrides)
    resp = await client.post("/api/models", json=payload)
    assert resp.status_code == 201
    return resp.json()


async def _logs() -> list[RequestLog]:
    """读取日志;流式路径的日志是后台任务写入,先排干再读,避免竞态。"""
    from app.services import proxy_service

    for _ in range(20):
        tasks = [t for t in proxy_service._BACKGROUND_TASKS if not t.done()]
        if not tasks:
            break
        await asyncio.gather(*tasks, return_exceptions=True)
    async with async_session_factory() as session:
        result = await session.execute(select(RequestLog))
        return list(result.scalars().all())


_MSG = {"messages": [{"role": "user", "content": "你好"}]}


# ---------------------------------------------------------------- 非流式


async def test_proxy_non_stream_success(client: AsyncClient) -> None:
    received: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        received.append(json.loads(request.content))
        return _ok_completion()

    llm_client.set_client(_mock_upstream(handler))
    await _seed_model(client)

    resp = await client.post("/v1/chat/completions", json=_MSG)
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "hello"

    # 上游收到的是替换后的模型 ID 和压缩后的消息
    assert received[0]["model"] == "bonsai-27b"
    assert received[0]["messages"] == [{"role": "user", "content": "[compressed]"}]

    logs = await _logs()
    assert len(logs) == 1
    log = logs[0]
    assert log.status == "success"
    assert log.route == "local"
    assert log.original_tokens == 1000
    assert log.compressed_tokens == 400
    assert log.prompt_tokens == 10
    assert log.completion_tokens == 5
    assert log.latency_ms is not None


async def test_proxy_model_name_matching_and_fallback(client: AsyncClient) -> None:
    llm_client.set_client(_mock_upstream(lambda r: _ok_completion()))
    await _seed_model(client)
    await _seed_model(client, name="gpt-4o", type="openai", model_id="gpt-4o", is_default=False)

    # 按名称匹配到非默认模型(未配置策略时按名选择)
    resp = await client.post("/v1/chat/completions", json={**_MSG, "model": "gpt-4o"})
    assert resp.status_code == 200
    logs = await _logs()
    assert logs[-1].model_name == "gpt-4o"
    assert logs[-1].route == "cloud"
    assert "按模型名匹配" in logs[-1].route_reason

    # 未匹配的模型名回退默认模型
    resp = await client.post(
        "/v1/chat/completions", json={**_MSG, "model": "no-such-model"}
    )
    assert resp.status_code == 200
    logs = await _logs()
    assert logs[-1].model_name == "local-bonsai"
    assert "兜底" in logs[-1].route_reason


async def test_proxy_no_available_model(client: AsyncClient) -> None:
    resp = await client.post("/v1/chat/completions", json=_MSG)
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "NO_AVAILABLE_MODEL"


async def test_proxy_anthropic_type_rejected(client: AsyncClient) -> None:
    await _seed_model(client, type="anthropic")
    resp = await client.post("/v1/chat/completions", json=_MSG)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "UNSUPPORTED_MODEL_TYPE"


# ---------------------------------------------------------------- 重试与熔断


async def test_proxy_retry_then_success(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "llm_max_retries", 2)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(503, json={"error": {"message": "busy"}})
        return _ok_completion()

    llm_client.set_client(_mock_upstream(handler))
    await _seed_model(client)

    resp = await client.post("/v1/chat/completions", json=_MSG)
    assert resp.status_code == 200
    assert calls == 3  # 两次失败后第三次成功


async def test_proxy_upstream_unreachable(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "llm_max_retries", 1)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("connection refused", request=request)

    llm_client.set_client(_mock_upstream(handler))
    await _seed_model(client)

    resp = await client.post("/v1/chat/completions", json=_MSG)
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "UPSTREAM_UNAVAILABLE"
    assert calls == 2  # 1 次原始 + 1 次重试
    logs = await _logs()
    assert logs[0].status == "error"


async def test_proxy_circuit_breaker_opens(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "llm_max_retries", 0)
    monkeypatch.setattr(settings, "circuit_failure_threshold", 3)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500, json={"error": {"message": "boom"}})

    llm_client.set_client(_mock_upstream(handler))
    await _seed_model(client)

    for _ in range(3):
        resp = await client.post("/v1/chat/completions", json=_MSG)
        assert resp.status_code == 500
    assert calls == 3

    # 第 4 次请求被熔断器直接拒绝,不再打到上游
    resp = await client.post("/v1/chat/completions", json=_MSG)
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "CIRCUIT_OPEN"
    assert calls == 3


async def test_proxy_upstream_4xx_passthrough_no_retry(client: AsyncClient) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(400, json={"error": {"message": "bad request"}})

    llm_client.set_client(_mock_upstream(handler))
    await _seed_model(client)

    resp = await client.post("/v1/chat/completions", json=_MSG)
    assert resp.status_code == 400
    assert resp.json()["error"]["message"] == "bad request"
    assert calls == 1  # 4xx 不重试


# ---------------------------------------------------------------- 流式


async def test_proxy_stream_passthrough(client: AsyncClient) -> None:
    received: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        received.append(json.loads(request.content))
        return httpx.Response(
            200, content=_SSE_BODY.encode(), headers={"content-type": "text/event-stream"}
        )

    llm_client.set_client(_mock_upstream(handler))
    await _seed_model(client)

    async with client.stream("POST", "/v1/chat/completions", json={**_MSG, "stream": True}) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = (await resp.aread()).decode()

    # 上游 SSE 原样透传,且自动注入了 stream_options
    assert "Hel" in body and "[DONE]" in body
    assert received[0]["stream_options"] == {"include_usage": True}

    logs = await _logs()
    assert logs[0].status == "success"
    assert logs[0].prompt_tokens == 12
    assert logs[0].completion_tokens == 7


async def test_proxy_stream_alias_endpoint(client: AsyncClient) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(
            200, content=_SSE_BODY.encode(), headers={"content-type": "text/event-stream"}
        )

    llm_client.set_client(_mock_upstream(handler))
    await _seed_model(client)

    resp = await client.post("/v1/chat/completions/stream", json=_MSG)
    assert resp.status_code == 200
    assert "Hel" in resp.text


async def test_proxy_stream_upstream_error(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "llm_max_retries", 0)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "rate limited"}})

    llm_client.set_client(_mock_upstream(handler))
    await _seed_model(client)

    resp = await client.post("/v1/chat/completions", json={**_MSG, "stream": True})
    assert resp.status_code == 200  # SSE 通道建立,错误以事件形式下发
    assert "rate limited" in resp.text
    assert "[DONE]" in resp.text
    logs = await _logs()
    assert logs[0].status == "error"


# ---------------------------------------------------------------- 压缩降级


async def test_proxy_compression_degraded_passthrough(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """压缩抛异常 → 降级直通,原始消息原样发往上游。"""
    received: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        received.append(json.loads(request.content))
        return _ok_completion()

    def boom(*_args, **_kwargs):
        raise RuntimeError("kompress model load failed")

    monkeypatch.setattr(headroom_client, "compress", boom)
    llm_client.set_client(_mock_upstream(handler))
    await _seed_model(client)

    resp = await client.post("/v1/chat/completions", json=_MSG)
    assert resp.status_code == 200
    assert received[0]["messages"] == _MSG["messages"]  # 原样直通
    logs = await _logs()
    assert logs[0].status == "success"
    assert logs[0].original_tokens == logs[0].compressed_tokens


async def test_proxy_compression_mode_off(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """mode=off 时不调用压缩,消息原样透传。"""
    compress_called = False

    def spy_compress(messages, **kwargs):
        nonlocal compress_called
        compress_called = True
        raise AssertionError("不应被调用")

    monkeypatch.setattr(headroom_client, "compress", spy_compress)
    llm_client.set_client(_mock_upstream(lambda r: _ok_completion()))
    await _seed_model(client)

    resp = await client.put("/api/compression/config", json={"mode": "off"})
    assert resp.status_code == 200

    resp = await client.post("/v1/chat/completions", json=_MSG)
    assert resp.status_code == 200
    assert compress_called is False
