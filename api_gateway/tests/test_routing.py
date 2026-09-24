"""决策路由测试:决策模型评估、阈值路由、A/B 分流、降级兜底、策略 CRUD。"""

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
from app.main import app
from app.models.request_log import RequestLog
from app.services import compression_service, proxy_service


@pytest_asyncio.fixture
async def client(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[AsyncClient, None]:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    proxy_service.reset_breakers()
    compression_service.invalidate_cache()

    # 假压缩:原样返回消息,只报告 token 数,避免触发真实模型下载
    def fake_compress(messages, **_kwargs):
        return SimpleNamespace(
            messages=messages,
            tokens_before=800,
            tokens_after=300,
            tokens_saved=500,
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


def _completion(content: str) -> httpx.Response:
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
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        },
    )


class UpstreamSim:
    """按 host 分发到决策/本地/线上三个模拟后端,并记录调用次数。"""

    def __init__(self, decision_body: dict | None = None, decision_status: int = 200):
        self.decision_body = decision_body or {
            "confidence": 0.9,
            "complexity": "simple",
            "reason": "简单问答",
        }
        self.decision_status = decision_status
        self.calls = {"decision": 0, "local": 0, "cloud": 0}

    def handler(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "decision.test":
            self.calls["decision"] += 1
            return _completion(json.dumps(self.decision_body, ensure_ascii=False))
        if host == "local.test":
            self.calls["local"] += 1
            return _completion("local-answer")
        if host == "cloud.test":
            self.calls["cloud"] += 1
            return _completion("cloud-answer")
        raise AssertionError(f"未知上游: {host}")


async def _seed_models(client: AsyncClient) -> dict[str, int]:
    """创建决策/本地/线上三个模型,返回 name -> id。"""
    specs = [
        ("judge", "local", "http://decision.test", "judge-1", 8192),
        ("local-bonsai", "local", "http://local.test", "bonsai-27b", 8192),
        # 线上档给真实的大窗口:梯度模式下窗口护栏对所有档生效,
        # 线上档窗口太小时超大 prompt 会无档可落
        ("gpt-4o", "openai", "http://cloud.test", "gpt-4o", 1048576),
    ]
    ids: dict[str, int] = {}
    for name, type_, url, model_id, ctx in specs:
        resp = await client.post(
            "/api/models",
            json={
                "name": name,
                "type": type_,
                "base_url": url,
                "model_id": model_id,
                "context_window": ctx,
                "is_default": name == "local-bonsai",
            },
        )
        assert resp.status_code == 201
        ids[name] = resp.json()["id"]
    return ids


async def _create_policy(client: AsyncClient, ids: dict[str, int], **overrides) -> dict:
    payload = {
        "name": "默认策略",
        "scenario": "default",
        "decision_model_id": ids["judge"],
        # 两档梯度:≥0.7 走本地档,其余走线上档(等价于旧的 0.7 二元阈值)
        "decision_tiers": [
            {
                "min_confidence": 0.7,
                "model_id": ids.get("local-bonsai") or ids.get("t-local"),
            },
            {
                "min_confidence": 0.0,
                "model_id": ids.get("gpt-4o") or ids.get("t-mid3"),
            },
        ],
    }
    payload.update(overrides)
    resp = await client.post("/api/routing/policies", json=payload)
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


_MSG = {"messages": [{"role": "user", "content": "1+1 等于几?"}]}


# ---------------------------------------------------------------- 策略 CRUD


async def test_policy_crud(client: AsyncClient) -> None:
    ids = await _seed_models(client)
    policy = await _create_policy(client, ids)
    assert policy["decision_content_limit"] == 8000
    assert len(policy["decision_tiers"]) == 2

    resp = await client.get("/api/routing/policies")
    assert len(resp.json()) == 1

    resp = await client.put(
        f"/api/routing/policies/{policy['id']}", json={"decision_content_limit": 6000}
    )
    assert resp.status_code == 200
    assert resp.json()["decision_content_limit"] == 6000

    resp = await client.delete(f"/api/routing/policies/{policy['id']}")
    assert resp.status_code == 204
    resp = await client.get(f"/api/routing/policies/{policy['id']}")
    assert resp.status_code == 404


async def test_policy_duplicate_name_rejected(client: AsyncClient) -> None:
    ids = await _seed_models(client)
    await _create_policy(client, ids)
    resp = await client.post(
        "/api/routing/policies", json={"name": "默认策略", "scenario": "code"}
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------- 决策路由


async def test_high_confidence_routes_local(client: AsyncClient) -> None:
    sim = UpstreamSim({"confidence": 0.9, "complexity": "simple", "reason": "简单"})
    llm_client.set_client(httpx.AsyncClient(transport=httpx.MockTransport(sim.handler)))
    ids = await _seed_models(client)
    await _create_policy(client, ids)

    resp = await client.post("/v1/chat/completions", json=_MSG)
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "local-answer"
    assert sim.calls == {"decision": 1, "local": 1, "cloud": 0}

    logs = await _logs()
    assert logs[0].route == "local"
    assert logs[0].confidence == 0.9
    assert "≥" in logs[0].route_reason
    # 请求摘录:记录原始用户消息,便于对照路由调阈值
    assert logs[0].request_excerpt is not None
    assert "1+1" in logs[0].request_excerpt


async def test_low_confidence_routes_cloud(client: AsyncClient) -> None:
    sim = UpstreamSim({"confidence": 0.3, "complexity": "hard", "reason": "架构设计"})
    llm_client.set_client(httpx.AsyncClient(transport=httpx.MockTransport(sim.handler)))
    ids = await _seed_models(client)
    await _create_policy(client, ids)

    resp = await client.post("/v1/chat/completions", json=_MSG)
    assert resp.json()["choices"][0]["message"]["content"] == "cloud-answer"
    assert sim.calls == {"decision": 1, "local": 0, "cloud": 1}

    logs = await _logs()
    assert logs[0].route == "cloud"
    assert logs[0].confidence == 0.3
    assert "命中区间" in logs[0].route_reason


async def test_decision_receives_compressed_messages(client: AsyncClient) -> None:
    """决策模型评估的应是压缩后的消息(文档模块 5 决策流程)。"""
    seen: list[dict] = []
    sim = UpstreamSim()

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return sim.handler(request)

    llm_client.set_client(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    ids = await _seed_models(client)
    await _create_policy(client, ids)

    await client.post("/v1/chat/completions", json=_MSG)
    # fake_compress 原样返回消息,这里验证决策调用确实发生且携带消息内容
    decision_payload = seen[0]
    assert decision_payload["model"] == "judge-1"
    assert "1+1" in decision_payload["messages"][1]["content"]


async def test_matched_model_name_still_routes(client: AsyncClient) -> None:
    """所有请求一律走策略路由:即使模型名精确匹配,也不跳过决策。"""
    sim = UpstreamSim({"confidence": 0.3, "complexity": "hard", "reason": "难"})
    llm_client.set_client(httpx.AsyncClient(transport=httpx.MockTransport(sim.handler)))
    ids = await _seed_models(client)
    await _create_policy(client, ids)

    resp = await client.post("/v1/chat/completions", json={**_MSG, "model": "gpt-4o"})
    assert resp.json()["choices"][0]["message"]["content"] == "cloud-answer"
    assert sim.calls == {"decision": 1, "local": 0, "cloud": 1}  # 决策模型被调用

    logs = await _logs()
    assert logs[0].route == "cloud"  # 按置信度路由,不是 manual
    assert logs[0].confidence == 0.3
    assert "按策略路由" in logs[0].route_reason


async def test_unmatched_model_name_still_routes(client: AsyncClient) -> None:
    """CC Switch/Codex 场景:客户端总带上游模型名,未匹配时必须继续走策略路由。"""
    sim = UpstreamSim({"confidence": 0.9, "complexity": "simple", "reason": "简单"})
    llm_client.set_client(httpx.AsyncClient(transport=httpx.MockTransport(sim.handler)))
    ids = await _seed_models(client)
    await _create_policy(client, ids)

    resp = await client.post(
        "/v1/chat/completions", json={**_MSG, "model": "gpt-5-codex"}
    )
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "local-answer"
    assert sim.calls == {"decision": 1, "local": 1, "cloud": 0}  # 决策路由生效

    logs = await _logs()
    assert logs[0].route == "local"
    assert "gpt-5-codex 未匹配" in logs[0].route_reason


async def test_decision_failure_falls_back_to_cloud(client: AsyncClient) -> None:
    """决策模型返回垃圾 → 保守走最强档(最低区间)模型。"""
    sim = UpstreamSim(decision_body=None)
    sim.decision_body = None

    def bad_decision(request: httpx.Request) -> httpx.Response:
        if request.url.host == "decision.test":
            sim.calls["decision"] += 1
            return _completion("这不是 JSON")
        return sim.handler(request)

    llm_client.set_client(httpx.AsyncClient(transport=httpx.MockTransport(bad_decision)))
    ids = await _seed_models(client)
    await _create_policy(client, ids)

    resp = await client.post("/v1/chat/completions", json=_MSG)
    assert resp.json()["choices"][0]["message"]["content"] == "cloud-answer"
    logs = await _logs()
    assert logs[0].route == "cloud"
    assert "决策模型不可用" in logs[0].route_reason


async def test_decision_json_in_reasoning_content(client: AsyncClient) -> None:
    """推理型决策模型:content 为空、JSON 在 reasoning_content 里也要能解析。"""
    sim = UpstreamSim()

    def reasoning_decision(request: httpx.Request) -> httpx.Response:
        if request.url.host == "decision.test":
            sim.calls["decision"] += 1
            payload = json.loads(request.content)
            assert payload["max_tokens"] >= 1024  # 推理预算要够,否则正文被截断
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-1",
                    "object": "chat.completion",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "",
                                "reasoning_content": '思考一下…… {"confidence": 0.9, "complexity": "simple", "reason": "简单"}',
                            },
                            "finish_reason": "length",
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
                },
            )
        return sim.handler(request)

    llm_client.set_client(
        httpx.AsyncClient(transport=httpx.MockTransport(reasoning_decision))
    )
    ids = await _seed_models(client)
    await _create_policy(client, ids)

    resp = await client.post("/v1/chat/completions", json=_MSG)
    assert resp.json()["choices"][0]["message"]["content"] == "local-answer"
    logs = await _logs()
    assert logs[0].route == "local"
    assert logs[0].confidence == 0.9


async def _prepare_stream_ctx(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    """种子模型+策略,构造一个真实 prepare_stream 出来的 StreamContext。"""
    from app.config.database import async_session_factory
    from app.schemas.proxy import ChatCompletionRequest
    from app.services import proxy_service

    sim = UpstreamSim()
    llm_client.set_client(httpx.AsyncClient(transport=httpx.MockTransport(sim.handler)))
    ids = await _seed_models(client)
    await _create_policy(client, ids)
    req = ChatCompletionRequest(messages=[{"role": "user", "content": "hi"}], stream=True)
    async with async_session_factory() as db:
        return await proxy_service.prepare_stream(db, req)


async def test_stream_client_cancel_logged(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """客户端中断流式请求:必须以 cancelled 状态落日志(重连风暴可见)。"""
    import asyncio

    from app.services import proxy_service

    async def broken_stream(_model, _payload):
        yield b'data: {"choices":[]}\n\n'
        raise asyncio.CancelledError

    monkeypatch.setattr(llm_client, "chat_completion_stream", broken_stream)
    sc = await _prepare_stream_ctx(client, monkeypatch)

    gen = proxy_service.stream_generator(sc)
    await anext(gen)
    with pytest.raises(asyncio.CancelledError):
        await anext(gen)

    logs: list[RequestLog] = []
    for _ in range(40):  # 火记忘日志任务,等它写完
        await asyncio.sleep(0.05)
        logs = await _logs()
        if logs:
            break
    assert logs and logs[0].status == "cancelled"
    assert "客户端中断" in (logs[0].error or "")


async def test_stream_generator_close_logged(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """uvicorn 直接 aclose 生成器(GeneratorExit 路径)也要记 cancelled。

    这是 codex 等客户端断连时 Starlette 的实际行为,与 CancelledError 不同。
    """
    import asyncio

    from app.services import proxy_service

    async def hanging_stream(_model, _payload):
        yield b'data: {"choices":[]}\n\n'
        await asyncio.sleep(60)  # 上游迟迟不出新块
        yield b""

    monkeypatch.setattr(llm_client, "chat_completion_stream", hanging_stream)
    sc = await _prepare_stream_ctx(client, monkeypatch)

    gen = proxy_service.stream_generator(sc)
    await anext(gen)
    await gen.aclose()  # 模拟客户端断开后 uvicorn 关闭生成器

    logs: list[RequestLog] = []
    for _ in range(40):
        await asyncio.sleep(0.05)
        logs = await _logs()
        if logs:
            break
    assert logs and logs[0].status == "cancelled"


async def test_stream_close_after_finish_reason_is_success(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """客户端拿到 finish_reason 后提前关闭:响应已完整交付,应记 success。"""
    import asyncio

    from app.services import proxy_service

    async def stream_then_hang(_model, _payload):
        yield (
            b'data: {"choices":[{"delta":{"content":"done"},"index":0,'
            b'"finish_reason":"stop"}]}\n\n'
        )
        await asyncio.sleep(60)  # usage/[DONE] 还没发,客户端已满足并断开
        yield b""

    monkeypatch.setattr(llm_client, "chat_completion_stream", stream_then_hang)
    sc = await _prepare_stream_ctx(client, monkeypatch)

    gen = proxy_service.stream_generator(sc)
    await anext(gen)
    await gen.aclose()

    logs: list[RequestLog] = []
    for _ in range(40):
        await asyncio.sleep(0.05)
        logs = await _logs()
        if logs:
            break
    assert logs and logs[0].status == "success"
    assert "提前关闭" in (logs[0].error or "")


async def test_stream_keepalive_during_upstream_silence(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """上游长时间不出块时,向下游注入 SSE 注释心跳(防客户端空闲超时断连)。"""
    import asyncio

    from app.config.settings import settings
    from app.services import proxy_service

    async def slow_stream(_model, _payload):
        await asyncio.sleep(0.15)
        yield b'data: {"choices":[{"delta":{"content":"a"},"index":0}]}\n\n'
        yield b"data: [DONE]\n\n"

    monkeypatch.setattr(llm_client, "chat_completion_stream", slow_stream)
    monkeypatch.setattr(settings, "stream_keepalive_seconds", 0.05)
    sc = await _prepare_stream_ctx(client, monkeypatch)

    chunks = [chunk async for chunk in proxy_service.stream_generator(sc)]
    pings = [c for c in chunks if c.startswith(b": ")]
    assert pings, "上游静默期间应注入 SSE 心跳"
    assert chunks[-1].startswith(b"data: ")  # 数据内容不受影响


async def test_oversized_prompt_skips_local(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """压缩后仍超出本地档窗口:决策照常评估,本地档顺延,落到线上档。"""
    def big_compress(messages, **_kwargs):
        return SimpleNamespace(
            messages=messages,
            tokens_before=60000,
            tokens_after=50000,  # 远超本地模型默认的 8192 窗口
            tokens_saved=10000,
            compression_ratio=0.83,
            transforms_applied=["SmartCrusher"],
        )

    monkeypatch.setattr(headroom_client, "compress", big_compress)
    sim = UpstreamSim({"confidence": 0.99, "complexity": "simple", "reason": "简单"})
    llm_client.set_client(httpx.AsyncClient(transport=httpx.MockTransport(sim.handler)))
    ids = await _seed_models(client)
    await _create_policy(client, ids)

    resp = await client.post("/v1/chat/completions", json=_MSG)
    assert resp.json()["choices"][0]["message"]["content"] == "cloud-answer"
    # 置信度再高也不走本地(装不下就是装不下),但梯度模式仍需决策给评分
    assert sim.calls == {"decision": 1, "local": 0, "cloud": 1}

    logs = await _logs()
    assert logs[0].route == "cloud"
    assert "装不下" in logs[0].route_reason


async def test_decision_content_limit_follows_model_window(client: AsyncClient) -> None:
    """决策输入截断:小窗口本地模型按窗口安全上限截断(低于采样上限 8000)。"""
    seen: list[dict] = []
    sim = UpstreamSim()

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return sim.handler(request)

    llm_client.set_client(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    specs = [
        # 小窗口决策模型:安全上限 = (4096-1024-512)*1.5 = 3840 < 采样上限 8000
        ("judge", "local", "http://decision.test", "judge-1", 4096),
        ("local-bonsai", "local", "http://local.test", "bonsai-27b", 8192),
        ("gpt-4o", "openai", "http://cloud.test", "gpt-4o", 8192),
    ]
    ids: dict[str, int] = {}
    for name, type_, url, model_id, ctx in specs:
        resp = await client.post(
            "/api/models",
            json={
                "name": name,
                "type": type_,
                "base_url": url,
                "model_id": model_id,
                "context_window": ctx,
                "is_default": name == "local-bonsai",
            },
        )
        assert resp.status_code == 201
        ids[name] = resp.json()["id"]
    await _create_policy(client, ids)

    big_msg = {"messages": [{"role": "user", "content": "x" * 20000}]}
    await client.post("/v1/chat/completions", json=big_msg)

    decision_payload = seen[0]
    content = decision_payload["messages"][1]["content"]
    prefix = "请评估以下请求:\n"
    assert content.startswith(prefix)
    # 小窗口按窗口截断(3840),头尾采样另加一条"中间省略"标记(~30 字符)
    assert len(content) - len(prefix) <= 3840 + 64
    assert "中间省略" in content


async def test_decision_head_tail_sampling(client: AsyncClient) -> None:
    """本地决策模型(大窗口):超出采样上限时按头部 20% + 尾部 80% 截取。"""
    seen: list[dict] = []
    sim = UpstreamSim()

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return sim.handler(request)

    llm_client.set_client(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    specs = [
        # 大窗口本地决策模型:安全上限 ~194K ≫ 采样上限 8000,应按 8000 截断
        ("judge", "local", "http://decision.test", "judge-1", 131072),
        ("local-bonsai", "local", "http://local.test", "bonsai-27b", 8192),
        ("gpt-4o", "openai", "http://cloud.test", "gpt-4o", 8192),
    ]
    ids: dict[str, int] = {}
    for name, type_, url, model_id, ctx in specs:
        resp = await client.post(
            "/api/models",
            json={
                "name": name,
                "type": type_,
                "base_url": url,
                "model_id": model_id,
                "context_window": ctx,
                "is_default": name == "local-bonsai",
            },
        )
        assert resp.status_code == 201
        ids[name] = resp.json()["id"]
    await _create_policy(client, ids)  # 默认 limit=8000, head_ratio=0.2

    big_msg = {"messages": [{"role": "user", "content": "x" * 20000}]}
    await client.post("/v1/chat/completions", json=big_msg)

    serialized = json.dumps(big_msg["messages"], ensure_ascii=False)
    content = seen[0]["messages"][1]["content"]
    prefix = "请评估以下请求:\n"
    head, tail = int(8000 * 0.2), 8000 - int(8000 * 0.2)
    assert "中间省略" in content
    assert content.startswith(prefix + serialized[:head])
    assert content.endswith(serialized[-tail:])


async def test_decision_content_limit_configurable(client: AsyncClient) -> None:
    """策略里可下调本地决策模型的采样上限。"""
    seen: list[dict] = []
    sim = UpstreamSim()

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return sim.handler(request)

    llm_client.set_client(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    specs = [
        ("judge", "local", "http://decision.test", "judge-1", 131072),
        ("local-bonsai", "local", "http://local.test", "bonsai-27b", 8192),
        ("gpt-4o", "openai", "http://cloud.test", "gpt-4o", 8192),
    ]
    ids: dict[str, int] = {}
    for name, type_, url, model_id, ctx in specs:
        resp = await client.post(
            "/api/models",
            json={
                "name": name,
                "type": type_,
                "base_url": url,
                "model_id": model_id,
                "context_window": ctx,
                "is_default": name == "local-bonsai",
            },
        )
        assert resp.status_code == 201
        ids[name] = resp.json()["id"]
    await _create_policy(client, ids, decision_content_limit=5000)

    big_msg = {"messages": [{"role": "user", "content": "x" * 20000}]}
    await client.post("/v1/chat/completions", json=big_msg)

    content = seen[0]["messages"][1]["content"]
    prefix = "请评估以下请求:\n"
    # 截断后 = 头 1000 + 标记 + 尾 4000,标记很短,总量应明显小于 8000
    assert len(content) - len(prefix) < 5200
    assert "中间省略" in content


async def test_cloud_decision_uses_window_limit(client: AsyncClient) -> None:
    """线上决策模型:不受采样上限约束,按其 context_window 推导的安全上限放行。"""
    seen: list[dict] = []
    sim = UpstreamSim()

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return sim.handler(request)

    llm_client.set_client(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    specs = [
        # 线上决策模型:窗口 262144,安全上限 ~39 万字符,2 万字符不截断
        ("judge", "openai", "http://decision.test", "judge-1", 262144),
        ("local-bonsai", "local", "http://local.test", "bonsai-27b", 8192),
        ("gpt-4o", "openai", "http://cloud.test", "gpt-4o", 8192),
    ]
    ids: dict[str, int] = {}
    for name, type_, url, model_id, ctx in specs:
        resp = await client.post(
            "/api/models",
            json={
                "name": name,
                "type": type_,
                "base_url": url,
                "model_id": model_id,
                "context_window": ctx,
                "is_default": name == "local-bonsai",
            },
        )
        assert resp.status_code == 201
        ids[name] = resp.json()["id"]
    await _create_policy(client, ids)

    big_msg = {"messages": [{"role": "user", "content": "x" * 20000}]}
    await client.post("/v1/chat/completions", json=big_msg)

    serialized = json.dumps(big_msg["messages"], ensure_ascii=False)
    content = seen[0]["messages"][1]["content"]
    assert "中间省略" not in content
    assert serialized in content  # 全量放行,不截断


# ---------------------------------------------------------------- 梯度区间路由


class TierSim:
    """按 host 分发到决策 + 4 档执行模型的模拟上游。"""

    def __init__(self) -> None:
        self.decision_body: dict = {
            "confidence": 0.9,
            "complexity": "simple",
            "reason": "简单问答",
        }
        self.calls: dict[str, int] = {"decision": 0}

    def handler(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "decision.test":
            self.calls["decision"] += 1
            return _completion(json.dumps(self.decision_body, ensure_ascii=False))
        if host.startswith("t") and host.endswith(".test"):
            self.calls[host] = self.calls.get(host, 0) + 1
            return _completion(f"{host}-answer")
        raise AssertionError(f"未知上游: {host}")


async def _seed_tier_models(client: AsyncClient) -> dict[str, int]:
    """决策模型 + 4 档执行模型(t-local/t-mid1/t-mid2/t-mid3),返回 name -> id。"""
    specs = [
        ("judge", "local", "http://decision.test", "judge-1", 131072),
        ("t-local", "local", "http://tlocal.test", "m-local", 65536),
        ("t-mid1", "openai_compatible", "http://tmid1.test", "m-mid1", 262144),
        ("t-mid2", "openai", "http://tmid2.test", "m-mid2", 1048576),
        ("t-mid3", "openai", "http://tmid3.test", "m-mid3", 200000),
    ]
    ids: dict[str, int] = {}
    for name, type_, url, model_id, ctx in specs:
        resp = await client.post(
            "/api/models",
            json={
                "name": name,
                "type": type_,
                "base_url": url,
                "model_id": model_id,
                "context_window": ctx,
                "is_default": name == "t-mid3",
            },
        )
        assert resp.status_code == 201
        ids[name] = resp.json()["id"]
    return ids


def _tiers(ids: dict[str, int]) -> list[dict]:
    # 故意乱序提交,验证服务端按下限从高到低匹配
    return [
        {"min_confidence": 0.0, "model_id": ids["t-mid3"]},
        {"min_confidence": 0.8, "model_id": ids["t-local"]},
        {"min_confidence": 0.2, "model_id": ids["t-mid2"]},
        {"min_confidence": 0.5, "model_id": ids["t-mid1"]},
    ]


async def test_tiered_routing_by_confidence(client: AsyncClient) -> None:
    """梯度路由:不同置信度落到对应区间的模型。"""
    sim = TierSim()
    llm_client.set_client(httpx.AsyncClient(transport=httpx.MockTransport(sim.handler)))
    ids = await _seed_tier_models(client)
    await _create_policy(client, ids, decision_tiers=_tiers(ids))

    cases = [
        (0.9, "tlocal.test-answer", "≥0.80"),
        (0.6, "tmid1.test-answer", "≥0.50"),
        (0.3, "tmid2.test-answer", "≥0.20"),
        (0.1, "tmid3.test-answer", "≥0.00"),
    ]
    for confidence, expected, tier_label in cases:
        sim.decision_body = {"confidence": confidence, "reason": "r"}
        resp = await client.post("/v1/chat/completions", json=_MSG)
        assert resp.json()["choices"][0]["message"]["content"] == expected, confidence

    logs = await _logs()
    assert len(logs) == 4
    assert "命中区间" in logs[0].route_reason


async def test_tier_skipped_when_model_disabled(client: AsyncClient) -> None:
    """命中区间的模型已停用时,顺延到下一个区间。"""
    sim = TierSim()
    sim.decision_body = {"confidence": 0.6, "reason": "r"}
    llm_client.set_client(httpx.AsyncClient(transport=httpx.MockTransport(sim.handler)))
    ids = await _seed_tier_models(client)
    await _create_policy(client, ids, decision_tiers=_tiers(ids))
    # 停用 0.5 档的 t-mid1
    resp = await client.put(f"/api/models/{ids['t-mid1']}", json={"is_enabled": False})
    assert resp.status_code == 200

    resp = await client.post("/v1/chat/completions", json=_MSG)
    assert resp.json()["choices"][0]["message"]["content"] == "tmid2.test-answer"

    logs = await _logs()
    assert "已跳过" in logs[0].route_reason


async def test_tier_skipped_when_prompt_exceeds_window(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """梯度模式下上下文护栏逐档生效:装不下本地档仍调决策,顺延到线上档。"""
    sim = TierSim()
    sim.decision_body = {"confidence": 0.95, "reason": "r"}
    llm_client.set_client(httpx.AsyncClient(transport=httpx.MockTransport(sim.handler)))

    def big_compress(messages, **_kwargs):
        return SimpleNamespace(
            messages=messages,
            tokens_before=120000,
            tokens_after=100000,  # 超出 t-local 的 65536 窗口
            tokens_saved=20000,
            compression_ratio=0.83,
            transforms_applied=["SmartCrusher"],
        )

    monkeypatch.setattr(headroom_client, "compress", big_compress)
    ids = await _seed_tier_models(client)
    await _create_policy(client, ids, decision_tiers=_tiers(ids))

    resp = await client.post("/v1/chat/completions", json=_MSG)
    assert resp.json()["choices"][0]["message"]["content"] == "tmid1.test-answer"
    # 梯度模式仍需决策给出置信度,不能像二元模式那样直接短路
    assert sim.calls["decision"] == 1
    assert sim.calls.get("tlocal.test", 0) == 0

    logs = await _logs()
    assert "装不下" in logs[0].route_reason


async def test_no_policy_uses_default_model(client: AsyncClient) -> None:
    sim = UpstreamSim()
    llm_client.set_client(httpx.AsyncClient(transport=httpx.MockTransport(sim.handler)))
    await _seed_models(client)  # 不创建策略

    resp = await client.post("/v1/chat/completions", json=_MSG)
    assert resp.json()["choices"][0]["message"]["content"] == "local-answer"
    assert sim.calls["decision"] == 0

    logs = await _logs()
    assert logs[0].route == "local"
    assert "无命中路由策略" in logs[0].route_reason


async def test_scenario_falls_back_to_default_policy(client: AsyncClient) -> None:
    """请求指定了无专属策略的场景时,回退 default 场景策略。"""
    sim = UpstreamSim()
    llm_client.set_client(httpx.AsyncClient(transport=httpx.MockTransport(sim.handler)))
    ids = await _seed_models(client)
    await _create_policy(client, ids)  # scenario=default

    resp = await client.post("/v1/chat/completions", json={**_MSG, "scenario": "code"})
    assert resp.status_code == 200
    assert sim.calls["decision"] == 1  # 命中 default 策略,走了决策


async def test_scenario_not_forwarded_upstream(client: AsyncClient) -> None:
    """scenario 是网关私有扩展字段,不应发给上游模型。"""
    seen: list[dict] = []
    sim = UpstreamSim()

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return sim.handler(request)

    llm_client.set_client(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await _seed_models(client)

    await client.post("/v1/chat/completions", json={**_MSG, "scenario": "code"})
    for payload in seen:
        assert "scenario" not in payload


# ---------------------------------------------------------------- 回归


async def test_policy_routing_without_default_model(client: AsyncClient) -> None:
    """策略配置完整时,不设全局默认模型也必须能路由(默认模型仅兜底用)。"""
    sim = UpstreamSim({"confidence": 0.3, "complexity": "hard", "reason": "难"})
    llm_client.set_client(httpx.AsyncClient(transport=httpx.MockTransport(sim.handler)))
    specs = [
        ("judge", "local", "http://decision.test", "judge-1"),
        ("local-bonsai", "local", "http://local.test", "bonsai-27b"),
        ("gpt-4o", "openai", "http://cloud.test", "gpt-4o"),
    ]
    ids: dict[str, int] = {}
    for name, type_, url, model_id in specs:
        resp = await client.post(
            "/api/models",
            json={
                "name": name,
                "type": type_,
                "base_url": url,
                "model_id": model_id,
                "is_default": False,  # 关键:没有任何默认模型
            },
        )
        assert resp.status_code == 201
        ids[name] = resp.json()["id"]
    await _create_policy(client, ids)

    resp = await client.post("/v1/chat/completions", json=_MSG)
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "cloud-answer"
    assert sim.calls == {"decision": 1, "local": 0, "cloud": 1}


async def test_stream_prepare_error_returns_json(client: AsyncClient) -> None:
    """流式请求在准备阶段失败(如无可用模型)必须返回 JSON 错误,
    而不是 200 空流(客户端会报 'stream ended before finish_reason')。"""
    resp = await client.post(
        "/v1/chat/completions", json={**_MSG, "stream": True}
    )
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "NO_AVAILABLE_MODEL"


# ---------------------------------------------------- 决策输出解析(截断挽救)


async def test_parse_decision_truncated_json_salvaged() -> None:
    """推理型模型预算耗尽导致 JSON 截断(无右括号)时,正则打捞 confidence。"""
    from app.services.decision_service import _parse_decision

    info = _parse_decision('{"confidence": 0.45, "complexity": "medium", "reason": "请求包含多步')
    assert info is not None
    assert info.confidence == 0.45
    assert "打捞" in (info.reason or "")


async def test_parse_decision_complete_json_unchanged() -> None:
    """完整 JSON 仍走正常解析,且打捞逻辑不干扰。"""
    from app.services.decision_service import _parse_decision

    info = _parse_decision('前言 {"confidence": 0.8, "complexity": "simple", "reason": "问答"} 后记')
    assert info is not None
    assert info.confidence == 0.8
    assert info.reason == "问答"


async def test_parse_decision_unrecoverable_returns_none() -> None:
    """content 为空且无任何 confidence 字样时返回 None,由上层降级。"""
    from app.services.decision_service import _parse_decision

    assert _parse_decision("") is None
    assert _parse_decision("模型拒绝回答") is None
