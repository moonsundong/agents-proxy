"""LLM 模型配置表:对应文档模块 3(模型配置管理)。"""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.config.database import Base


class ModelType(StrEnum):
    """模型后端类型。"""

    LOCAL = "local"  # 本地 llama-server
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OPENAI_COMPATIBLE = "openai_compatible"  # 其他 OpenAI 兼容服务


class HealthStatus(StrEnum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"


class LLMModel(Base):
    __tablename__ = "llm_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False, default=ModelType.LOCAL)
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    api_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    model_id: Mapped[str] = mapped_column(String(256), nullable=False)

    context_window: Mapped[int] = mapped_column(Integer, default=8192)
    temperature: Mapped[float] = mapped_column(Float, default=0.7)

    priority: Mapped[int] = mapped_column(Integer, default=0)  # 数值越小优先级越高
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    health_status: Mapped[str] = mapped_column(String(16), default=HealthStatus.UNKNOWN)
    last_health_check: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
