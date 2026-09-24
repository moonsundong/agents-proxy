"""模型 CRUD API 的单元测试(内存 SQLite)。"""

import httpx
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.clients import llm_client
from app.config.database import engine
from app.main import app


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    async with engine.begin() as conn:
        from app.config.database import Base

        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    llm_client.set_client(None)


def _payload(**overrides) -> dict:
    base = {
        "name": "bonsai-27b",
        "type": "local",
        "base_url": "http://127.0.0.1:7070",
        "model_id": "bonsai-27b",
        "context_window": 65536,
        "temperature": 0.7,
    }
    base.update(overrides)
    return base


async def test_model_crud(client: AsyncClient) -> None:
    # 创建
    resp = await client.post("/api/models", json=_payload())
    assert resp.status_code == 201
    created = resp.json()
    assert created["name"] == "bonsai-27b"
    model_id = created["id"]

    # 列表
    resp = await client.get("/api/models")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    # 更新
    resp = await client.put(f"/api/models/{model_id}", json={"temperature": 0.3})
    assert resp.status_code == 200
    assert resp.json()["temperature"] == 0.3

    # 删除
    resp = await client.delete(f"/api/models/{model_id}")
    assert resp.status_code == 204
    resp = await client.get(f"/api/models/{model_id}")
    assert resp.status_code == 404


async def test_duplicate_name_rejected(client: AsyncClient) -> None:
    resp = await client.post("/api/models", json=_payload())
    assert resp.status_code == 201
    resp = await client.post("/api/models", json=_payload())
    assert resp.status_code == 409


async def test_only_one_default_model(client: AsyncClient) -> None:
    r1 = await client.post("/api/models", json=_payload(is_default=True))
    r2 = await client.post(
        "/api/models", json=_payload(name="gpt-4o", type="openai", is_default=True)
    )
    assert r1.status_code == r2.status_code == 201

    resp = await client.get("/api/models")
    defaults = [m for m in resp.json() if m["is_default"]]
    assert len(defaults) == 1
    assert defaults[0]["name"] == "gpt-4o"


async def test_health_endpoint(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_fetch_upstream_models(client: AsyncClient) -> None:
    """从上游 /v1/models 拉取模型 ID 列表,地址末尾 /v1 应被规范化。"""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"  # 不是 /v1/v1/models
        assert request.headers["authorization"] == "Bearer k"
        return httpx.Response(
            200,
            json={"data": [{"id": "model-b"}, {"id": "model-a"}, {"noid": 1}]},
        )

    llm_client.set_client(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    resp = await client.post(
        "/api/models/fetch-upstream",
        json={"base_url": "http://upstream.test/v1", "api_key": "k"},
    )
    assert resp.status_code == 200
    assert resp.json()["models"] == ["model-a", "model-b"]


async def test_fetch_upstream_models_anthropic_headers(client: AsyncClient) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "ak"
        assert request.headers["anthropic-version"] == "2023-06-01"
        return httpx.Response(200, json={"data": [{"id": "claude-x"}]})

    llm_client.set_client(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    resp = await client.post(
        "/api/models/fetch-upstream",
        json={"base_url": "http://upstream.test", "api_key": "ak", "type": "anthropic"},
    )
    assert resp.status_code == 200
    assert resp.json()["models"] == ["claude-x"]


async def test_fetch_upstream_models_unreachable(client: AsyncClient) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    llm_client.set_client(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    resp = await client.post(
        "/api/models/fetch-upstream", json={"base_url": "http://upstream.test"}
    )
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "UPSTREAM_UNAVAILABLE"
