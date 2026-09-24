"""CC Switch 端点相关的 Pydantic Schema(文档模块 1)。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CCSwitchEndpointBase(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    url: str = Field(min_length=1, max_length=512)
    api_key: str | None = None
    timeout: float = Field(default=30.0, gt=0)
    health_check_interval: int = Field(default=60, ge=5)
    is_enabled: bool = True


class CCSwitchEndpointCreate(CCSwitchEndpointBase):
    pass


class CCSwitchEndpointUpdate(BaseModel):
    """全字段可选,支持部分更新。"""

    name: str | None = Field(default=None, min_length=1, max_length=128)
    url: str | None = Field(default=None, min_length=1, max_length=512)
    api_key: str | None = None
    timeout: float | None = Field(default=None, gt=0)
    health_check_interval: int | None = Field(default=None, ge=5)
    is_enabled: bool | None = None


class CCSwitchEndpointOut(CCSwitchEndpointBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


class CCSwitchImportResult(BaseModel):
    imported: int
    skipped: list[str]  # 因重名跳过的端点名称
