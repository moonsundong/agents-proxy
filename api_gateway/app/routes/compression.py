"""压缩策略配置接口:/api/compression/config(文档模块 4,全局单行配置)。"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.schemas.compression import CompressionConfigOut, CompressionConfigUpdate
from app.services import compression_service

router = APIRouter(prefix="/api/compression", tags=["compression"])


@router.get("/config", response_model=CompressionConfigOut)
async def get_config(db: AsyncSession = Depends(get_db)) -> CompressionConfigOut:
    """读取全局压缩配置(首次访问时自动创建默认值)。"""
    return await compression_service.get_config(db)


@router.put("/config", response_model=CompressionConfigOut)
async def update_config(
    data: CompressionConfigUpdate, db: AsyncSession = Depends(get_db)
) -> CompressionConfigOut:
    return await compression_service.update_config(db, data)
