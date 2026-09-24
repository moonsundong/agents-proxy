"""转发引擎:模型选择 → 压缩 → 重试/熔断调用 → 全链路日志(文档模块 2)。

- 重试:仅对传输错误与 429/5xx 做指数退避重试,4xx 客户端错误直接透传;
- 熔断:每模型独立内存态断路器,连续失败阈值开路,冷却后半开试探;
- 日志:独立数据库会话写 request_logs,流式请求在流结束后落库。
"""

import asyncio
import contextlib
import json
import re
import time
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass

import httpx
from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients import llm_client
from app.clients.headroom_client import CompressOutcome, estimate_tokens
from app.config.database import async_session_factory
from app.config.settings import settings
from app.models.llm_model import LLMModel
from app.models.request_log import RequestLog
from app.schemas.proxy import ChatCompletionRequest
from app.services import compression_service, decision_service
from app.utils.exceptions import AppError

# 可重试的上游状态码
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


# ---------------------------------------------------------------- 熔断器


class CircuitBreaker:
    """单模型断路器:closed → (连续失败达阈值) open → (冷却后) half-open。"""

    def __init__(self) -> None:
        self.consecutive_failures = 0
        self.opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        if self.opened_at is None:
            return False
        # 冷却时间到:进入半开,放行一次试探
        return time.monotonic() - self.opened_at < settings.circuit_cooldown_seconds

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.opened_at = None

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= settings.circuit_failure_threshold:
            self.opened_at = time.monotonic()


_breakers: dict[int, CircuitBreaker] = {}


def get_breaker(model_id: int) -> CircuitBreaker:
    return _breakers.setdefault(model_id, CircuitBreaker())


def reset_breakers() -> None:
    """测试用:清空所有断路器状态。"""
    _breakers.clear()


# ---------------------------------------------------------------- 模型选择


async def _compression_reference_model(db: AsyncSession) -> LLMModel | None:
    """压缩用的参考模型(其上下文窗口作为压缩上限)。

    路由决策需要先看到压缩后的消息,而压缩又需要一个 context_window 参考,
    这里统一取默认启用模型;一个可用模型都没有时返回 None(跳过压缩,
    后续 resolve_route 会抛出 503)。
    """
    stmt = (
        select(LLMModel)
        .where(LLMModel.is_enabled.is_(True))
        .order_by(LLMModel.is_default.desc(), LLMModel.priority, LLMModel.id)
    )
    return (await db.execute(stmt)).scalars().first()


async def _log_prepare_cancel(req: ChatCompletionRequest, start: float) -> None:
    """准备阶段被客户端中断的最小化日志(此时还没有路由结果可记)。"""
    await _insert_log(
        request_id=uuid.uuid4().hex[:12],
        model_id=None,
        model_name=None,
        route=None,
        confidence=None,
        route_reason=None,
        request_excerpt=_request_excerpt(req.messages),
        original_tokens=None,
        compressed_tokens=None,
        prompt_tokens=None,
        completion_tokens=None,
        latency_ms=int((time.perf_counter() - start) * 1000),
        status="cancelled",
        error="客户端在压缩/决策阶段中断(准备耗时过长)",
    )


async def _prepare(
    db: AsyncSession, req: ChatCompletionRequest
) -> tuple[LLMModel, decision_service.RouteDecision, CompressOutcome]:
    """压缩 → 路由决策,返回 (参考模型, 路由决策, 压缩结果)。"""
    start = time.perf_counter()
    try:
        ref_model = await _compression_reference_model(db)
        if ref_model is not None:
            compression = await compression_service.apply_compression(
                db, req.messages, ref_model
            )
        else:
            tokens = estimate_tokens(req.messages)
            compression = CompressOutcome(
                messages=req.messages, original_tokens=tokens, compressed_tokens=tokens
            )
        decision = await decision_service.resolve_route(
            db, req.model, req.scenario, compression.messages, compression.compressed_tokens
        )
        return ref_model, decision, compression
    except asyncio.CancelledError:
        # 大上下文的压缩可能耗时分钟级,客户端等不及断开时也要留痕
        task = asyncio.create_task(_log_prepare_cancel(req, start))
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_on_log_task_done)
        raise


# ---------------------------------------------------------------- 日志

# 请求摘录的字符上限:够对照路由判断即可,避免日志表膨胀
_EXCERPT_LIMIT = 1000

# SQLite 单写者串行化 + 锁冲突重试参数
_LOG_WRITE_LOCK = asyncio.Lock()
_LOG_WRITE_MAX_ATTEMPTS = 3
_LOG_WRITE_RETRY_BACKOFF = 0.5


def _request_excerpt(messages: list[dict]) -> str:
    """最后一条用户消息的截断摘录(原始请求,非压缩后),供日志页对照路由。"""
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, list):  # OpenAI 多段内容格式
            content = " ".join(
                str(part.get("text", "")) if isinstance(part, dict) else str(part)
                for part in content
            )
        text = str(content or "").strip()
        if text:
            return text[:_EXCERPT_LIMIT]
    return json.dumps(messages, ensure_ascii=False)[:_EXCERPT_LIMIT]


@dataclass
class LogContext:
    request_id: str
    model: LLMModel
    route: str  # local / cloud / manual
    confidence: float | None
    route_reason: str
    compression: CompressOutcome
    excerpt: str
    start: float


async def _insert_log(**fields) -> None:
    """串行写入一条 request_logs,锁冲突时退避重试。

    SQLite 是单写者:本进程的并发写只会互相制造 database is locked,
    用模块级锁串行化;WAL 下剩余的锁冲突只应来自检查点等瞬时事件,重试可恢复。
    """
    async with _LOG_WRITE_LOCK:
        for attempt in range(_LOG_WRITE_MAX_ATTEMPTS):
            try:
                async with async_session_factory() as session:
                    session.add(RequestLog(**fields))
                    await session.commit()
                return
            except OperationalError as exc:
                if (
                    "locked" not in str(exc).lower()
                    or attempt == _LOG_WRITE_MAX_ATTEMPTS - 1
                ):
                    raise
                await asyncio.sleep(_LOG_WRITE_RETRY_BACKOFF * (2**attempt))


def _log_fields(
    ctx: LogContext,
    *,
    status: str,
    latency_ms: int,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    error: str | None = None,
) -> dict:
    return {
        "request_id": ctx.request_id,
        "model_id": ctx.model.id,
        "model_name": ctx.model.name,
        "route": ctx.route,
        "confidence": ctx.confidence,
        "route_reason": ctx.route_reason,
        "request_excerpt": ctx.excerpt,
        "original_tokens": ctx.compression.original_tokens,
        "compressed_tokens": ctx.compression.compressed_tokens,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "latency_ms": latency_ms,
        "status": status,
        "error": error,
    }


async def write_log(
    ctx: LogContext,
    *,
    status: str,
    latency_ms: int,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    error: str | None = None,
) -> None:
    """写请求日志(取消安全)。

    客户端断开时 uvicorn 会直接取消正在 await 本函数的请求任务;取消若落在
    commit 中途,aiosqlite 连接会带着未完结事务泄漏,写锁永不释放,之后所有
    日志写全部 database is locked(2026-09-24 日志黑洞事故根因)。因此实际
    写入放进独立任务并用 shield 屏蔽调用方取消:调用方被取消时写入仍在后台
    完成,失败由 _on_log_task_done 记录可见。
    """
    task = asyncio.create_task(
        _insert_log(
            **_log_fields(
                ctx,
                status=status,
                latency_ms=latency_ms,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                error=error,
            )
        )
    )
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_on_log_task_done)
    try:
        await asyncio.shield(task)
    except OperationalError as exc:
        # 写日志失败不应让请求本身报错;失败已由 _on_log_task_done 记录
        # (CancelledError 是 BaseException,不在此处拦截,正常向上传播)
        logger.warning("请求日志写入失败(已放弃): {}", exc)


# 取消路径上的日志写入不能随请求任务一起被取消,用火记忘任务兜底
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def _on_log_task_done(task: asyncio.Task) -> None:
    """火记忘任务的失败必须可见(如 database is locked),否则又是静默丢日志。"""
    _BACKGROUND_TASKS.discard(task)
    if not task.cancelled() and (exc := task.exception()) is not None:
        logger.error("后台写请求日志失败: {}", exc)


def _log_in_background(ctx: LogContext, *, status: str, error: str) -> None:
    latency = int((time.perf_counter() - ctx.start) * 1000)
    task = asyncio.create_task(
        _insert_log(**_log_fields(ctx, status=status, latency_ms=latency, error=error))
    )
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_on_log_task_done)


# ---------------------------------------------------------------- 非流式


async def chat_completion(
    db: AsyncSession, req: ChatCompletionRequest
) -> tuple[int, dict]:
    """非流式转发,返回 (HTTP 状态码, 响应体)。"""
    _, decision, compression = await _prepare(db, req)
    model = decision.model
    payload = req.to_upstream_payload(model.model_id)
    payload["messages"] = compression.messages

    ctx = LogContext(
        request_id=uuid.uuid4().hex[:12],
        model=model,
        route=decision.route,
        confidence=decision.confidence,
        route_reason=decision.reason,
        compression=compression,
        excerpt=_request_excerpt(req.messages),
        start=time.perf_counter(),
    )
    breaker = get_breaker(model.id)
    if breaker.is_open:
        latency = int((time.perf_counter() - ctx.start) * 1000)
        await write_log(
            ctx, status="error", latency_ms=latency, error="熔断器开路,请求被拒绝"
        )
        raise AppError(
            message=f"模型 {model.name} 连续失败已熔断,请稍后再试",
            status_code=503,
            code="CIRCUIT_OPEN",
        )

    last_exc: Exception | None = None
    try:
        for attempt in range(settings.llm_max_retries + 1):
            try:
                resp = await llm_client.chat_completion(model, payload)
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt < settings.llm_max_retries:
                    await asyncio.sleep(settings.llm_retry_backoff * (2**attempt))
                continue

            if resp.status_code in _RETRYABLE_STATUS and attempt < settings.llm_max_retries:
                await asyncio.sleep(settings.llm_retry_backoff * (2**attempt))
                continue

            latency = int((time.perf_counter() - ctx.start) * 1000)
            try:
                body = resp.json()
            except ValueError:
                body = {"error": {"code": "INVALID_UPSTREAM_BODY", "message": resp.text[:500]}}

            if resp.status_code == 200:
                breaker.record_success()
                usage = body.get("usage") or {}
                await write_log(
                    ctx,
                    status="success",
                    latency_ms=latency,
                    prompt_tokens=usage.get("prompt_tokens"),
                    completion_tokens=usage.get("completion_tokens"),
                )
            else:
                # 熔断按逻辑请求计数(重试不重复计数)
                breaker.record_failure()
                await write_log(
                    ctx,
                    status="error",
                    latency_ms=latency,
                    error=f"upstream HTTP {resp.status_code}",
                )
            return resp.status_code, body

        breaker.record_failure()
        latency = int((time.perf_counter() - ctx.start) * 1000)
        await write_log(
            ctx, status="error", latency_ms=latency, error=f"上游不可达: {last_exc}"
        )
        raise AppError(
            message=f"上游模型 {model.name} 不可达(已重试 {settings.llm_max_retries} 次): {last_exc}",
            status_code=502,
            code="UPSTREAM_UNAVAILABLE",
        )
    except asyncio.CancelledError:
        # 客户端中断(取消/重连):不算上游故障,不计熔断,记录后重抛
        _log_in_background(ctx, status="cancelled", error="客户端中断连接")
        raise


# ---------------------------------------------------------------- 流式


@dataclass
class StreamContext:
    """流式请求的准备结果:路由/压缩/熔断检查全部完成后的转发上下文。"""

    model: LLMModel
    payload: dict
    log: LogContext


async def prepare_stream(db: AsyncSession, req: ChatCompletionRequest) -> StreamContext:
    """流式转发的准备阶段:压缩 → 路由 → 熔断检查。

    必须在 StreamingResponse 建立之前 await,此阶段的异常(如无可用模型)
    才能以正常的 JSON 错误响应返回;一旦流开始,错误只能以 SSE 事件表达。
    """
    _, decision, compression = await _prepare(db, req)
    model = decision.model
    payload = req.to_upstream_payload(model.model_id)
    payload["messages"] = compression.messages
    payload["stream"] = True
    # 让兼容后端在流末尾回传 token 用量,用于日志统计
    payload.setdefault("stream_options", {"include_usage": True})

    ctx = LogContext(
        request_id=uuid.uuid4().hex[:12],
        model=model,
        route=decision.route,
        confidence=decision.confidence,
        route_reason=decision.reason,
        compression=compression,
        excerpt=_request_excerpt(req.messages),
        start=time.perf_counter(),
    )
    breaker = get_breaker(model.id)
    if breaker.is_open:
        await write_log(
            ctx,
            status="error",
            latency_ms=int((time.perf_counter() - ctx.start) * 1000),
            error="熔断器开路,请求被拒绝",
        )
        raise AppError(
            message=f"模型 {model.name} 连续失败已熔断,请稍后再试",
            status_code=503,
            code="CIRCUIT_OPEN",
        )
    return StreamContext(model=model, payload=payload, log=ctx)


async def _with_keepalive(model: LLMModel, payload: dict) -> AsyncGenerator[bytes, None]:
    """包装上游 SSE 流:上游空闲超过心跳间隔时,向下游注入 SSE 注释心跳。

    codex 等客户端有流空闲超时,上游(如 Kimi)大 prompt 首字节/推理停顿
    超过其容忍就会断开重连(每次重连都重新计费!)。SSE 规范中 ": " 开头
    的注释行会被客户端解析器忽略,用它保活不影响协议内容。
    """
    interval = settings.stream_keepalive_seconds
    stream = llm_client.chat_completion_stream(model, payload)
    if interval <= 0:
        async for chunk in stream:
            yield chunk
        return
    ait = stream.__aiter__()
    task: asyncio.Task[bytes] | None = None
    try:
        while True:
            if task is None:
                task = asyncio.ensure_future(ait.__anext__())
            # 注意必须用 asyncio.wait(超时不取消),不能用 wait_for(会取消上游迭代)
            done, _ = await asyncio.wait({task}, timeout=interval)
            if task not in done:
                yield b": keepalive\n\n"
                continue
            try:
                chunk = task.result()
            except StopAsyncIteration:
                return
            task = None
            yield chunk
    finally:
        if task is not None:
            if not task.done():
                task.cancel()
                # 等上游迭代任务真正结束再 aclose:aclose 与 pending 的
                # __anext__ 并发会抛 "asynchronous generator is already
                # running"(uvicorn 侧断连时就踩过),导致上游流泄漏
                with contextlib.suppress(Exception):
                    await asyncio.wait({task}, timeout=2)
            if task.done() and not task.cancelled():
                # 取回异常标记已消费,避免 "Task exception was never retrieved"
                task.exception()
        with contextlib.suppress(Exception):
            await stream.aclose()


async def stream_generator(sc: StreamContext) -> AsyncGenerator[bytes, None]:
    """流式转发:逐块透传上游 SSE;建连阶段可重试,流开始后的错误以 SSE 事件告知。"""
    model, payload, ctx = sc.model, sc.payload, sc.log
    breaker = get_breaker(model.id)

    stream_started = False
    usage: dict = {}
    last_exc: Exception | None = None
    logged = False
    finish_seen = False  # 客户端是否已拿到完整响应(非 null finish_reason)

    try:
        for attempt in range(settings.llm_max_retries + 1):
            try:
                prev_chunk = b""
                async for chunk in _with_keepalive(model, payload):
                    stream_started = True
                    # usage 行可能跨块分割,拼接上一块再解析
                    usage.update(_extract_usage(prev_chunk + chunk))
                    if not finish_seen:
                        finish_seen = _has_finish_reason(prev_chunk + chunk)
                    prev_chunk = chunk
                    yield chunk
                breaker.record_success()
                await write_log(
                    ctx,
                    status="success",
                    latency_ms=int((time.perf_counter() - ctx.start) * 1000),
                    prompt_tokens=usage.get("prompt_tokens"),
                    completion_tokens=usage.get("completion_tokens"),
                )
                logged = True
                return
            except (llm_client.UpstreamError, httpx.HTTPError) as exc:
                last_exc = exc
                if stream_started:
                    # 流已开始,不能重试:以 SSE 错误事件收尾
                    break
                if isinstance(exc, llm_client.UpstreamError) and (
                    exc.status_code not in _RETRYABLE_STATUS
                    or attempt >= settings.llm_max_retries
                ):
                    # 不可重试的上游错误:透传状态码与错误体
                    breaker.record_failure()
                    await write_log(
                        ctx,
                        status="error",
                        latency_ms=int((time.perf_counter() - ctx.start) * 1000),
                        error=f"upstream HTTP {exc.status_code}",
                    )
                    logged = True
                    yield _sse_raw(exc.body)
                    return
                if attempt < settings.llm_max_retries:
                    await asyncio.sleep(settings.llm_retry_backoff * (2**attempt))
                continue

        breaker.record_failure()
        await write_log(
            ctx,
            status="error",
            latency_ms=int((time.perf_counter() - ctx.start) * 1000),
            error=f"流式调用失败: {last_exc}",
        )
        logged = True
        yield _sse_error(f"流式调用失败(已重试 {settings.llm_max_retries} 次): {last_exc}", "STREAM_FAILED")
    finally:
        if not logged:
            if finish_seen:
                # 客户端拿到 finish_reason 后主动关闭(不等尾部的 usage/[DONE]),
                # 响应其实已完整交付,算成功而不是取消
                _log_in_background(
                    ctx,
                    status="success",
                    error="已完整交付,客户端提前关闭(未取回 token 用量)",
                )
            else:
                # 生成中途断连:CancelledError(任务被取消)或
                # GeneratorExit(uvicorn 直接 aclose 生成器)都汇聚到这里
                _log_in_background(ctx, status="cancelled", error="客户端中断连接")


# ---------------------------------------------------------------- SSE 工具


def _sse_error(message: str, code: str) -> bytes:
    body = json.dumps({"error": {"code": code, "message": message}})
    return f"data: {body}\n\ndata: [DONE]\n\n".encode()


def _sse_raw(body: bytes) -> bytes:
    return b"data: " + body + b"\n\ndata: [DONE]\n\n"


def _extract_usage(chunk: bytes) -> dict:
    """从 SSE 块中提取 usage 字段(后端在 include_usage 下于末尾返回)。"""
    for line in chunk.decode("utf-8", errors="ignore").splitlines():
        if not line.startswith("data:") or '"usage"' not in line:
            continue
        try:
            data = json.loads(line[5:].strip())
        except ValueError:
            continue
        if isinstance(data, dict) and isinstance(data.get("usage"), dict):
            return data["usage"]
    return {}


_FINISH_RE = re.compile(rb'"finish_reason"\s*:\s*"[a-zA-Z_]')


def _has_finish_reason(chunk: bytes) -> bool:
    """块里是否带非 null 的 finish_reason(客户端拿到它即视为响应完整)。"""
    return b"finish_reason" in chunk and bool(_FINISH_RE.search(chunk))
