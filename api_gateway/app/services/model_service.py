"""模型管理服务:CRUD + 默认模型唯一性维护 + 健康检查。"""

import time

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.models.llm_model import HealthStatus, LLMModel, ModelType
from app.schemas.model import (
    HealthCheckResult,
    ModelCreate,
    ModelUpdate,
    UpstreamModelsRequest,
    UpstreamModelsResult,
)
from app.utils.exceptions import AppError, ConflictError, NotFoundError


async def list_models(db: AsyncSession) -> list[LLMModel]:
    result = await db.execute(select(LLMModel).order_by(LLMModel.priority, LLMModel.id))
    return list(result.scalars().all())


async def get_model(db: AsyncSession, model_id: int) -> LLMModel:
    model = await db.get(LLMModel, model_id)
    if model is None:
        raise NotFoundError("模型", model_id)
    return model


async def _ensure_name_unique(db: AsyncSession, name: str, exclude_id: int | None = None) -> None:
    stmt = select(LLMModel.id).where(LLMModel.name == name)
    if exclude_id is not None:
        stmt = stmt.where(LLMModel.id != exclude_id)
    if (await db.execute(stmt)).scalar_one_or_none() is not None:
        raise ConflictError(f"模型名称已存在: {name}")


async def _clear_default(db: AsyncSession, exclude_id: int | None = None) -> None:
    """保证全局只有一个默认模型。"""
    stmt = update(LLMModel).values(is_default=False).where(LLMModel.is_default.is_(True))
    if exclude_id is not None:
        stmt = stmt.where(LLMModel.id != exclude_id)
    await db.execute(stmt)


async def create_model(db: AsyncSession, data: ModelCreate) -> LLMModel:
    await _ensure_name_unique(db, data.name)
    model = LLMModel(**data.model_dump())
    db.add(model)
    await db.flush()  # 先拿到 id
    if model.is_default:
        await _clear_default(db, exclude_id=model.id)
    await db.commit()
    await db.refresh(model)
    return model


async def update_model(db: AsyncSession, model_id: int, data: ModelUpdate) -> LLMModel:
    model = await get_model(db, model_id)
    changes = data.model_dump(exclude_unset=True)
    if "name" in changes:
        await _ensure_name_unique(db, changes["name"], exclude_id=model_id)
    for key, value in changes.items():
        setattr(model, key, value)
    if changes.get("is_default"):
        await _clear_default(db, exclude_id=model.id)
    await db.commit()
    await db.refresh(model)
    return model


async def delete_model(db: AsyncSession, model_id: int) -> None:
    model = await get_model(db, model_id)
    await db.delete(model)
    await db.commit()


async def check_model_health(db: AsyncSession, model_id: int) -> HealthCheckResult:
    """Ping 模型后端的 /v1/models 或根路径,更新健康状态。"""
    model = await get_model(db, model_id)
    # 允许 base_url 误填结尾 /v1,统一规范化后再拼路径
    from app.clients.llm_client import normalize_base_url

    url = normalize_base_url(model.base_url)
    headers = {"Authorization": f"Bearer {model.api_key}"} if model.api_key else {}

    start = time.perf_counter()
    status = HealthStatus.UNHEALTHY
    detail: str | None = None
    try:
        async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
            resp = await client.get(f"{url}/v1/models", headers=headers)
            if resp.status_code < 500:
                status = HealthStatus.HEALTHY
            else:
                detail = f"HTTP {resp.status_code}"
    except httpx.HTTPError as exc:
        detail = str(exc)
    latency_ms = int((time.perf_counter() - start) * 1000)

    model.health_status = status
    from datetime import UTC, datetime

    model.last_health_check = datetime.now(UTC)
    await db.commit()

    return HealthCheckResult(
        model_id=model.id,
        status=status,
        latency_ms=latency_ms if status == HealthStatus.HEALTHY else None,
        detail=detail,
    )


async def fetch_upstream_models(data: UpstreamModelsRequest) -> UpstreamModelsResult:
    """调用上游 GET /v1/models 拉取可用模型 ID 列表(OpenAI/Anthropic 格式同构)。"""
    from app.clients.llm_client import get_client, normalize_base_url

    url = f"{normalize_base_url(data.base_url)}/v1/models"
    headers: dict[str, str] = {}
    if data.type == ModelType.ANTHROPIC:
        headers["anthropic-version"] = "2023-06-01"
        if data.api_key:
            headers["x-api-key"] = data.api_key
    elif data.api_key:
        headers["Authorization"] = f"Bearer {data.api_key}"

    try:
        resp = await get_client().get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise AppError(
            message=f"无法连接上游: {exc}",
            status_code=502,
            code="UPSTREAM_UNAVAILABLE",
        ) from exc
    if resp.status_code != 200:
        raise AppError(
            message=f"上游返回 HTTP {resp.status_code}: {resp.text[:200]}",
            status_code=502,
            code="UPSTREAM_ERROR",
        )
    try:
        payload = resp.json()
        ids = sorted({item["id"] for item in payload.get("data", []) if "id" in item})
    except (ValueError, AttributeError) as exc:
        raise AppError(
            message="上游响应格式无法解析",
            status_code=502,
            code="INVALID_UPSTREAM_BODY",
        ) from exc
    return UpstreamModelsResult(models=ids)
