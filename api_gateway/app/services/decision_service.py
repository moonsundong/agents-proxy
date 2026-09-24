"""决策路由服务(文档模块 5):决策模型评估 → 简单度评分 → 梯度区间路由。

所有请求一律走策略路由(用户决策,废弃了文档中的手动覆盖/二元阈值/A-B 设计):
1. 命中启用策略 → 决策模型评估(压缩后的消息)给出简单度评分,
   按 decision_tiers 梯度区间路由(下限从高到低匹配,档位模型停用或
   上下文装不下时顺延下一档);
2. 无策略 → 按模型名匹配选择,匹配不上用默认模型兜底;
   决策失败/未配置 → 保守走最强档(最低区间),再退默认模型,原因写日志。
"""

import json
import re
from dataclasses import dataclass

import httpx
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients import llm_client
from app.config.settings import settings
from app.models.llm_model import LLMModel, ModelType
from app.models.routing_policy import RoutingPolicy
from app.schemas.routing import DecisionInfo
from app.services import routing_service
from app.utils.exceptions import AppError

_DECISION_SYSTEM_PROMPT = """你是一个请求复杂度评估器,负责给请求的简单度打分,网关据此把请求路由到不同能力档位的模型(简单请求用弱模型省成本,复杂请求用强模型保质量)。

评估用户请求的简单度,只输出 JSON,不要输出其他内容:
{"confidence": 0.0到1.0, "complexity": "simple|medium|hard", "reason": "一句话原因"}

confidence 含义:该请求简单到可以由能力较弱的小模型独立高质量完成的确信度。
- 简单问答、常见代码片段、文本改写翻译 → 0.8~1.0
- 中等复杂度(多步骤推理、较大的代码改造)→ 0.4~0.7
- 高复杂度(大型架构设计、罕见领域知识、需要最新信息)→ 0.0~0.4"""

# 决策输入采样上限/头尾占比的默认值:与 RoutingPolicy 表默认值保持一致,
# 仅作为策略未显式配置时的兜底(正常路径都读策略配置)
_DEFAULT_CONTENT_LIMIT = 8000
_DEFAULT_HEAD_RATIO = 0.2

# 字符/token 保守换算:中文约 1 token/字,取 1.5 保证截断后不溢出窗口
_CHARS_PER_TOKEN = 1.5

# 决策输出 token 上限的兜底:推理型模型(reasoning_content)会先消耗预算再
# 输出正文,小上限会导致正文被截断/为空,评估失败保守走线上。
# 实际值读 settings.decision_max_tokens(默认 4096),此常量仅作导入期兜底
_DECISION_MAX_TOKENS_FALLBACK = 1024

# 决策请求里 system prompt + 消息模板的 token 预留
_DECISION_PROMPT_RESERVE = 512

# 上下文窗口护栏的预留空间:给输出 token 和模板开销留余量
_CONTEXT_RESERVE = 1024


@dataclass
class RouteDecision:
    """一次路由解析的结果。"""

    model: LLMModel
    route: str  # local / cloud / manual
    confidence: float | None
    reason: str


def _route_of(model: LLMModel) -> str:
    return "local" if model.type == ModelType.LOCAL else "cloud"


async def _default_model(db: AsyncSession) -> LLMModel:
    stmt = select(LLMModel).where(
        LLMModel.is_enabled.is_(True), LLMModel.is_default.is_(True)
    )
    model = (await db.execute(stmt)).scalars().first()
    if model is None:
        raise AppError(
            message="没有可用的模型:未配置默认模型",
            status_code=503,
            code="NO_AVAILABLE_MODEL",
        )
    return model


async def _enabled_model_or_none(db: AsyncSession, model_id: int | None) -> LLMModel | None:
    if model_id is None:
        return None
    model = await db.get(LLMModel, model_id)
    if model is None or not model.is_enabled:
        return None
    return model


# ---------------------------------------------------------------- 决策评估


def _content_limit_for(model: LLMModel, policy: RoutingPolicy | None = None) -> int:
    """决策内容的字符截断上限。

    安全上限 = (context_window - 输出预算 - prompt 预留) × 保守字符/token 比,
    保证任何窗口的决策模型都装得下。本地决策模型再叠加策略配置的采样上限
    (全量评估大上下文会让评估本身成为分钟级瓶颈);线上模型评估快,
    直接按窗口安全上限放行,看清完整上下文才能评得准。
    """
    max_tokens = getattr(settings, "decision_max_tokens", _DECISION_MAX_TOKENS_FALLBACK)
    safe_tokens = model.context_window - max_tokens - _DECISION_PROMPT_RESERVE
    safe_limit = int(safe_tokens * _CHARS_PER_TOKEN)
    if model.type == ModelType.LOCAL:
        cap = policy.decision_content_limit if policy else _DEFAULT_CONTENT_LIMIT
        return max(500, min(cap, safe_limit))
    return max(500, safe_limit)


def _sample_head_tail(text: str, limit: int, head_ratio: float) -> str:
    """超长内容按头部+尾部采样:尾部是最新用户消息,复杂度判断主要看它。"""
    if len(text) <= limit:
        return text
    head = int(limit * head_ratio)
    tail = limit - head
    marker = f"\n...[中间省略 {len(text) - limit} 字符]...\n"
    return text[:head] + marker + text[-tail:]


def _build_decision_payload(
    model: LLMModel, messages: list[dict], policy: RoutingPolicy | None = None
) -> dict:
    serialized = json.dumps(messages, ensure_ascii=False)
    sampled = _sample_head_tail(
        serialized,
        _content_limit_for(model, policy),
        policy.decision_head_ratio if policy else _DEFAULT_HEAD_RATIO,
    )
    payload = {
        "model": model.model_id,
        "messages": [
            {"role": "system", "content": _DECISION_SYSTEM_PROMPT},
            {"role": "user", "content": f"请评估以下请求:\n{sampled}"},
        ],
        "temperature": 0,
        "max_tokens": getattr(settings, "decision_max_tokens", _DECISION_MAX_TOKENS_FALLBACK),
        "stream": False,
    }
    return payload


def _parse_decision(content: str) -> DecisionInfo | None:
    """从决策模型输出中解析 JSON;宽容处理前后多余文本与截断。

    推理型模型的输出预算常被思考耗尽,JSON 可能写到一半被截断(没有右括号),
    此时 confidence 字段往往已经写完——用正则单独打捞,避免整次评估作废。
    """
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(0))
            confidence = float(data["confidence"])
        except (ValueError, KeyError, TypeError):
            confidence = None
        if confidence is not None:
            return DecisionInfo(
                confidence=max(0.0, min(1.0, confidence)),
                complexity=data.get("complexity"),
                reason=data.get("reason"),
            )
    # 截断挽救:JSON 不完整,但 confidence 字段可能已完整输出
    salvage = re.search(r'"confidence"\s*:\s*([0-9]*\.?[0-9]+)', content)
    if salvage:
        confidence = float(salvage.group(1))
        return DecisionInfo(
            confidence=max(0.0, min(1.0, confidence)),
            reason="(输出被截断,confidence 由正则打捞)",
        )
    return None


async def evaluate_complexity(
    decision_model: LLMModel, messages: list[dict], policy: RoutingPolicy | None = None
) -> DecisionInfo | None:
    """调用决策模型评估请求;任何失败(网络/解析)返回 None 由上层降级。"""
    payload = _build_decision_payload(decision_model, messages, policy)
    # 决策用专用超时:本地推理型模型评估要先"思考",默认 request_timeout 不够
    timeout = httpx.Timeout(settings.decision_timeout)
    try:
        resp = await llm_client.chat_completion(decision_model, payload, timeout=timeout)
        if resp.status_code != 200:
            logger.warning("决策模型返回 HTTP {}", resp.status_code)
            return None
        message = resp.json()["choices"][0]["message"]
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        # 注意用 repr:httpx 的超时异常 str() 是空串,直接插值会打出空日志
        logger.warning("决策模型调用失败: {!r}", exc)
        return None
    # 推理型模型可能把 JSON 留在 reasoning_content 而 content 为空(预算被思考耗尽)
    info = _parse_decision(message.get("content") or "")
    if info is None:
        info = _parse_decision(message.get("reasoning_content") or "")
    if info is None:
        logger.warning(
            "决策模型输出无法解析: {}", (message.get("content") or "")[:200]
        )
    return info


# ---------------------------------------------------------------- 路由解析


async def _resolve_by_tiers(
    db: AsyncSession,
    policy: RoutingPolicy,
    info: DecisionInfo,
    prompt_tokens: int | None,
) -> RouteDecision:
    """置信度梯度路由:区间按下限从高到低,取第一个 confidence >= 下限的区间。

    选中区间的模型不可用时顺延到下一个区间;压缩后的 prompt 超出区间模型
    上下文窗口时同样顺延(窗口是物理约束,置信度再高也装不下)。
    所有区间都不命中(置信度低于最低下限)时保守回退默认模型。
    """
    tiers = sorted(
        policy.decision_tiers or [],
        key=lambda t: t["min_confidence"],
        reverse=True,
    )
    detail = info.reason or info.complexity or "无"
    skipped: list[str] = []
    for tier in tiers:
        low = float(tier["min_confidence"])
        if info.confidence < low:
            continue
        model = await _enabled_model_or_none(db, tier.get("model_id"))
        if model is None:
            skipped.append(f"区间 ≥{low:.2f} 的模型不可用")
            continue
        if (
            prompt_tokens is not None
            and prompt_tokens > model.context_window - _CONTEXT_RESERVE
        ):
            skipped.append(
                f"{model.name} 窗口 {model.context_window} 装不下约 {prompt_tokens} tokens"
            )
            continue
        suffix = f";已跳过: {';'.join(skipped)}" if skipped else ""
        return RouteDecision(
            model=model,
            route=_route_of(model),
            confidence=info.confidence,
            reason=(
                f"置信度 {info.confidence:.2f} 命中区间 ≥{low:.2f} → {model.name}"
                f"({detail}){suffix}"
            ),
        )
    default = await _default_model(db)
    suffix = f";已跳过: {';'.join(skipped)}" if skipped else ""
    return RouteDecision(
        model=default,
        route=_route_of(default),
        confidence=info.confidence,
        reason=(
            f"置信度 {info.confidence:.2f} 低于所有区间下限({detail}),回退默认模型{suffix}"
        ),
    )


async def _strongest_tier_model(
    db: AsyncSession,
    tiers: list[dict],
    prompt_tokens: int | None,
) -> LLMModel | None:
    """最低置信度区间(最强档)的可用模型:决策不可用时的保守兜底。

    窗口装不下的档跳过(装不下就是装不下),继续往下找。
    """
    for tier in sorted(tiers, key=lambda t: t["min_confidence"]):
        model = await _enabled_model_or_none(db, tier.get("model_id"))
        if model is None:
            continue
        if (
            prompt_tokens is not None
            and prompt_tokens > model.context_window - _CONTEXT_RESERVE
        ):
            continue
        return model
    return None


async def _resolve_by_policy(
    db: AsyncSession,
    policy: RoutingPolicy,
    messages: list[dict],
    prompt_tokens: int | None = None,
) -> RouteDecision:
    """策略路由 = 决策模型评估 + 置信度梯度区间(旧二元阈值/A-B 设计已废弃)。

    兜底原则:配置缺决策模型或决策调用失败时,保守走最强档(下限最低的
    区间),最强档不可用再回退全局默认模型。
    """
    tiers = policy.decision_tiers or []
    if not tiers:
        default = await _default_model(db)
        return RouteDecision(
            model=default,
            route=_route_of(default),
            confidence=None,
            reason="策略未配置置信度区间,回退默认模型",
        )

    decision_model = await _enabled_model_or_none(db, policy.decision_model_id)
    if decision_model is None:
        fallback = await _strongest_tier_model(db, tiers, prompt_tokens)
        if fallback is not None:
            return RouteDecision(
                model=fallback,
                route=_route_of(fallback),
                confidence=None,
                reason=f"策略未配置决策模型,保守走最强档 {fallback.name}",
            )
        default = await _default_model(db)
        return RouteDecision(
            model=default,
            route=_route_of(default),
            confidence=None,
            reason="策略未配置决策模型且最强档不可用,回退默认模型",
        )

    info = await evaluate_complexity(decision_model, messages, policy)
    if info is None:
        # 决策失败:保守走最强档保证质量,不可用则回退默认
        fallback = await _strongest_tier_model(db, tiers, prompt_tokens)
        if fallback is not None:
            return RouteDecision(
                model=fallback,
                route=_route_of(fallback),
                confidence=None,
                reason=f"决策模型不可用,保守走最强档 {fallback.name}",
            )
        default = await _default_model(db)
        return RouteDecision(
            model=default,
            route=_route_of(default),
            confidence=None,
            reason="决策模型不可用且最强档不可用,回退默认模型",
        )

    return await _resolve_by_tiers(db, policy, info, prompt_tokens)


async def resolve_route(
    db: AsyncSession,
    requested_model: str | None,
    scenario: str,
    messages: list[dict],
    prompt_tokens: int | None = None,
) -> RouteDecision:
    """路由解析入口:上下文护栏 > A/B 分流 > 决策路由 > 默认模型。

    所有请求一律走策略路由,请求的 model 字段不触发手动覆盖:
    - 有命中策略时,model 字段完全被忽略(仅在路由原因中备注);
    - prompt_tokens(压缩后的 token 估计)超出本地模型窗口时直接走线上;
    - 没有配置任何策略时,才按模型名匹配选择模型(此时无策略可走,
      按名选择是唯一合理行为),匹配不上用默认模型。
    """
    stmt = select(LLMModel).where(LLMModel.is_enabled.is_(True))
    candidates = list((await db.execute(stmt)).scalars().all())
    matched = next(
        (m for m in candidates if requested_model in (m.name, m.model_id)),
        None,
    )

    # 1/2. 策略路由
    policy = await routing_service.find_active_policy(db, scenario)
    if policy is not None:
        decision = await _resolve_by_policy(db, policy, messages, prompt_tokens)
        if requested_model:
            note = (
                f"请求模型 {requested_model} 已匹配 {matched.name},按策略路由;"
                if matched
                else f"请求模型 {requested_model} 未匹配已配置模型(不影响,仍按策略路由);"
            )
            decision.reason = note + decision.reason
        return decision

    # 3. 无策略:按模型名匹配选择,匹配不上用默认模型
    if matched is not None:
        return RouteDecision(
            model=matched,
            route=_route_of(matched),
            confidence=None,
            reason="按模型名匹配(未配置路由策略)",
        )
    default = await _default_model(db)
    note = f"请求模型 {requested_model} 未匹配;" if requested_model else ""
    return RouteDecision(
        model=default,
        route=_route_of(default),
        confidence=None,
        reason=f"{note}兜底默认模型(无命中路由策略)",
    )
