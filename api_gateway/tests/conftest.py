"""测试配置:隔离测试数据库,严禁触碰开发库 gateway.db。

测试夹具会 drop_all/create_all,历史上曾直接作用在开发库上导致真实数据被清空。
因此在导入任何 app 模块之前,先把数据库 URL 指向独立的测试库文件
(settings 有 lru_cache,导入即定型,必须在最前面设置)。
"""

import os
from pathlib import Path

os.environ["GATEWAY_DATABASE_URL"] = "sqlite:///./test_gateway.db"

import pytest_asyncio

from app.config.database import engine

_TEST_DB = Path("test_gateway.db")


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _dispose_engine():
    """测试会话结束时释放数据库引擎并删除测试库文件。"""
    yield
    await engine.dispose()
    _TEST_DB.unlink(missing_ok=True)
