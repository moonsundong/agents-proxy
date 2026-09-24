"""LLM 后端客户端:OpenAI 兼容协议的非流式/流式调用。

模块级共享 httpx.AsyncClient(连接池复用);测试可通过 set_client 注入
MockTransport 客户端。Anthropic 原生协议转换尚未实现,anthropic 类型模型
会显式报错而不是发出错误请求。
"""

from collections.abc import AsyncGenerator

import httpx

from app.config.settings import settings
from app.models.llm_model import LLMModel, ModelType
from app.utils.exceptions import AppError

_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    """获取共享 HTTP 客户端(懒加载)。"""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.request_timeout),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        )
    return _client


def set_client(client: httpx.AsyncClient | None) -> None:
    """测试注入点:替换/重置共享客户端。"""
    global _client
    _client = client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def normalize_base_url(base_url: str) -> str:
    """规范化 base_url:允许用户误填结尾的 /v1,统一剥掉再拼路径。"""
    base = base_url.rstrip("/")
    base = base.removesuffix("/v1")
    return base


def _chat_url(model: LLMModel) -> str:
    return f"{normalize_base_url(model.base_url)}/v1/chat/completions"


def _headers(model: LLMModel) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if model.api_key:
        headers["Authorization"] = f"Bearer {model.api_key}"
    return headers


def _ensure_supported(model: LLMModel) -> None:
    if model.type == ModelType.ANTHROPIC:
        raise AppError(
            message=f"模型 {model.name} 为 anthropic 类型,原生协议转换将在后续迭代支持",
            status_code=400,
            code="UNSUPPORTED_MODEL_TYPE",
        )


async def chat_completion(model: LLMModel, payload: dict) -> httpx.Response:
    """非流式调用,返回原始响应(由调用方决定透传或解析)。"""
    _ensure_supported(model)
    return await get_client().post(
        _chat_url(model), json=payload, headers=_headers(model)
    )


async def chat_completion_stream(
    model: LLMModel, payload: dict
) -> AsyncGenerator[bytes, None]:
    """流式调用,逐行透传上游 SSE 字节流。

    读超时单独放宽(慢推理模型首字节/停顿可达分钟级),不能用共享客户端
    的默认超时,否则上游长停顿会被误判为失败。
    """
    _ensure_supported(model)
    timeout = httpx.Timeout(
        connect=10.0,
        read=settings.stream_read_timeout,
        write=60.0,
        pool=60.0,
    )
    async with get_client().stream(
        "POST", _chat_url(model), json=payload, headers=_headers(model), timeout=timeout
    ) as resp:
        if resp.status_code != 200:
            # 流式入口的建连错误:读出错误体抛给上层统一处理
            body = await resp.aread()
            raise UpstreamError(resp.status_code, body)
        async for chunk in resp.aiter_bytes():
            yield chunk


class UpstreamError(Exception):
    """上游返回非 200(流式建连阶段),携带状态码与原始错误体。"""

    def __init__(self, status_code: int, body: bytes) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"upstream HTTP {status_code}")
