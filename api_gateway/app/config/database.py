"""数据库连接与会话管理(SQLAlchemy 2.0 风格)。

开发默认 SQLite;生产通过 GATEWAY_DATABASE_URL 切换 PostgreSQL。
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config.settings import settings


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


def _normalize_url(url: str) -> str:
    # SQLite 需要 aiosqlite 驱动才能异步访问
    if url.startswith("sqlite:///"):
        return url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
    return url


_IS_SQLITE = settings.database_url.startswith("sqlite:///")

engine = create_async_engine(
    _normalize_url(settings.database_url),
    echo=settings.debug,
    # SQLite:写等待超时 30s(默认 5s 在并发写日志时容易 database is locked)
    connect_args={"timeout": 30} if _IS_SQLITE else {},
)

async_session_factory = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖:提供请求级数据库会话。"""
    async with async_session_factory() as session:
        yield session


async def init_db() -> None:
    """启动时建表 + 轻量补列(开发模式;生产环境应使用 Alembic 迁移)。"""
    # 确保所有模型已注册到 Base.metadata
    from app import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # create_all 不会给已有表加新列,这里为存量数据库补列
        await conn.run_sync(_apply_column_migrations)
        # 数据迁移:旧的二元阈值策略(有本地/线上模型但无梯度区间)
        # 自动转成等价的两档区间,避免统一梯度路由后老策略失效
        await conn.exec_driver_sql(
            """
            UPDATE routing_policies
            SET decision_tiers = json_array(
                json_object('min_confidence', confidence_threshold, 'model_id', local_model_id),
                json_object('min_confidence', 0.0, 'model_id', cloud_model_id)
            )
            WHERE decision_tiers IS NULL
              AND local_model_id IS NOT NULL
              AND cloud_model_id IS NOT NULL
            """
        )
        if _IS_SQLITE:
            # WAL 模式:读写不互斥——否则流式请求期间挂着的读事务会
            # 阻塞日志写入的 COMMIT,导致 database is locked 静默丢日志。
            # journal_mode 持久化在库文件中,重复执行无副作用。
            await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
            await conn.exec_driver_sql("PRAGMA busy_timeout=30000")


def _apply_column_migrations(sync_conn) -> None:
    """给存量表补加后加的列;新库由 create_all 建好,这里是 no-op。"""
    from sqlalchemy import inspect, text

    migrations = {
        "request_logs": {
            "request_excerpt": "ALTER TABLE request_logs ADD COLUMN request_excerpt TEXT",
        },
        "routing_policies": {
            "decision_content_limit": "ALTER TABLE routing_policies ADD COLUMN decision_content_limit INTEGER NOT NULL DEFAULT 8000",
            "decision_head_ratio": "ALTER TABLE routing_policies ADD COLUMN decision_head_ratio REAL NOT NULL DEFAULT 0.2",
            "decision_tiers": "ALTER TABLE routing_policies ADD COLUMN decision_tiers TEXT",
        },
    }
    inspector = inspect(sync_conn)
    for table, columns in migrations.items():
        existing = {c["name"] for c in inspector.get_columns(table)}
        for column, ddl in columns.items():
            if column not in existing:
                sync_conn.execute(text(ddl))
