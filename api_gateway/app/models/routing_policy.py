"""路由策略表:对应文档模块 5(智能决策路由引擎)。"""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.config.database import Base


class RoutingPolicy(Base):
    __tablename__ = "routing_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)

    # 适用场景,如 code / chat / default,便于不同场景使用不同阈值
    scenario: Mapped[str] = mapped_column(String(64), default="default")

    # 置信度阈值:>= 阈值走本地模型,< 阈值走线上模型(默认 0.7)
    confidence_threshold: Mapped[float] = mapped_column(Float, default=0.7)

    decision_model_id: Mapped[int | None] = mapped_column(
        ForeignKey("llm_models.id"), nullable=True
    )

    # 决策输入采样上限(字符):仅对本地决策模型生效(保评估速度);
    # 线上决策模型按其 context_window 自动推导安全上限,不受此限
    decision_content_limit: Mapped[int] = mapped_column(Integer, default=8000)

    # 决策内容超长截断时的头部保留占比,其余给尾部(尾部是最新用户消息,
    # 复杂度判断主要看它);默认 0.2 = 头部 20% + 尾部 80%
    decision_head_ratio: Mapped[float] = mapped_column(Float, default=0.2)

    # 置信度梯度区间(JSON):[{"min_confidence": 0.8, "model_id": 1}, ...],
    # 按下限从高到低取第一个 confidence >= min_confidence 的区间。
    # 为空时回退到 confidence_threshold + local/cloud 的二元分流
    decision_tiers: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    local_model_id: Mapped[int | None] = mapped_column(
        ForeignKey("llm_models.id"), nullable=True
    )
    cloud_model_id: Mapped[int | None] = mapped_column(
        ForeignKey("llm_models.id"), nullable=True
    )

    # A/B 测试:按比例将部分请求强制走本地模型以对比效果
    ab_test_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    ab_local_ratio: Mapped[float] = mapped_column(Float, default=0.5)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
