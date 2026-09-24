"""路由策略配置接口:/api/routing/policies(文档模块 5)。"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.schemas.routing import (
    RoutingPolicyCreate,
    RoutingPolicyOut,
    RoutingPolicyUpdate,
)
from app.services import routing_service

router = APIRouter(prefix="/api/routing/policies", tags=["routing"])


@router.get("", response_model=list[RoutingPolicyOut])
async def list_policies(db: AsyncSession = Depends(get_db)) -> list[RoutingPolicyOut]:
    return await routing_service.list_policies(db)


@router.post("", response_model=RoutingPolicyOut, status_code=status.HTTP_201_CREATED)
async def create_policy(
    data: RoutingPolicyCreate, db: AsyncSession = Depends(get_db)
) -> RoutingPolicyOut:
    return await routing_service.create_policy(db, data)


@router.get("/{policy_id}", response_model=RoutingPolicyOut)
async def get_policy(policy_id: int, db: AsyncSession = Depends(get_db)) -> RoutingPolicyOut:
    return await routing_service.get_policy(db, policy_id)


@router.put("/{policy_id}", response_model=RoutingPolicyOut)
async def update_policy(
    policy_id: int, data: RoutingPolicyUpdate, db: AsyncSession = Depends(get_db)
) -> RoutingPolicyOut:
    return await routing_service.update_policy(db, policy_id, data)


@router.delete("/{policy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_policy(policy_id: int, db: AsyncSession = Depends(get_db)) -> None:
    await routing_service.delete_policy(db, policy_id)
