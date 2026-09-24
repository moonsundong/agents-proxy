"""压缩策略服务:全局配置的读取(带缓存)与更新。"""

import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients import headroom_client
from app.clients.headroom_client import CompressOutcome
from app.models.compression_config import CompressionConfig
from app.models.llm_model import LLMModel
from app.schemas.compression import CompressionConfigUpdate

# 配置热加载缓存:避免每个请求都查库,TTL 内直接使用缓存值
_cache: tuple[CompressionConfig, float] | None = None
_CACHE_TTL_SECONDS = 10.0


def invalidate_cache() -> None:
    global _cache
    _cache = None


async def get_config(db: AsyncSession) -> CompressionConfig:
    """读取全局压缩配置,不存在时创建默认行(单行, id=1)。"""
    global _cache
    if _cache is not None and time.monotonic() - _cache[1] < _CACHE_TTL_SECONDS:
        return _cache[0]

    cfg = await db.get(CompressionConfig, 1)
    if cfg is None:
        cfg = CompressionConfig(id=1)
        db.add(cfg)
        await db.commit()
        await db.refresh(cfg)
    # 缓存的是 ORM 对象,过期前不再查库;expire_on_commit=False 保证属性可用
    _cache = (cfg, time.monotonic())
    return cfg


async def update_config(
    db: AsyncSession, data: CompressionConfigUpdate
) -> CompressionConfig:
    result = await db.execute(select(CompressionConfig).where(CompressionConfig.id == 1))
    cfg = result.scalar_one_or_none()
    if cfg is None:
        cfg = CompressionConfig(id=1)
        db.add(cfg)
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(cfg, key, value)
    await db.commit()
    await db.refresh(cfg)
    invalidate_cache()
    return cfg


async def apply_compression(
    db: AsyncSession, messages: list[dict], target_model: LLMModel
) -> CompressOutcome:
    """按当前策略压缩消息,目标模型的上下文窗口作为 model_limit。"""
    cfg = await get_config(db)
    return await headroom_client.compress_messages(
        messages, cfg, model_limit=target_model.context_window
    )
