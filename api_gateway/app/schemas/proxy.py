"""请求转发相关的 Pydantic Schema。

OpenAI 兼容请求字段繁多且不断演进,这里只校验网关依赖的最小字段集,
其余字段原样透传给后端(extra="allow")。
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str | None = Field(default=None, description="模型名称,缺省时使用默认模型")
    messages: list[dict[str, Any]] = Field(min_length=1)
    stream: bool = False

    def to_upstream_payload(self, upstream_model_id: str) -> dict[str, Any]:
        """生成发给后端模型的请求体:替换模型 ID,剥离网关私有字段,其余透传。"""
        payload = self.model_dump()
        payload.pop("scenario", None)  # 网关路由用的扩展字段,不发给上游
        payload["model"] = upstream_model_id
        return payload

    @property
    def scenario(self) -> str:
        """路由场景(扩展字段),默认 default。"""
        return str((self.model_extra or {}).get("scenario") or "default")
