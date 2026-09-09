import hashlib
import json
import re
import time
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import ValidationError

from app.agent.status import render_status_context
from app.domain.llm_contracts import IntentSlots, RouteDecision
from app.settings import get_settings

# Explicit test override only. Environment-backed values are resolved from Settings at
# call time so importing this module never freezes runtime configuration.
DEEPSEEK_MODEL: str | None = None
PROMPT_VERSION = "deepseek-cost-prompts-v1"


def get_deepseek_model() -> str:
    return DEEPSEEK_MODEL or get_settings().deepseek_model


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _take_response_content(trace: dict[str, Any]) -> str:
    return str(trace.pop("_content"))


class LLMUnavailableError(RuntimeError):
    def __init__(self, message: str, trace: dict[str, Any] | None = None):
        super().__init__(message)
        self.trace = trace


def _api_key_state() -> dict[str, Any]:
    api_key = get_settings().deepseek_api_key.strip()
    if not api_key:
        return {
            "configured": False,
            "valid_shape": False,
            "reason": "missing",
            "message": "未配置 DEEPSEEK_API_KEY。",
        }
    looks_placeholder = bool(
        re.search(
            r"your|你的|example|placeholder|sk-your", api_key, flags=re.IGNORECASE
        )
    )
    if looks_placeholder:
        return {
            "configured": True,
            "valid_shape": False,
            "reason": "placeholder",
            "message": "DEEPSEEK_API_KEY 仍是示例占位值，请替换为真实 API Key。",
        }
    return {
        "configured": True,
        "valid_shape": True,
        "reason": "configured",
        "message": "DeepSeek API Key 已配置，尚未在状态接口中发起 live 请求。",
    }


def is_llm_configured() -> bool:
    state = _api_key_state()
    return bool(state["configured"] and state["valid_shape"])


def get_model_status() -> dict[str, Any]:
    settings = get_settings()
    model = get_deepseek_model()
    return {
        "provider": "deepseek",
        "model": model,
        "base_url": settings.deepseek_base_url.rstrip("/"),
        "thinking": settings.deepseek_thinking,
        "api_key_configured": _api_key_state()["configured"],
        "api_key_valid_shape": _api_key_state()["valid_shape"],
        "api_key_reason": _api_key_state()["reason"],
        "ready_for_real_call": is_llm_configured(),
        "status": "configured" if is_llm_configured() else _api_key_state()["reason"],
        "message": (
            "DeepSeek API Key 已配置，尚未执行实时连接测试。"
            if is_llm_configured()
            else _api_key_state()["message"]
        ),
    }


def test_model_connection() -> dict[str, Any]:
    started_at = time.perf_counter()
    settings = get_settings()
    model = get_deepseek_model()
    base_url = settings.deepseek_base_url.rstrip("/")

    def result(*, ok: bool, stage: str, message: str) -> dict[str, Any]:
        return {
            "ok": ok,
            "provider": "deepseek",
            "model": model,
            "stage": stage,
            "message": message,
            "duration_ms": round((time.perf_counter() - started_at) * 1000, 2),
            "tested_at": datetime.now(UTC),
        }

    key_state = _api_key_state()
    if not key_state["configured"] or not key_state["valid_shape"]:
        return result(
            ok=False,
            stage="configuration",
            message=key_state["message"],
        )

    headers = _deepseek_headers()
    current_stage = "models"
    try:
        with httpx.Client(timeout=settings.deepseek_timeout_seconds) as client:
            models_response = client.get(
                f"{base_url}/models",
                headers=headers,
            )
            models_response.raise_for_status()
            try:
                model_ids = {
                    item["id"]
                    for item in models_response.json().get("data", [])
                    if isinstance(item, dict) and isinstance(item.get("id"), str)
                }
            except (ValueError, AttributeError):
                return result(
                    ok=False,
                    stage="models",
                    message="DeepSeek 模型列表响应格式无效。",
                )

            if model not in model_ids:
                return result(
                    ok=False,
                    stage="models",
                    message=f"模型 {model} 不可用，请检查 DEEPSEEK_MODEL。",
                )

            current_stage = "completion"
            completion_response = client.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "Reply with exactly OK."}],
                    "temperature": 0,
                    "max_tokens": 8,
                    "thinking": {"type": settings.deepseek_thinking},
                },
            )
            completion_response.raise_for_status()
            try:
                choices = completion_response.json().get("choices", [])
            except (ValueError, AttributeError):
                choices = []
            if not choices:
                return result(
                    ok=False,
                    stage="completion",
                    message="DeepSeek 推理响应格式无效。",
                )
    except httpx.TimeoutException:
        return result(
            ok=False,
            stage=current_stage,
            message="DeepSeek 请求超时，请检查网络或超时配置。",
        )
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        if status_code in {401, 403}:
            message = "DeepSeek API Key 无效或无权限。"
        elif current_stage == "models":
            message = f"DeepSeek 模型列表请求失败：HTTP {status_code}。"
        else:
            message = f"DeepSeek 推理请求失败：HTTP {status_code}，请检查模型参数。"
        return result(ok=False, stage=current_stage, message=message)
    except httpx.HTTPError:
        return result(
            ok=False,
            stage=current_stage,
            message="无法连接 DeepSeek，请检查网络和 DEEPSEEK_BASE_URL。",
        )

    return result(
        ok=True,
        stage="completion",
        message=f"DeepSeek {model} 连接成功。",
    )


def _deepseek_headers() -> dict[str, str]:
    api_key = get_settings().deepseek_api_key
    key_state = _api_key_state()
    if not key_state["configured"]:
        raise LLMUnavailableError("缺少 DEEPSEEK_API_KEY。")
    if not key_state["valid_shape"]:
        raise LLMUnavailableError(str(key_state["message"]))
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _extract_json_object(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def extract_latest_period_from_question(question: str) -> str:
    compact_query = question.replace(" ", "")
    normalized_periods: list[str] = []
    year_month_matches = re.findall(
        r"(?<!\d)((?:20)?\d{2})年(1[0-2]|0?[1-9])月", compact_query
    )
    period_matches = re.findall(r"(?<!\d)((?:20)?\d{2})-(1[0-2]|0[1-9])", compact_query)

    if year_month_matches:
        normalized_periods.extend(
            f"{_normalize_year(year)}-{int(month):02d}"
            for year, month in year_month_matches
        )
        base_year = _normalize_year(year_month_matches[-1][0])
        shorthand_months = re.findall(r"[和与、到至](1[0-2]|0?[1-9])月", compact_query)
        normalized_periods.extend(
            f"{base_year}-{int(month):02d}" for month in shorthand_months
        )
    normalized_periods.extend(
        f"{_normalize_year(year)}-{month}" for year, month in period_matches
    )

    return max(normalized_periods, default="")


def extract_date_range_from_question(question: str) -> dict[str, str] | None:
    compact_query = question.replace(" ", "")
    dated_mentions: list[tuple[int, str]] = []
    year_anchors: list[tuple[int, str]] = []
    full_cn_pattern = re.compile(
        r"(?<!\d)((?:20)?\d{2})年(1[0-2]|0?[1-9])月([12]\d|3[01]|0?[1-9])日?"
    )
    dashed_pattern = re.compile(
        r"(?<!\d)((?:20)?\d{2})[-/](1[0-2]|0?[1-9])[-/]([12]\d|3[01]|0?[1-9])"
    )
    for pattern in (full_cn_pattern, dashed_pattern):
        for match in pattern.finditer(compact_query):
            year, month, day = match.groups()
            normalized_year = _normalize_year(year)
            dated_mentions.append(
                (
                    match.start(),
                    f"{normalized_year}-{int(month):02d}-{int(day):02d}",
                )
            )
            year_anchors.append((match.start(), normalized_year))

    shorthand_pattern = re.compile(
        r"[到至和与、~—-](1[0-2]|0?[1-9])月([12]\d|3[01]|0?[1-9])日?"
    )
    for match in shorthand_pattern.finditer(compact_query):
        prior_years = [
            year for position, year in year_anchors if position < match.start()
        ]
        if not prior_years:
            continue
        month, day = match.groups()
        dated_mentions.append(
            (
                match.start(),
                f"{prior_years[-1]}-{int(month):02d}-{int(day):02d}",
            )
        )

    ordered_dates = [
        date for _, date in sorted(dated_mentions, key=lambda item: item[0])
    ]
    if len(ordered_dates) == 1:
        # A single explicit day is still a bounded query. Treating it as the
        # whole month is especially misleading for daily production records.
        only_date = ordered_dates[0]
        return {"start_date": only_date, "end_date": only_date}
    if len(ordered_dates) < 2:
        return None
    return {"start_date": ordered_dates[0], "end_date": ordered_dates[-1]}


def _normalize_year(year: str) -> str:
    """Map two-digit year inputs to the 2000-2099 range."""
    return f"20{year}" if len(year) == 2 else year


def _chat_completion(
    node: str,
    purpose: str,
    messages: list[dict[str, str]],
    *,
    response_format: dict[str, str] | None = None,
    temperature: float = 0,
    max_tokens: int = 512,
) -> dict[str, Any]:
    started_at = time.perf_counter()
    settings = get_settings()
    model = get_deepseek_model()
    base_url = settings.deepseek_base_url.rstrip("/")
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "thinking": {"type": settings.deepseek_thinking},
    }
    if response_format is not None:
        payload["response_format"] = response_format

    trace: dict[str, Any] = {
        "node": node,
        "purpose": purpose,
        "provider": "deepseek",
        "model": model,
        "status": "started",
        "request_sha256": canonical_sha256(payload),
    }

    try:
        headers = _deepseek_headers()
        with httpx.Client(timeout=settings.deepseek_timeout_seconds) as client:
            response = client.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
    except LLMUnavailableError as exc:
        trace["status"] = "error"
        trace["error_code"] = "llm_unavailable"
        trace["duration_ms"] = round((time.perf_counter() - started_at) * 1000, 2)
        exc.trace = trace
        raise
    except httpx.HTTPStatusError as exc:
        trace["status"] = "error"
        trace["error_code"] = f"llm_http_{exc.response.status_code}"
        trace["duration_ms"] = round((time.perf_counter() - started_at) * 1000, 2)
        raise LLMUnavailableError(
            f"DeepSeek API 返回 HTTP {exc.response.status_code}。", trace
        ) from exc
    except httpx.HTTPError as exc:
        trace["status"] = "error"
        trace["error_code"] = "llm_transport_error"
        trace["duration_ms"] = round((time.perf_counter() - started_at) * 1000, 2)
        raise LLMUnavailableError("DeepSeek API 请求失败。", trace) from exc

    try:
        data = response.json()
        if not isinstance(data, dict):
            raise TypeError("invalid completion shape")
        choices = data.get("choices")
        if (
            not isinstance(choices, list)
            or not choices
            or not isinstance(choices[0], dict)
        ):
            raise TypeError("invalid completion shape")
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise TypeError("invalid completion shape")
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        trace["status"] = "error"
        trace["error_code"] = "llm_contract_invalid"
        trace["duration_ms"] = round((time.perf_counter() - started_at) * 1000, 2)
        raise LLMUnavailableError("DeepSeek 响应格式无效。", trace) from exc
    trace["status"] = "success"
    trace["duration_ms"] = round((time.perf_counter() - started_at) * 1000, 2)
    content = message.get("content") or ""
    reasoning_content = message.get("reasoning_content") or ""
    trace["response"] = {
        "finish_reason": data["choices"][0].get("finish_reason"),
        "usage": data.get("usage"),
        "response_sha256": canonical_sha256(
            {"content": content, "reasoning_content": reasoning_content}
        ),
    }
    trace["_content"] = content
    return trace


def parse_cost_question_with_llm(
    question: str,
    status_bar: dict[str, Any] | None = None,
    conversation_context: dict[str, Any] | None = None,
    recent_messages: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "你是制造业成本 Agent 的问题理解节点。只输出 JSON，不要输出解释。"
                "字段必须包含 intent、product_text、period、start_date、end_date。"
                "intent 只能是 cost_query、cost_breakdown、variance_analysis、unknown。"
                "period 使用 YYYY-MM；如果用户提到多个期间，period 使用较晚的目标期间。"
                "如果用户给出具体日期范围，start_date 和 end_date 使用 YYYY-MM-DD；"
                "不能识别则用空字符串。"
                "product_text 保留用户提到的产品名称，例如 产品A。不要生成成本金额。"
            ),
        },
        {"role": "user", "content": question},
    ]
    if conversation_context or recent_messages:
        messages.append(
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "context_note": (
                            "以下内容只用于理解省略表达，不是权限授予或金额事实来源。"
                        ),
                        "conversation_slots": conversation_context or {},
                        "recent_messages": (recent_messages or [])[-6:],
                    },
                    ensure_ascii=False,
                ),
            }
        )
    if status_bar:
        messages.append({"role": "user", "content": render_status_context(status_bar)})

    trace = _chat_completion(
        "understand_question",
        "使用 DeepSeek 从自然语言问题中提取意图、产品和期间。",
        messages,
        response_format={"type": "json_object"},
        temperature=0,
        max_tokens=256,
    )
    content = _take_response_content(trace)
    try:
        parsed = _extract_json_object(content)
        slots = IntentSlots.model_validate(parsed)
    except (json.JSONDecodeError, ValidationError, ValueError) as exc:
        trace["status"] = "error"
        trace["error_code"] = "llm_contract_invalid"
        raise LLMUnavailableError(
            "DeepSeek 返回内容不符合意图槽位契约。", trace
        ) from exc
    normalized_period = extract_latest_period_from_question(question)
    date_range = extract_date_range_from_question(question)
    return {
        "intent": slots.intent,
        "product_text": slots.product_text,
        "period": (
            date_range["end_date"][:7]
            if date_range
            else normalized_period or slots.period
        ),
        # Date ranges are accepted only when they are explicitly present in the
        # user query. This keeps a model from turning "May and June" into a
        # daily range and preserves the deterministic date-range boundary.
        "date_range": date_range,
        "model": get_deepseek_model(),
        "llm_call": trace,
    }


def classify_route_with_llm(
    question: str,
    candidate_routes: list[str],
    status_bar: dict[str, Any] | None = None,
    conversation_context: dict[str, Any] | None = None,
    recent_messages: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Classify an ambiguous request into a server-allowed route.

    The model may suggest only a route from ``candidate_routes``. The caller still
    validates the result before using it for graph routing.
    """

    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "你是制造业成本 Agent 的路由分类节点。只输出 JSON，不要输出解释。"
                "route 只能从 candidate_routes 中选择；如果无法确定，选择 system_help。"
                "字段必须包含 route、confidence、reason。"
                "cost_calculation 只用于产品成本、单位成本、成本构成、成本核算、工序成本或成本报表问题；"
                "system_help 用于系统能力、流程、使用方式和一般说明。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {"question": question, "candidate_routes": candidate_routes},
                ensure_ascii=False,
            ),
        },
    ]
    if conversation_context or recent_messages:
        messages.append(
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "conversation_slots": conversation_context or {},
                        "recent_messages": (recent_messages or [])[-6:],
                    },
                    ensure_ascii=False,
                ),
            }
        )
    if status_bar:
        messages.append({"role": "user", "content": render_status_context(status_bar)})

    trace = _chat_completion(
        "select_route",
        "使用 DeepSeek 在服务端允许的能力范围内选择 Agent 路由。",
        messages,
        response_format={"type": "json_object"},
        temperature=0,
        max_tokens=160,
    )
    content = _take_response_content(trace)
    try:
        parsed = _extract_json_object(content)
        decision = RouteDecision.model_validate(parsed)
    except (json.JSONDecodeError, ValidationError, ValueError) as exc:
        trace["status"] = "error"
        trace["error_code"] = "llm_route_contract_invalid"
        raise LLMUnavailableError("DeepSeek 路由结果不符合结构化契约。", trace) from exc

    route = decision.route
    if route not in candidate_routes:
        trace["status"] = "error"
        trace["error_code"] = "llm_route_not_allowed"
        raise LLMUnavailableError("DeepSeek 返回了未允许的路由。", trace)

    return {
        "route": route,
        "confidence": decision.confidence,
        "reason": "模型在服务端允许的路由集合内完成分类。",
        "llm_call": trace,
    }


def generate_cost_analysis_with_llm(
    product: dict[str, Any],
    period: str,
    calculation_result: dict[str, Any],
    comparison_result: dict[str, Any] | None = None,
    date_range: dict[str, str] | None = None,
    status_bar: dict[str, Any] | None = None,
) -> dict[str, Any]:
    facts = {
        "product_name": product["product_name"],
        "period": period,
        "output_qty": calculation_result["output_qty"],
        "total_cost": calculation_result["total_cost"],
        "unit_cost": calculation_result["unit_cost"],
        "process_breakdown": calculation_result["process_breakdown"],
        "cost_composition": calculation_result["cost_composition"],
        "comparison": comparison_result,
        "date_range": date_range,
    }
    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "你是制造业成本分析助手。只能基于用户提供的 JSON 事实写一段中文分析。"
                "不要新增任何金额、产量、期间或产品。不要说数据来自真实 ERP。"
                "如果 comparison 不为空，说明单位成本变化和主要变化工序。"
                "如果 date_range 不为空，说明这是按日级记录精确汇总的区间核算。"
                "长度控制在 100 字以内。"
            ),
        },
        {"role": "user", "content": json.dumps(facts, ensure_ascii=False)},
    ]
    if status_bar:
        messages.append({"role": "user", "content": render_status_context(status_bar)})

    trace = _chat_completion(
        "build_report_json",
        "使用 DeepSeek 基于确定性计算结果生成业务分析文字。",
        messages,
        temperature=0.2,
        max_tokens=220,
    )
    content = _take_response_content(trace).strip()
    if not content:
        trace["status"] = "error"
        trace["error_code"] = "llm_contract_invalid"
        raise LLMUnavailableError("DeepSeek 未返回分析文本。", trace)
    trace["input_facts_sha256"] = canonical_sha256(facts)
    return {
        "analysis_text": content,
        "llm_call": trace,
    }
