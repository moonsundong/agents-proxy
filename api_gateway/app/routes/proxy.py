"""请求转发接口:/v1/chat/completions(文档 6.2 节,OpenAI 兼容)。"""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.schemas.proxy import ChatCompletionRequest
from app.services import proxy_service

router = APIRouter(tags=["proxy"])

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


@router.post("/v1/chat/completions")
async def chat_completions(
    req: ChatCompletionRequest, db: AsyncSession = Depends(get_db)
) -> Response:
    """统一转发入口;请求体 stream=true 时走 SSE 流式。"""
    if req.stream:
        # 准备阶段(压缩/路由/熔断)先完成,此处的异常走正常 JSON 错误响应
        sc = await proxy_service.prepare_stream(db, req)
        return StreamingResponse(
            proxy_service.stream_generator(sc),
            media_type="text/event-stream",
            headers=_SSE_HEADERS,
        )
    status_code, body = await proxy_service.chat_completion(db, req)
    return JSONResponse(status_code=status_code, content=body)


@router.post("/v1/chat/completions/stream")
async def chat_completions_stream(
    req: ChatCompletionRequest, db: AsyncSession = Depends(get_db)
) -> StreamingResponse:
    """流式转发入口(强制 stream=true 的别名)。"""
    req.stream = True
    sc = await proxy_service.prepare_stream(db, req)
    return StreamingResponse(
        proxy_service.stream_generator(sc),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
