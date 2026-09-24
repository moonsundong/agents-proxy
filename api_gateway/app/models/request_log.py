"""请求日志表:全链路记录(耗时、Token 用量、压缩前后对比、路由决策)。"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.config.database import Base


class RequestLog(Base):
    __tablename__ = "request_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)

    model_id: Mapped[int | None] = mapped_column(ForeignKey("llm_models.id"), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # 路由结果:local / cloud / manual
    route: Mapped[str | None] = mapped_column(String(16), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    route_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 请求内容摘录(最后一条用户消息,截断):对照路由判断是否调阈值用
    request_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Token 统计:压缩前后 + 实际用量
    original_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    compressed_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="success")  # success / error
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
