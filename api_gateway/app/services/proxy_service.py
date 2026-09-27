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
from app.models.llm_model import LLMModel, ModelType
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


# ---------------------------------------------------------------- 客户端来源

# 常见的客户端工具 UA 特征(不区分大小写);命中即显示友好名,否则展示截断的原始 UA
_SOURCE_PATTERNS: list[tuple[str, str]] = [
    (r"claude[- ]?code|claude-cli", "Claude Code"),
    (r"claude\.ai", "Claude.ai"),
    (r"claude", "Claude"),
    (r"codex", "Codex"),
    (r"cursor", "Cursor"),
    (r"aider", "Aider"),
    (r"openai[-_ ]?(sdk|python|node)", "OpenAI SDK"),
    (r"langchain", "LangChain"),
    (r"llama[-_ ]?index", "LlamaIndex"),
    (r"python-requests", "Python Requests"),
    (r"httpx", "HTTPX"),
    (r"curl", "cURL"),
    (r"node-fetch|undici|axios", "Node 客户端"),
]

_SOURCE_FALLBACK_LEN = 64  # 未识别 UA 的展示截断长度


def normalize_source(ua: str | None) -> str | None:
    """从 User-Agent 解析客户端工具名;识别不出返回截断的原始 UA,空 UA 返回 None。"""
    if not ua or not ua.strip():
        return None
    for pattern, label in _SOURCE_PATTERNS:
        if re.search(pattern, ua, re.IGNORECASE):
            return label
    return ua.strip()[:_SOURCE_FALLBACK_LEN]


# ---------------------------------------------------------------- 消息规范化

# codex 等客户端会把 Responses API 的内容段类型(input_text/output_text)混进
# chat/completions 请求;llama-server 严格校验 content[].type,不认的类型直接 400
_CONTENT_PART_ALIAS = {"input_text": "text", "output_text": "text"}


def normalize_messages(messages: list[dict]) -> list[dict]:
    """把 Responses 风格的内容段类型转换为 chat 风格(input_text→text 等)。

    无需改动的消息保持原对象,避免大 payload 的无谓深拷贝。
    """
    out: list[dict] = []
    changed = False
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            new_parts = []
            msg_changed = False
            for part in content:
                if isinstance(part, dict) and part.get("type") in _CONTENT_PART_ALIAS:
                    part = {**part, "type": _CONTENT_PART_ALIAS[part["type"]]}
                    msg_changed = True
                new_parts.append(part)
            if msg_changed:
                msg = {**msg, "content": new_parts}
                changed = True
        out.append(msg)
    return out if changed else messages


# ---------------------------------------------------------------- 上下文溢出回退

# 上游按真实 token 数拒绝时的识别特征(估算偏低会漏过窗口护栏,只有上游的
# 计数才是事实):llama-server / OpenAI 系各自的文案与错误码
_OVERFLOW_PATTERNS = (
    "exceeds the available context size",  # llama-server
    "context_length_exceeded",  # OpenAI 系错误码
    "maximum context length",  # OpenAI 系文案
)


def _is_context_overflow(status_code: int, body: str) -> bool:
    """上游错误是否为"超出上下文窗口"——唯一值得自动换模型重试的 4xx。"""
    if status_code not in (400, 413, 422):
        return False
    low = body.lower()
    return any(p in low for p in _OVERFLOW_PATTERNS)


def _pick_overflow_fallback(chain: list[LLMModel], current: LLMModel) -> LLMModel | None:
    """从策略溢出回退链取下一个候选:跳过当前模型、熔断开路、窗口不比当前大的。

    超窗意味着需要更大的窗口,同/更小窗口的下一档必然同样被拒,跳过省一次往返。
    """
    for candidate in chain:
        if candidate.id == current.id:
            continue
        if candidate.context_window <= current.context_window:
            continue
        if get_breaker(candidate.id).is_open:
            continue
        return candidate
    return None


async def _context_overflow_fallback(failed: LLMModel) -> LLMModel | None:
    """上下文溢出兜底:换窗口最大的启用模型。

    用独立会话(流式路径里请求级会话生命周期不可靠);
    没有更大窗口的模型可换时返回 None,原错误照常透传。
    """
    async with async_session_factory() as session:
        stmt = (
            select(LLMModel)
            .where(LLMModel.is_enabled.is_(True), LLMModel.id != failed.id)
            .order_by(LLMModel.context_window.desc(), LLMModel.priority, LLMModel.id)
        )
        candidate = (await session.execute(stmt)).scalars().first()
        if candidate is not None and candidate.context_window <= failed.context_window:
            return None
        return candidate


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


async def _log_prepare_cancel(
    req: ChatCompletionRequest, start: float, source: str | None
) -> None:
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
        source=source,
    )


async def _prepare(
    db: AsyncSession, req: ChatCompletionRequest, source: str | None
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
        task = asyncio.create_task(_log_prepare_cancel(req, start, source))
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
    source: str | None = None  # 客户端工具名(由路由层从 UA 解析后传入)
    # 是否已提交过日志:由 write_log/_log_in_background 在派发时置位,
    # 取消路径据此避免重复补记(同一条请求日志写两次)
    logged: bool = False


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
        "source": ctx.source,
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
    ctx.logged = True  # 派发即置位:即使随后被取消,取消路径也不会重复补记
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


def _log_in_background(
    ctx: LogContext,
    *,
    status: str,
    error: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
) -> None:
    ctx.logged = True
    latency = int((time.perf_counter() - ctx.start) * 1000)
    task = asyncio.create_task(
        _insert_log(
            **_log_fields(
                ctx,
                status=status,
                latency_ms=latency,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                error=error,
            )
        )
    )
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_on_log_task_done)


# ---------------------------------------------------------------- 非流式


async def chat_completion(
    db: AsyncSession, req: ChatCompletionRequest, source: str | None = None
) -> tuple[int, dict]:
    """非流式转发,返回 (HTTP 状态码, 响应体)。"""
    _, decision, compression = await _prepare(db, req, source)
    model = decision.model
    payload = req.to_upstream_payload(model.model_id)
    payload["messages"] = normalize_messages(compression.messages)

    ctx = LogContext(
        request_id=uuid.uuid4().hex[:12],
        model=model,
        route=decision.route,
        confidence=decision.confidence,
        route_reason=decision.reason,
        compression=compression,
        excerpt=_request_excerpt(req.messages),
        start=time.perf_counter(),
        source=source,
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
    overflow_fallback_tried = False  # 上下文溢出换模型只试一次,防循环
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
                # 上游按真实 token 数拒绝(估算偏低漏过护栏):先沿策略区间链
                # 回退(下一档才是用户意图),链空再全局挑窗口最大的启用模型
                if _is_context_overflow(resp.status_code, resp.text) and (
                    not overflow_fallback_tried
                ):
                    fallback = _pick_overflow_fallback(decision.fallback_models, model)
                    if fallback is None:
                        fallback = await _context_overflow_fallback(model)
                    if fallback is not None and not get_breaker(fallback.id).is_open:
                        overflow_fallback_tried = True
                        ctx.route_reason += (
                            f";{model.name} 实际超出上下文窗口,回退 {fallback.name}"
                        )
                        model = fallback
                        payload = {**payload, "model": fallback.model_id}
                        ctx.model = fallback
                        ctx.route = (
                            "local" if fallback.type == ModelType.LOCAL else "cloud"
                        )
                        breaker = get_breaker(fallback.id)
                        continue
                # 熔断按逻辑请求计数(重试不重复计数);4xx(429 除外)是
                # 客户端/payload 问题,不代表上游不健康,不计熔断——
                # 否则个别坏请求会把后续好请求全挡掉
                if not (400 <= resp.status_code < 500 and resp.status_code != 429):
                    breaker.record_failure()
                # 错误体截断入日志:4xx 的具体原因(如 unsupported content[].type)
                # 只在上游响应里,不带正文日志就是"upstream HTTP 400"一句废话
                await write_log(
                    ctx,
                    status="error",
                    latency_ms=latency,
                    error=f"upstream HTTP {resp.status_code}: {resp.text[:300]}",
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
        # 客户端中断(取消/重连):不算上游故障,不计熔断。
        # 取消若落在 write_log 的 shield 等待上,日志其实已派发成功,
        # 这里补记会造成同一条请求两行日志——以 ctx.logged 判重
        if not ctx.logged:
            _log_in_background(ctx, status="cancelled", error="客户端中断连接")
        raise


# ---------------------------------------------------------------- 流式


@dataclass
class StreamContext:
    """流式请求的准备结果:路由/压缩/熔断检查全部完成后的转发上下文。"""

    model: LLMModel
    payload: dict
    log: LogContext
    fallback_models: list[LLMModel]  # 溢出回退链(来自策略区间,可能为空)


async def prepare_stream(
    db: AsyncSession, req: ChatCompletionRequest, source: str | None = None
) -> StreamContext:
    """流式转发的准备阶段:压缩 → 路由 → 熔断检查。

    必须在 StreamingResponse 建立之前 await,此阶段的异常(如无可用模型)
    才能以正常的 JSON 错误响应返回;一旦流开始,错误只能以 SSE 事件表达。
    """
    _, decision, compression = await _prepare(db, req, source)
    model = decision.model
    payload = req.to_upstream_payload(model.model_id)
    payload["messages"] = normalize_messages(compression.messages)
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
        source=source,
    )
    breaker = get_breaker(model.id)
    if breaker.is_open:
        _log_in_background(ctx, status="error", error="熔断器开路,请求被拒绝")
        raise AppError(
            message=f"模型 {model.name} 连续失败已熔断,请稍后再试",
            status_code=503,
            code="CIRCUIT_OPEN",
        )
    return StreamContext(
        model=model, payload=payload, log=ctx, fallback_models=decision.fallback_models
    )


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
    finish_seen = False  # 客户端是否已拿到完整响应(非 null finish_reason)
    overflow_fallback_tried = False  # 上下文溢出换模型只试一次,防循环

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
                # _log_in_background 派发即置位 ctx.logged:
                # 客户端拿到 finish_reason 后随时断开,取消路径不会重复补记
                _log_in_background(
                    ctx,
                    status="success",
                    prompt_tokens=usage.get("prompt_tokens"),
                    completion_tokens=usage.get("completion_tokens"),
                )
                return
            except (llm_client.UpstreamError, httpx.HTTPError) as exc:
                last_exc = exc
                if stream_started:
                    # 流已开始,不能重试:以 SSE 错误事件收尾
                    break
                # 流未开始时的上下文溢出:先沿策略区间链回退,链空再全局
                # 挑窗口最大的启用模型
                if (
                    isinstance(exc, llm_client.UpstreamError)
                    and not overflow_fallback_tried
                    and _is_context_overflow(
                        exc.status_code, exc.body.decode("utf-8", errors="ignore")
                    )
                ):
                    fallback = _pick_overflow_fallback(sc.fallback_models, model)
                    if fallback is None:
                        fallback = await _context_overflow_fallback(model)
                    if fallback is not None and not get_breaker(fallback.id).is_open:
                        overflow_fallback_tried = True
                        ctx.route_reason += (
                            f";{model.name} 实际超出上下文窗口,回退 {fallback.name}"
                        )
                        model = fallback
                        payload = {**payload, "model": fallback.model_id}
                        ctx.model = fallback
                        ctx.route = (
                            "local" if fallback.type == ModelType.LOCAL else "cloud"
                        )
                        breaker = get_breaker(fallback.id)
                        continue
                if isinstance(exc, llm_client.UpstreamError) and (
                    exc.status_code not in _RETRYABLE_STATUS
                    or attempt >= settings.llm_max_retries
                ):
                    # 不可重试的上游错误:透传状态码与错误体;
                    # 4xx 属 payload 问题不计熔断(429 在 _RETRYABLE_STATUS 里,到不了这)
                    if not 400 <= exc.status_code < 500:
                        breaker.record_failure()
                    body_preview = exc.body[:300].decode("utf-8", errors="ignore")
                    _log_in_background(
                        ctx,
                        status="error",
                        error=f"upstream HTTP {exc.status_code}: {body_preview}",
                    )
                    yield _sse_raw(exc.body)
                    return
                if attempt < settings.llm_max_retries:
                    await asyncio.sleep(settings.llm_retry_backoff * (2**attempt))
                continue

        breaker.record_failure()
        # repr:httpx 超时异常的 str 是空串,直接插值日志里只剩"流式调用失败: "
        _log_in_background(ctx, status="error", error=f"流式调用失败: {last_exc!r}")
        yield _sse_error(f"流式调用失败(已重试 {settings.llm_max_retries} 次): {last_exc!r}", "STREAM_FAILED")
    finally:
        if not ctx.logged:
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
