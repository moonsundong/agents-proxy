"""压缩策略配置 API 测试。"""

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.config.database import Base, engine
from app.main import app
from app.services import compression_service


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    compression_service.invalidate_cache()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    compression_service.invalidate_cache()


async def test_get_config_auto_creates_default(client: AsyncClient) -> None:
    resp = await client.get("/api/compression/config")
    assert resp.status_code == 200
    cfg = resp.json()
    assert cfg["id"] == 1
    assert cfg["mode"] == "on"
    assert cfg["protect_recent"] == 4
    assert cfg["min_tokens_to_compress"] == 250
    assert cfg["kompress_model"] is None  # 默认启用 ML 压缩


async def test_update_config_partial(client: AsyncClient) -> None:
    resp = await client.put(
        "/api/compression/config",
        json={"mode": "tools_only", "target_ratio": 0.5, "kompress_model": "disabled"},
    )
    assert resp.status_code == 200
    cfg = resp.json()
    assert cfg["mode"] == "tools_only"
    assert cfg["target_ratio"] == 0.5
    assert cfg["kompress_model"] == "disabled"
    assert cfg["protect_recent"] == 4  # 未更新字段保持默认

    # 再次读取应持久化
    resp = await client.get("/api/compression/config")
    assert resp.json()["mode"] == "tools_only"


async def test_update_config_validation(client: AsyncClient) -> None:
    resp = await client.put("/api/compression/config", json={"mode": "bogus"})
    assert resp.status_code == 422

    resp = await client.put("/api/compression/config", json={"target_ratio": 1.5})
    assert resp.status_code == 422
