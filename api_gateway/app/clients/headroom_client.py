"""Headroom 压缩客户端封装。

设计要点(对应文档模块 4):
- import 守卫:headroom-ai 未安装时 HEADROOM_AVAILABLE=False,自动不压缩直通;
- 同步 SDK 用 asyncio.to_thread 执行,避免阻塞事件循环;
- 任何压缩异常都降级为直通(不压缩直接转发),不影响服务可用性;
- HF_HOME 环境变量必须在 headroom 首次导入前设置。
"""

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from app.config.settings import settings
from app.models.compression_config import CompressionConfig, CompressionMode

if settings.hf_home:
    os.environ.setdefault("HF_HOME", settings.hf_home)

try:
    from headroom import CompressConfig, compress

    HEADROOM_AVAILABLE = True
except Exception as exc:  # noqa: BLE001  # 可选依赖:任何导入/加载失败都应降级
    CompressConfig = None  # type: ignore[assignment, misc]
    compress = None  # type: ignore[assignment]
    HEADROOM_AVAILABLE = False
    logger.warning("Headroom 不可用,压缩功能自动降级为直通: {}", exc)


@dataclass
class CompressOutcome:
    """一次压缩调用的结果(成功、跳过或降级都归一到这个结构)。"""

    messages: list[dict[str, Any]]
    compressed: bool = False  # 实际执行了压缩
    degraded: bool = False  # 压缩失败,已降级直通
    original_tokens: int = 0
    compressed_tokens: int = 0
    transforms: list[str] = field(default_factory=list)
    error: str | None = None


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """粗略 token 估算(每 4 字符约 1 token),用于未压缩时的日志记录。"""
    total = 0
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):  # 多模态分片
            total += sum(len(str(part)) for part in content)
    return total // 4


def _build_compress_config(cfg: CompressionConfig) -> Any:
    """数据库配置 → Headroom CompressConfig。"""
    kwargs: dict[str, Any] = {
        "protect_recent": cfg.protect_recent,
        "target_ratio": cfg.target_ratio,
        "min_tokens_to_compress": cfg.min_tokens_to_compress,
    }
    if cfg.mode == CompressionMode.TOOLS_ONLY:
        # 仅压缩工具输出:用户/系统消息都不动
        kwargs["compress_user_messages"] = False
        kwargs["compress_system_messages"] = False
    else:
        kwargs["compress_user_messages"] = cfg.compress_user_messages
        kwargs["compress_system_messages"] = cfg.compress_system_messages
    if cfg.kompress_model:
        kwargs["kompress_model"] = cfg.kompress_model
    return CompressConfig(**kwargs)


async def compress_messages(
    messages: list[dict[str, Any]],
    cfg: CompressionConfig,
    model_limit: int,
) -> CompressOutcome:
    """按策略压缩消息列表;任何失败都降级为直通。"""
    if cfg.mode == CompressionMode.OFF:
        tokens = estimate_tokens(messages)
        return CompressOutcome(messages=messages, original_tokens=tokens, compressed_tokens=tokens)

    if not HEADROOM_AVAILABLE:
        tokens = estimate_tokens(messages)
        return CompressOutcome(
            messages=messages,
            degraded=True,
            original_tokens=tokens,
            compressed_tokens=tokens,
            error="headroom 不可用",
        )

    try:
        result = await asyncio.to_thread(
            compress,
            messages,
            model_limit=model_limit,
            config=_build_compress_config(cfg),
        )
        return CompressOutcome(
            messages=result.messages,
            compressed=True,
            original_tokens=result.tokens_before,
            compressed_tokens=result.tokens_after,
            transforms=list(result.transforms_applied),
        )
    except Exception as exc:  # noqa: BLE001  # 需求:压缩失败一律降级直通
        logger.warning("Headroom 压缩失败,降级为直通: {}", exc)
        tokens = estimate_tokens(messages)
        return CompressOutcome(
            messages=messages,
            degraded=True,
            original_tokens=tokens,
            compressed_tokens=tokens,
            error=str(exc),
        )
