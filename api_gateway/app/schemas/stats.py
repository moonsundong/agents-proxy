"""统计与监控相关的 Pydantic Schema(文档 6.3 节)。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class RequestLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    request_id: str
    model_id: int | None
    model_name: str | None
    route: str | None
    confidence: float | None
    route_reason: str | None
    request_excerpt: str | None
    original_tokens: int | None
    compressed_tokens: int | None
    prompt_tokens: int | None
    completion_tokens: int | None
    latency_ms: int | None
    status: str
    error: str | None
    created_at: datetime


class LogPage(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[RequestLogOut]


class StatsOverview(BaseModel):
    today_requests: int
    today_errors: int
    today_success_rate: float | None  # 0~1,今日无请求时为 None
    today_avg_latency_ms: int | None
    total_requests: int
    tokens_saved_today: int
    tokens_saved_total: int
    route_distribution: dict[str, int]  # 今日 local/cloud/manual 分布


class DailySavings(BaseModel):
    date: str  # YYYY-MM-DD(服务器本地时区)
    requests: int
    original_tokens: int
    compressed_tokens: int
    saved_tokens: int


class SavingsTrend(BaseModel):
    days: list[DailySavings]
