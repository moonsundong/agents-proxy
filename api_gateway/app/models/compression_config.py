"""压缩策略配置表:对应文档模块 4(Headroom 内容压缩集成)。

全局单行配置(id 固定为 1),由第四阶段的压缩配置页面维护。
"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.config.database import Base


class CompressionMode(StrEnum):
    """压缩模式。"""

    ON = "on"  # 按策略压缩所有消息
    OFF = "off"  # 不压缩,直接透传
    TOOLS_ONLY = "tools_only"  # 仅压缩工具输出(role=tool 的消息)


class CompressionConfig(Base):
    __tablename__ = "compression_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    mode: Mapped[str] = mapped_column(String(16), default=CompressionMode.ON)

    # 映射 Headroom CompressConfig 的策略字段
    compress_user_messages: Mapped[bool] = mapped_column(Boolean, default=False)
    compress_system_messages: Mapped[bool] = mapped_column(Boolean, default=True)
    protect_recent: Mapped[int] = mapped_column(Integer, default=4)
    target_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_tokens_to_compress: Mapped[int] = mapped_column(Integer, default=250)

    # Kompress ML 模型:None = 默认启用(chopratejas/kompress-v2-base),
    # "disabled" = 跳过 ML 压缩,仅规则变换(SmartCrusher + CacheAligner)
    kompress_model: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # 关键信息保留标记(JSON 数组,如 ["FATAL", "Traceback"]),
    # 预留字段:Headroom SmartCrusher 默认保留错误/异常行,此字段供后续自定义策略使用
    keep_markers: Mapped[str | None] = mapped_column(Text, nullable=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
