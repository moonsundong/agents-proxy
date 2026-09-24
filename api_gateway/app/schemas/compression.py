"""压缩策略相关的 Pydantic Schema。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CompressionConfigOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    mode: str  # on / off / tools_only
    compress_user_messages: bool
    compress_system_messages: bool
    protect_recent: int
    target_ratio: float | None
    min_tokens_to_compress: int
    kompress_model: str | None
    keep_markers: str | None
    updated_at: datetime


class CompressionConfigUpdate(BaseModel):
    """全字段可选,支持部分更新。"""

    mode: str | None = Field(default=None, pattern="^(on|off|tools_only)$")
    compress_user_messages: bool | None = None
    compress_system_messages: bool | None = None
    protect_recent: int | None = Field(default=None, ge=0)
    target_ratio: float | None = Field(default=None, gt=0.0, le=1.0)
    min_tokens_to_compress: int | None = Field(default=None, ge=0)
    kompress_model: str | None = None
    keep_markers: str | None = None


class CompressionResultInfo(BaseModel):
    """单次压缩的结果摘要(写入日志 / 返回给调用方)。"""

    compressed: bool  # 是否实际执行了压缩
    degraded: bool  # 是否发生降级(压缩失败直通)
    original_tokens: int
    compressed_tokens: int
    transforms: list[str] = []
    error: str | None = None
