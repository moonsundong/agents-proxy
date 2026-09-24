"""路由策略相关的 Pydantic Schema(文档模块 5)。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DecisionTier(BaseModel):
    """置信度梯度区间:confidence >= min_confidence 时路由到 model_id。"""

    min_confidence: float = Field(ge=0.0, le=1.0)
    model_id: int


class RoutingPolicyBase(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    scenario: str = Field(default="default", max_length=64)
    decision_model_id: int | None = None
    decision_content_limit: int = Field(default=8000, ge=500, le=200000)
    decision_head_ratio: float = Field(default=0.2, ge=0.0, le=1.0)
    decision_tiers: list[DecisionTier] | None = None
    is_active: bool = True


class RoutingPolicyCreate(RoutingPolicyBase):
    pass


class RoutingPolicyUpdate(BaseModel):
    """全字段可选,支持部分更新。"""

    name: str | None = Field(default=None, min_length=1, max_length=128)
    scenario: str | None = Field(default=None, max_length=64)
    decision_model_id: int | None = None
    decision_content_limit: int | None = Field(default=None, ge=500, le=200000)
    decision_head_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    decision_tiers: list[DecisionTier] | None = None
    is_active: bool | None = None


class RoutingPolicyOut(RoutingPolicyBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


class DecisionInfo(BaseModel):
    """决策模型评估结果(内部使用 / 日志)。"""

    confidence: float
    complexity: str | None = None
    reason: str | None = None
