"""CC Switch 端点管理 API 测试(CRUD + 导入/导出)。"""

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.config.database import Base, engine
from app.main import app


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _payload(**overrides) -> dict:
    base = {
        "name": "codex-desktop",
        "url": "http://127.0.0.1:8317",
        "api_key": None,
        "timeout": 30.0,
        "health_check_interval": 60,
        "is_enabled": True,
    }
    base.update(overrides)
    return base


async def test_endpoint_crud(client: AsyncClient) -> None:
    resp = await client.post("/api/cc-switch/endpoints", json=_payload())
    assert resp.status_code == 201
    endpoint_id = resp.json()["id"]

    resp = await client.get("/api/cc-switch/endpoints")
    assert len(resp.json()) == 1

    resp = await client.put(
        f"/api/cc-switch/endpoints/{endpoint_id}", json={"timeout": 45.0}
    )
    assert resp.status_code == 200
    assert resp.json()["timeout"] == 45.0

    resp = await client.delete(f"/api/cc-switch/endpoints/{endpoint_id}")
    assert resp.status_code == 204
    resp = await client.get(f"/api/cc-switch/endpoints/{endpoint_id}")
    assert resp.status_code == 404


async def test_duplicate_name_rejected(client: AsyncClient) -> None:
    assert (await client.post("/api/cc-switch/endpoints", json=_payload())).status_code == 201
    assert (await client.post("/api/cc-switch/endpoints", json=_payload())).status_code == 409


async def test_export_import_roundtrip(client: AsyncClient) -> None:
    await client.post("/api/cc-switch/endpoints", json=_payload())
    await client.post("/api/cc-switch/endpoints", json=_payload(name="second"))

    resp = await client.get("/api/cc-switch/endpoints/export")
    assert resp.status_code == 200
    exported = resp.json()
    assert len(exported) == 2
    assert {e["name"] for e in exported} == {"codex-desktop", "second"}
    # 导出不包含数据库内部字段
    assert "id" not in exported[0]

    # 导入:重名跳过,新名入库
    items = exported + [_payload(name="third")]
    resp = await client.post("/api/cc-switch/endpoints/import", json=items)
    assert resp.status_code == 200
    result = resp.json()
    assert result["imported"] == 1
    assert sorted(result["skipped"]) == ["codex-desktop", "second"]

    resp = await client.get("/api/cc-switch/endpoints")
    assert len(resp.json()) == 3


async def test_import_validation(client: AsyncClient) -> None:
    resp = await client.post("/api/cc-switch/endpoints/import", json=[{"name": "x"}])
    assert resp.status_code == 422  # 缺 url
