"""CC Switch 端点管理接口:/api/cc-switch/endpoints(文档模块 1)。"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.schemas.cc_switch import (
    CCSwitchEndpointCreate,
    CCSwitchEndpointOut,
    CCSwitchEndpointUpdate,
    CCSwitchImportResult,
)
from app.services import cc_switch_service

router = APIRouter(prefix="/api/cc-switch/endpoints", tags=["cc-switch"])


@router.get("", response_model=list[CCSwitchEndpointOut])
async def list_endpoints(db: AsyncSession = Depends(get_db)) -> list[CCSwitchEndpointOut]:
    return await cc_switch_service.list_endpoints(db)


@router.post("", response_model=CCSwitchEndpointOut, status_code=status.HTTP_201_CREATED)
async def create_endpoint(
    data: CCSwitchEndpointCreate, db: AsyncSession = Depends(get_db)
) -> CCSwitchEndpointOut:
    return await cc_switch_service.create_endpoint(db, data)


@router.get("/export", response_model=list[CCSwitchEndpointCreate])
async def export_endpoints(db: AsyncSession = Depends(get_db)) -> list[dict]:
    """导出全部端点为 JSON 数组(可再导入)。"""
    return await cc_switch_service.export_endpoints(db)


@router.post("/import", response_model=CCSwitchImportResult)
async def import_endpoints(
    items: list[CCSwitchEndpointCreate], db: AsyncSession = Depends(get_db)
) -> CCSwitchImportResult:
    """导入端点 JSON 数组,重名跳过。"""
    return await cc_switch_service.import_endpoints(db, items)


@router.get("/{endpoint_id}", response_model=CCSwitchEndpointOut)
async def get_endpoint(
    endpoint_id: int, db: AsyncSession = Depends(get_db)
) -> CCSwitchEndpointOut:
    return await cc_switch_service.get_endpoint(db, endpoint_id)


@router.put("/{endpoint_id}", response_model=CCSwitchEndpointOut)
async def update_endpoint(
    endpoint_id: int, data: CCSwitchEndpointUpdate, db: AsyncSession = Depends(get_db)
) -> CCSwitchEndpointOut:
    return await cc_switch_service.update_endpoint(db, endpoint_id, data)


@router.delete("/{endpoint_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_endpoint(endpoint_id: int, db: AsyncSession = Depends(get_db)) -> None:
    await cc_switch_service.delete_endpoint(db, endpoint_id)
