"""CC Switch 端点配置表:对应文档模块 1(CC Switch 配置管理)。"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.config.database import Base


class CCSwitchEndpoint(Base):
    __tablename__ = "cc_switch_endpoints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    api_key: Mapped[str | None] = mapped_column(String(512), nullable=True)

    timeout: Mapped[float] = mapped_column(Float, default=30.0)
    health_check_interval: Mapped[int] = mapped_column(Integer, default=60)  # 秒

    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
