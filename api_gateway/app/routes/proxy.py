"""请求转发接口:/v1/chat/completions(文档 6.2 节,OpenAI 兼容)。"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.schemas.proxy import ChatCompletionRequest
from app.services import proxy_service

router = APIRouter(tags=["proxy"])

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _client_source(request: Request, req: ChatCompletionRequest) -> str | None:
    """客户端来源:User-Agent 优先;无 UA 时回退请求体 metadata.user_id。

    解析在路由层做,服务层只接收结果——日志链路(含取消补记)都用同一个值。
    """
    source = proxy_service.normalize_source(request.headers.get("user-agent"))
    if source is not None:
        return source
    metadata = (req.model_extra or {}).get("metadata")
    if isinstance(metadata, dict):
        user_id = metadata.get("user_id")
        if isinstance(user_id, str) and user_id.strip():
            return user_id.strip()[:64]
    return None


@router.post("/v1/chat/completions")
async def chat_completions(
    request: Request, req: ChatCompletionRequest, db: AsyncSession = Depends(get_db)
) -> Response:
    """统一转发入口;请求体 stream=true 时走 SSE 流式。"""
    source = _client_source(request, req)
    if req.stream:
        # 准备阶段(压缩/路由/熔断)先完成,此处的异常走正常 JSON 错误响应
        sc = await proxy_service.prepare_stream(db, req, source)
        return StreamingResponse(
            proxy_service.stream_generator(sc),
            media_type="text/event-stream",
            headers=_SSE_HEADERS,
        )
    status_code, body = await proxy_service.chat_completion(db, req, source)
    return JSONResponse(status_code=status_code, content=body)


@router.post("/v1/chat/completions/stream")
async def chat_completions_stream(
    request: Request, req: ChatCompletionRequest, db: AsyncSession = Depends(get_db)
) -> StreamingResponse:
    """流式转发入口(强制 stream=true 的别名)。"""
    req.stream = True
    sc = await proxy_service.prepare_stream(db, req, _client_source(request, req))
    return StreamingResponse(
        proxy_service.stream_generator(sc),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
