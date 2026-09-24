"""模型管理接口:/api/models(文档 6.1 节)。"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.schemas.model import (
    HealthCheckResult,
    ModelCreate,
    ModelOut,
    ModelUpdate,
    UpstreamModelsRequest,
    UpstreamModelsResult,
)
from app.services import model_service

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("", response_model=list[ModelOut])
async def list_models(db: AsyncSession = Depends(get_db)) -> list[ModelOut]:
    return await model_service.list_models(db)


@router.post("", response_model=ModelOut, status_code=status.HTTP_201_CREATED)
async def create_model(data: ModelCreate, db: AsyncSession = Depends(get_db)) -> ModelOut:
    return await model_service.create_model(db, data)


@router.post("/fetch-upstream", response_model=UpstreamModelsResult)
async def fetch_upstream_models(data: UpstreamModelsRequest) -> UpstreamModelsResult:
    """用表单中的地址/Key 调上游 GET /v1/models,拉取可选模型 ID 列表。"""
    return await model_service.fetch_upstream_models(data)


@router.get("/{model_id}", response_model=ModelOut)
async def get_model(model_id: int, db: AsyncSession = Depends(get_db)) -> ModelOut:
    return await model_service.get_model(db, model_id)


@router.put("/{model_id}", response_model=ModelOut)
async def update_model(
    model_id: int, data: ModelUpdate, db: AsyncSession = Depends(get_db)
) -> ModelOut:
    return await model_service.update_model(db, model_id, data)


@router.delete("/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_model(model_id: int, db: AsyncSession = Depends(get_db)) -> None:
    await model_service.delete_model(db, model_id)


@router.get("/{model_id}/health", response_model=HealthCheckResult)
async def check_health(model_id: int, db: AsyncSession = Depends(get_db)) -> HealthCheckResult:
    return await model_service.check_model_health(db, model_id)
