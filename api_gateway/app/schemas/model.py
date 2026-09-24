"""模型管理相关的 Pydantic Schema。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ModelBase(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    type: str = Field(default="local", description="local/openai/anthropic/openai_compatible")
    base_url: str = Field(min_length=1, max_length=512)
    api_key: str | None = None
    model_id: str = Field(min_length=1, max_length=256)
    context_window: int = Field(default=8192, ge=1)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    priority: int = 0
    is_default: bool = False
    is_enabled: bool = True
    description: str | None = None


class ModelCreate(ModelBase):
    pass


class ModelUpdate(BaseModel):
    """全字段可选,支持部分更新。"""

    name: str | None = Field(default=None, min_length=1, max_length=128)
    type: str | None = None
    base_url: str | None = Field(default=None, min_length=1, max_length=512)
    api_key: str | None = None
    model_id: str | None = Field(default=None, min_length=1, max_length=256)
    context_window: int | None = Field(default=None, ge=1)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    priority: int | None = None
    is_default: bool | None = None
    is_enabled: bool | None = None
    description: str | None = None


class ModelOut(ModelBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    health_status: str
    last_health_check: datetime | None
    created_at: datetime
    updated_at: datetime


class HealthCheckResult(BaseModel):
    model_id: int
    status: str  # healthy / unhealthy
    latency_ms: int | None = None
    detail: str | None = None


class UpstreamModelsRequest(BaseModel):
    """从上游拉取模型列表的参数(模型表单未保存时使用)。"""

    base_url: str = Field(min_length=1, max_length=512)
    api_key: str | None = None
    type: str = "openai_compatible"


class UpstreamModelsResult(BaseModel):
    models: list[str]
