"""路由策略服务:CRUD + 按场景查找启用策略。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.routing_policy import RoutingPolicy
from app.schemas.routing import RoutingPolicyCreate, RoutingPolicyUpdate
from app.utils.exceptions import ConflictError, NotFoundError


async def list_policies(db: AsyncSession) -> list[RoutingPolicy]:
    result = await db.execute(select(RoutingPolicy).order_by(RoutingPolicy.id))
    return list(result.scalars().all())


async def get_policy(db: AsyncSession, policy_id: int) -> RoutingPolicy:
    policy = await db.get(RoutingPolicy, policy_id)
    if policy is None:
        raise NotFoundError("路由策略", policy_id)
    return policy


async def _ensure_name_unique(
    db: AsyncSession, name: str, exclude_id: int | None = None
) -> None:
    stmt = select(RoutingPolicy.id).where(RoutingPolicy.name == name)
    if exclude_id is not None:
        stmt = stmt.where(RoutingPolicy.id != exclude_id)
    if (await db.execute(stmt)).scalar_one_or_none() is not None:
        raise ConflictError(f"路由策略名称已存在: {name}")


async def create_policy(db: AsyncSession, data: RoutingPolicyCreate) -> RoutingPolicy:
    await _ensure_name_unique(db, data.name)
    policy = RoutingPolicy(**data.model_dump())
    db.add(policy)
    await db.commit()
    await db.refresh(policy)
    return policy


async def update_policy(
    db: AsyncSession, policy_id: int, data: RoutingPolicyUpdate
) -> RoutingPolicy:
    policy = await get_policy(db, policy_id)
    changes = data.model_dump(exclude_unset=True)
    if "name" in changes:
        await _ensure_name_unique(db, changes["name"], exclude_id=policy_id)
    for key, value in changes.items():
        setattr(policy, key, value)
    await db.commit()
    await db.refresh(policy)
    return policy


async def delete_policy(db: AsyncSession, policy_id: int) -> None:
    policy = await get_policy(db, policy_id)
    await db.delete(policy)
    await db.commit()


async def find_active_policy(db: AsyncSession, scenario: str) -> RoutingPolicy | None:
    """按场景查找启用中的策略;指定场景没有时回退 default 场景。"""
    stmt = (
        select(RoutingPolicy)
        .where(RoutingPolicy.is_active.is_(True))
        .order_by(RoutingPolicy.id)
    )
    policies = list((await db.execute(stmt)).scalars().all())
    for policy in policies:
        if policy.scenario == scenario:
            return policy
    if scenario != "default":
        for policy in policies:
            if policy.scenario == "default":
                return policy
    return None
