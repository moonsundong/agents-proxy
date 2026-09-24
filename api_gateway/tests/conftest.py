"""测试配置:隔离测试数据库,严禁触碰开发库 gateway.db。

测试夹具会 drop_all/create_all,历史上曾直接作用在开发库上导致真实数据被清空。
因此在导入任何 app 模块之前,先把数据库 URL 指向独立的测试库文件
(settings 有 lru_cache,导入即定型,必须在最前面设置)。
"""

import os
from pathlib import Path

os.environ["GATEWAY_DATABASE_URL"] = "sqlite:///./test_gateway.db"

import asyncio

import pytest_asyncio

from app.config.database import engine

_TEST_DB = Path("test_gateway.db")


@pytest_asyncio.fixture(autouse=True)
async def _drain_background_log_tasks():
    """每个测试结束后排干火记忘日志任务。

    流式路径的日志写入是后台任务;不排干的话,任务可能在下一个测试
    drop_all 之后才落库,造成跨测试污染(no such table / 数据串台)。
    """
    yield
    from app.services import proxy_service

    for _ in range(20):
        tasks = [t for t in proxy_service._BACKGROUND_TASKS if not t.done()]
        if not tasks:
            break
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _dispose_engine():
    """测试会话结束时释放数据库引擎并删除测试库文件。"""
    yield
    await engine.dispose()
    _TEST_DB.unlink(missing_ok=True)
