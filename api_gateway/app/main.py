"""应用入口:FastAPI 实例装配。"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.clients import llm_client
from app.config.database import init_db
from app.config.logging_setup import setup_logging
from app.config.settings import settings
from app.middleware.logging import RequestLoggingMiddleware
from app.routes import api_router
from app.utils.exceptions import register_exception_handlers

setup_logging()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    await init_db()
    yield
    await llm_client.close_client()


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestLoggingMiddleware)

    register_exception_handlers(app)
    app.include_router(api_router)
    return app


app = create_app()
