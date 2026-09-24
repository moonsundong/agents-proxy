"""CC Switch 端点服务:CRUD + JSON 导入/导出(文档模块 1)。

端点数据直接读库,改动立即生效(热加载),无需缓存。
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cc_switch_endpoint import CCSwitchEndpoint
from app.schemas.cc_switch import (
    CCSwitchEndpointCreate,
    CCSwitchEndpointUpdate,
    CCSwitchImportResult,
)
from app.utils.exceptions import ConflictError, NotFoundError


async def list_endpoints(db: AsyncSession) -> list[CCSwitchEndpoint]:
    result = await db.execute(select(CCSwitchEndpoint).order_by(CCSwitchEndpoint.id))
    return list(result.scalars().all())


async def get_endpoint(db: AsyncSession, endpoint_id: int) -> CCSwitchEndpoint:
    endpoint = await db.get(CCSwitchEndpoint, endpoint_id)
    if endpoint is None:
        raise NotFoundError("CC Switch 端点", endpoint_id)
    return endpoint


async def _ensure_name_unique(
    db: AsyncSession, name: str, exclude_id: int | None = None
) -> None:
    stmt = select(CCSwitchEndpoint.id).where(CCSwitchEndpoint.name == name)
    if exclude_id is not None:
        stmt = stmt.where(CCSwitchEndpoint.id != exclude_id)
    if (await db.execute(stmt)).scalar_one_or_none() is not None:
        raise ConflictError(f"端点名称已存在: {name}")


async def create_endpoint(
    db: AsyncSession, data: CCSwitchEndpointCreate
) -> CCSwitchEndpoint:
    await _ensure_name_unique(db, data.name)
    endpoint = CCSwitchEndpoint(**data.model_dump())
    db.add(endpoint)
    await db.commit()
    await db.refresh(endpoint)
    return endpoint


async def update_endpoint(
    db: AsyncSession, endpoint_id: int, data: CCSwitchEndpointUpdate
) -> CCSwitchEndpoint:
    endpoint = await get_endpoint(db, endpoint_id)
    changes = data.model_dump(exclude_unset=True)
    if "name" in changes:
        await _ensure_name_unique(db, changes["name"], exclude_id=endpoint_id)
    for key, value in changes.items():
        setattr(endpoint, key, value)
    await db.commit()
    await db.refresh(endpoint)
    return endpoint


async def delete_endpoint(db: AsyncSession, endpoint_id: int) -> None:
    endpoint = await get_endpoint(db, endpoint_id)
    await db.delete(endpoint)
    await db.commit()


async def export_endpoints(db: AsyncSession) -> list[dict]:
    """导出为 JSON 数组(不含数据库内部字段,可再导入)。"""
    endpoints = await list_endpoints(db)
    return [
        {
            "name": e.name,
            "url": e.url,
            "api_key": e.api_key,
            "timeout": e.timeout,
            "health_check_interval": e.health_check_interval,
            "is_enabled": e.is_enabled,
        }
        for e in endpoints
    ]


async def import_endpoints(
    db: AsyncSession, items: list[CCSwitchEndpointCreate]
) -> CCSwitchImportResult:
    """导入端点:重名跳过,返回导入统计。"""
    existing = {e.name for e in await list_endpoints(db)}
    imported = 0
    skipped: list[str] = []
    for item in items:
        if item.name in existing:
            skipped.append(item.name)
            continue
        db.add(CCSwitchEndpoint(**item.model_dump()))
        existing.add(item.name)
        imported += 1
    await db.commit()
    return CCSwitchImportResult(imported=imported, skipped=skipped)
