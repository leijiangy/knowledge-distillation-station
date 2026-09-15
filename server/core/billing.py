# -*- coding: utf-8 -*-
"""积分计费的计算、契约校验与薄数据库编排。

用户生成按响应 usage.total_tokens 计费，固定 1 Token = 1 积分。调用层负责
持久化计费策略、钱包锁和状态变更；纯函数不读取环境变量或访问外部网络。

金额单位：
- 钱包：微积分，1 积分 = 1,000,000 微积分（int64）
- 充值/退款：人民币分（int64）
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo


MICROCREDITS_PER_CREDIT = 1_000_000
MICROCREDITS_PER_TOKEN = MICROCREDITS_PER_CREDIT
FEN_PER_CNY = 100
MAX_BIGINT = 9_223_372_036_854_775_807

QUOTE_TTL_SECONDS = 60
MAX_QUEUE_SECONDS = 60
DISPATCH_DEADLINE_SECONDS = 240

SUPPORTED_ACTIONS = frozenset({"init", "explain", "ask", "advanced"})
BILLING_RULE = "total_tokens_1_to_1"
STANDARD_DAILY_TOKENS = 50_000
PREMIUM_DAILY_TOKENS = 500_000
PREMIUM_MONTHLY_PRICE_FEN = 1_990
DAILY_RESET_HOUR = 4
QUOTA_TIMEZONE = ZoneInfo("Asia/Shanghai")


class BillingError(ValueError):
    """上层可稳定映射成 HTTP/RPC error_code 的纯核心错误。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class BillingConfigError(BillingError):
    def __init__(self, message: str):
        super().__init__("BILLING_CONFIG_UNAVAILABLE", message)


class BillingContractError(BillingError):
    def __init__(self, message: str):
        super().__init__("BILLING_CONTRACT_MISMATCH", message)


class OperationStatus(StrEnum):
    QUOTED = "quoted"
    RESERVED = "reserved"
    DISPATCHED = "dispatched"
    RESULT_RECORDED = "result_recorded"
    SETTLED = "settled"
    CACHE_HIT = "cache_hit"
    QUOTE_EXPIRED = "quote_expired"
    WAIVED_NOT_DISPATCHED = "waived_not_dispatched"
    WAIVED_FAILURE = "waived_failure"
    WAIVED_UNKNOWN = "waived_unknown"


TERMINAL_OPERATION_STATUSES = frozenset({
    OperationStatus.SETTLED,
    OperationStatus.CACHE_HIT,
    OperationStatus.QUOTE_EXPIRED,
    OperationStatus.WAIVED_NOT_DISPATCHED,
    OperationStatus.WAIVED_FAILURE,
    OperationStatus.WAIVED_UNKNOWN,
})

HELD_OPERATION_STATUSES = frozenset({
    OperationStatus.RESERVED,
    OperationStatus.DISPATCHED,
    OperationStatus.RESULT_RECORDED,
})

_ALLOWED_TRANSITIONS = {
    OperationStatus.QUOTED: frozenset({
        OperationStatus.RESERVED,
        OperationStatus.CACHE_HIT,
        OperationStatus.QUOTE_EXPIRED,
    }),
    OperationStatus.RESERVED: frozenset({
        OperationStatus.DISPATCHED,
        OperationStatus.WAIVED_NOT_DISPATCHED,
        OperationStatus.WAIVED_FAILURE,
    }),
    OperationStatus.DISPATCHED: frozenset({
        OperationStatus.RESULT_RECORDED,
        OperationStatus.WAIVED_FAILURE,
        OperationStatus.WAIVED_UNKNOWN,
    }),
    OperationStatus.RESULT_RECORDED: frozenset({
        OperationStatus.SETTLED,
        OperationStatus.WAIVED_FAILURE,
    }),
}


def _strict_int(value: Any, name: str, *, minimum: int = 0,
                maximum: int = MAX_BIGINT) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BillingError("INVALID_INPUT", f"{name} 必须是整数")
    if value < minimum or value > maximum:
        raise BillingError(
            "INVALID_INPUT", f"{name} 必须在 {minimum} 到 {maximum} 之间"
        )
    return value


def _positive_int(value: Any, name: str) -> int:
    return _strict_int(value, name, minimum=1)


def _utc_datetime(value: Any, name: str) -> datetime:
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            value = datetime.fromisoformat(text)
        except ValueError:
            raise BillingConfigError(f"{name} 不是有效 ISO-8601 时间") from None
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise BillingConfigError(f"{name} 必须是带时区时间")
    if value.utcoffset() != timedelta(0):
        raise BillingConfigError(f"{name} 必须显式使用 UTC")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class Usage:
    total_tokens: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cache_hit_tokens: int | None = None
    cache_miss_tokens: int | None = None
    reasoning_tokens: int | None = None

    def __post_init__(self) -> None:
        try:
            total = _strict_int(self.total_tokens, "total_tokens")
            prompt = (
                _strict_int(self.prompt_tokens, "prompt_tokens")
                if self.prompt_tokens is not None else None
            )
            completion = (
                _strict_int(self.completion_tokens, "completion_tokens")
                if self.completion_tokens is not None else None
            )
            if self.cache_hit_tokens is not None:
                _strict_int(self.cache_hit_tokens, "cache_hit_tokens")
            if self.cache_miss_tokens is not None:
                _strict_int(self.cache_miss_tokens, "cache_miss_tokens")
        except BillingError as exc:
            raise BillingContractError(exc.message) from None
        if prompt is not None and completion is not None and total != prompt + completion:
            raise BillingContractError("total_tokens 必须等于 prompt_tokens 与 completion_tokens 之和")
        if self.reasoning_tokens is not None:
            try:
                reasoning = _strict_int(self.reasoning_tokens, "reasoning_tokens")
            except BillingError as exc:
                raise BillingContractError(exc.message) from None
            if completion is not None and reasoning > completion:
                raise BillingContractError("reasoning_tokens 必须是 completion_tokens 的子集")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "Usage":
        if not isinstance(value, Mapping):
            raise BillingContractError("usage 必须是对象")

        def required(primary: str) -> Any:
            if primary not in value:
                raise BillingContractError(f"usage 缺少 {primary}")
            return value[primary]

        def optional(primary: str, alias: str | None = None) -> Any:
            present = [key for key in (primary, alias) if key and key in value]
            if not present:
                return None
            if len(present) == 2 and value[present[0]] != value[present[1]]:
                raise BillingContractError(f"usage 的 {primary} 别名值不一致")
            return value[present[0]]

        return cls(
            total_tokens=required("total_tokens"),
            prompt_tokens=optional("prompt_tokens"),
            completion_tokens=optional("completion_tokens"),
            cache_hit_tokens=optional("cache_hit_tokens", "prompt_cache_hit_tokens"),
            cache_miss_tokens=optional("cache_miss_tokens", "prompt_cache_miss_tokens"),
            reasoning_tokens=optional("reasoning_tokens", "reasoning_tokens_count"),
        )

    def as_dict(self) -> dict[str, int]:
        result = {"total_tokens": self.total_tokens}
        for name in (
            "prompt_tokens", "completion_tokens", "cache_hit_tokens",
            "cache_miss_tokens", "reasoning_tokens",
        ):
            value = getattr(self, name)
            if value is not None:
                result[name] = value
        return result


@dataclass(frozen=True, slots=True)
class TokenBillingPolicy:
    """一次报价固化的模型预算规则；Token 单价不是可配置项。"""

    requested_model: str
    allowed_returned_models: tuple[str, ...]
    tokenizer_revision: str
    prompt_template_revision: str
    output_limit: int

    def __post_init__(self) -> None:
        for field_name in (
            "requested_model", "tokenizer_revision", "prompt_template_revision",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise BillingConfigError(f"{field_name} 必须是非空字符串")
        if not isinstance(self.allowed_returned_models, tuple):
            object.__setattr__(self, "allowed_returned_models", tuple(self.allowed_returned_models))
        if not self.allowed_returned_models or any(
            not isinstance(model, str) or not model.strip()
            for model in self.allowed_returned_models
        ):
            raise BillingConfigError("allowed_returned_models 必须包含非空模型名")
        if len(set(self.allowed_returned_models)) != len(self.allowed_returned_models):
            raise BillingConfigError("allowed_returned_models 不得重复")
        _positive_int(self.output_limit, "output_limit")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TokenBillingPolicy":
        if not isinstance(value, Mapping):
            raise BillingConfigError("billing_policy 必须是对象")
        required = (
            "billing_rule", "microcredits_per_token", "requested_model",
            "allowed_returned_models", "tokenizer_revision",
            "prompt_template_revision", "output_limit",
        )
        missing = [key for key in required if key not in value]
        if missing:
            raise BillingConfigError(f"billing_policy 缺少 {', '.join(missing)}")
        if value["billing_rule"] != BILLING_RULE:
            raise BillingConfigError("billing_rule 必须是 total_tokens_1_to_1")
        microcredits = value["microcredits_per_token"]
        if microcredits not in (MICROCREDITS_PER_TOKEN, str(MICROCREDITS_PER_TOKEN)):
            raise BillingConfigError("microcredits_per_token 固定为1000000")
        allowed = value["allowed_returned_models"]
        if isinstance(allowed, (str, bytes)) or not isinstance(allowed, Sequence):
            raise BillingConfigError("allowed_returned_models 必须是数组")
        return cls(
            requested_model=value["requested_model"],
            allowed_returned_models=tuple(allowed),
            tokenizer_revision=value["tokenizer_revision"],
            prompt_template_revision=value["prompt_template_revision"],
            output_limit=value["output_limit"],
        )

    def as_dict(self) -> dict[str, Any]:
        """生成可直接写入 jsonb 的固定 Token 计费策略。"""
        return {
            "billing_rule": BILLING_RULE,
            "microcredits_per_token": str(MICROCREDITS_PER_TOKEN),
            "requested_model": self.requested_model,
            "allowed_returned_models": list(self.allowed_returned_models),
            "tokenizer_revision": self.tokenizer_revision,
            "prompt_template_revision": self.prompt_template_revision,
            "output_limit": self.output_limit,
        }


@dataclass(frozen=True, slots=True)
class UsageCharge:
    total_tokens: int
    charged_microcredits: int


@dataclass(frozen=True, slots=True)
class ReservationSplit:
    daily_tokens: int
    wallet_microcredits: int


def quota_period_key(at: datetime) -> str:
    """Return the Beijing-date label for a quota day starting at 04:00."""
    if not isinstance(at, datetime) or at.tzinfo is None:
        raise BillingError("INVALID_INPUT", "额度周期时间必须带时区")
    local = at.astimezone(QUOTA_TIMEZONE) - timedelta(hours=DAILY_RESET_HOUR)
    return local.date().isoformat()


def daily_token_limit(membership_tier: str) -> int:
    if membership_tier == "premium":
        return PREMIUM_DAILY_TOKENS
    if membership_tier == "standard":
        return STANDARD_DAILY_TOKENS
    raise BillingError("INVALID_INPUT", "未知会员等级")


def split_reservation(*, required_tokens: int,
                      daily_limit_tokens: int,
                      daily_used_tokens: int,
                      daily_reserved_tokens: int,
                      balance_microcredits: int,
                      balance_reserved_microcredits: int,
                      refund_reserved_microcredits: int) -> ReservationSplit:
    """Reserve today's allowance first, then the persistent recharge balance."""
    required = _strict_int(required_tokens, "required_tokens")
    daily_limit = _strict_int(daily_limit_tokens, "daily_limit_tokens")
    daily_used = _strict_int(daily_used_tokens, "daily_used_tokens")
    daily_reserved = _strict_int(daily_reserved_tokens, "daily_reserved_tokens")
    if daily_used + daily_reserved > daily_limit:
        raise BillingContractError("每日额度的已用与冻结之和超过上限")
    daily_part = min(required, daily_limit - daily_used - daily_reserved)
    balance_part = charge_microcredits(required - daily_part)
    if balance_part > available_microcredits(
        balance_microcredits, balance_reserved_microcredits,
        refund_reserved_microcredits,
    ):
        raise BillingError("INSUFFICIENT_CREDITS", "今日额度和充值积分不足")
    return ReservationSplit(daily_part, balance_part)


def split_settlement(*, charged_tokens: int,
                     reserved_daily_tokens: int,
                     reserved_wallet_microcredits: int) -> ReservationSplit:
    """Settle against the same quota period and source split captured at reserve."""
    charged = _strict_int(charged_tokens, "charged_tokens")
    reserved_daily = _strict_int(
        reserved_daily_tokens, "reserved_daily_tokens"
    )
    reserved_wallet = _strict_int(
        reserved_wallet_microcredits, "reserved_wallet_microcredits"
    )
    wallet_tokens = reserved_wallet // MICROCREDITS_PER_TOKEN
    if reserved_wallet % MICROCREDITS_PER_TOKEN:
        raise BillingContractError("钱包冻结额不是完整积分")
    if charged > reserved_daily + wallet_tokens:
        raise BillingContractError("实际扣费超过冻结总额")
    daily_part = min(charged, reserved_daily)
    return ReservationSplit(daily_part, charge_microcredits(charged - daily_part))


@dataclass(frozen=True, slots=True)
class QuoteBudget:
    quoted_input_token_upper_bound: int
    output_limit: int
    quoted_microcredits: int
    budget_valid_until: datetime


@dataclass(frozen=True, slots=True)
class AIRequestIntent:
    action: str
    article_key: str
    git_commit: str
    segment_id: str
    parent_id: int | None = None
    replace_node_id: int | None = None
    pos_start: int | None = None
    pos_end: int | None = None
    question: str | None = None

    def __post_init__(self) -> None:
        if self.action not in SUPPORTED_ACTIONS:
            raise BillingError("INVALID_INPUT", "action 必须是 init、explain、ask 或 advanced")
        for name in ("article_key", "git_commit", "segment_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise BillingError("INVALID_INPUT", f"{name} 必须是非空字符串")
        for name in ("parent_id", "replace_node_id"):
            value = getattr(self, name)
            if value is not None:
                _positive_int(value, name)
        has_start = self.pos_start is not None
        has_end = self.pos_end is not None
        if has_start != has_end:
            raise BillingError("INVALID_OFFSET", "pos_start 与 pos_end 必须同时提供")
        if has_start:
            start = _strict_int(self.pos_start, "pos_start")
            end = _strict_int(self.pos_end, "pos_end")
            if start >= end:
                raise BillingError("INVALID_OFFSET", "选区必须满足 pos_start < pos_end")
        if self.question is not None and not isinstance(self.question, str):
            raise BillingError("INVALID_INPUT", "question 必须是字符串或 null")
        if self.action == "init":
            if self.parent_id is not None or has_start or self.question is not None:
                raise BillingError("INVALID_INPUT", "init 不接受父节点、选区或问题")
        elif self.action == "explain":
            if not has_start or self.question is not None:
                raise BillingError("INVALID_INPUT", "explain 需要合法选区，且不接受问题")
        elif self.action == "ask":
            if self.question is None or not self.question.strip():
                raise BillingError("INVALID_INPUT", "ask 需要非空问题")
        elif self.action == "advanced":
            if self.parent_id is None:
                raise BillingError("INVALID_INPUT", "高级解释需要对应的父解释")

    def fingerprint_payload(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "article_key": self.article_key,
            "git_commit": self.git_commit,
            "segment_id": self.segment_id,
            "parent_id": self.parent_id,
            "replace_node_id": self.replace_node_id,
            "pos_start": self.pos_start,
            "pos_end": self.pos_end,
            "question": self.question,
        }

    def request_hash(self) -> str:
        return stable_json_hash(self.fingerprint_payload())


def validate_quote_window(*, quoted_at: datetime, quote_expires_at: datetime,
                          budget_valid_until: datetime) -> None:
    quoted = _utc_datetime(quoted_at, "quoted_at")
    expires = _utc_datetime(quote_expires_at, "quote_expires_at")
    budget_end = _utc_datetime(budget_valid_until, "budget_valid_until")
    if expires != quoted + timedelta(seconds=QUOTE_TTL_SECONDS):
        raise BillingConfigError("报价有效期必须固定为60秒")
    expected_end = expires + timedelta(
        seconds=MAX_QUEUE_SECONDS + DISPATCH_DEADLINE_SECONDS
    )
    if budget_end != expected_end:
        raise BillingConfigError("budget_valid_until 必须覆盖报价、排队与执行完整窗口")


def charge_microcredits(total_tokens: int) -> int:
    """固定按 usage.total_tokens 计费：1 Token = 1 积分。"""
    tokens = _strict_int(total_tokens, "total_tokens")
    if tokens > MAX_BIGINT // MICROCREDITS_PER_TOKEN:
        raise BillingError("INVALID_INPUT", "Token 对应微积分超出 bigint 范围")
    return tokens * MICROCREDITS_PER_TOKEN


def quote_budget(policy: TokenBillingPolicy, *, input_token_upper_bound: int,
                 quoted_at: datetime, quote_expires_at: datetime,
                 budget_valid_until: datetime) -> QuoteBudget:
    input_tokens = _strict_int(
        input_token_upper_bound, "quoted_input_token_upper_bound"
    )
    validate_quote_window(
        quoted_at=quoted_at,
        quote_expires_at=quote_expires_at,
        budget_valid_until=budget_valid_until,
    )
    total_upper_bound = input_tokens + policy.output_limit
    return QuoteBudget(
        quoted_input_token_upper_bound=input_tokens,
        output_limit=policy.output_limit,
        quoted_microcredits=charge_microcredits(total_upper_bound),
        budget_valid_until=_utc_datetime(budget_valid_until, "budget_valid_until"),
    )


def calculate_actual_charge(policy: TokenBillingPolicy, *, usage: Usage,
                            returned_model: str, quote: QuoteBudget,
                            reserved_microcredits: int) -> UsageCharge:
    if returned_model not in policy.allowed_returned_models:
        raise BillingContractError("响应模型不在报价计费策略许可范围内")
    if quote.output_limit != policy.output_limit:
        raise BillingContractError("报价输出上限与计费策略不一致")
    total_upper_bound = quote.quoted_input_token_upper_bound + quote.output_limit
    if usage.total_tokens > total_upper_bound:
        raise BillingContractError("实际总Token超过报价上限")
    if (
        usage.prompt_tokens is not None
        and usage.prompt_tokens > quote.quoted_input_token_upper_bound
    ):
        raise BillingContractError("实际输入Token超过报价上限")
    if (
        usage.completion_tokens is not None
        and usage.completion_tokens > policy.output_limit
    ):
        raise BillingContractError("实际输出Token超过报价上限")
    expected_reserved = charge_microcredits(
        quote.quoted_input_token_upper_bound + quote.output_limit
    )
    if quote.quoted_microcredits != expected_reserved:
        raise BillingContractError("报价微积分与Token上限不一致")
    reserved = _strict_int(reserved_microcredits, "reserved_microcredits")
    if reserved != quote.quoted_microcredits:
        raise BillingContractError("实际冻结微积分与报价不一致")
    charged = charge_microcredits(usage.total_tokens)
    if charged > reserved:
        raise BillingContractError("实际扣费超过已冻结上限")
    return UsageCharge(usage.total_tokens, charged)


def recharge_microcredits(amount_fen: int, recharge_credits_per_cny: int) -> int:
    fen = _positive_int(amount_fen, "amount_fen")
    credits = _positive_int(
        recharge_credits_per_cny, "recharge_credits_per_cny"
    )
    value = fen * credits * (MICROCREDITS_PER_CREDIT // FEN_PER_CNY)
    if value > MAX_BIGINT:
        raise BillingError("INVALID_INPUT", "充值微积分超出 bigint 范围")
    return value


def available_microcredits(balance_microcredits: int, ai_reserved_microcredits: int,
                           refund_reserved_microcredits: int) -> int:
    balance = _strict_int(balance_microcredits, "balance_microcredits")
    ai_reserved = _strict_int(ai_reserved_microcredits, "ai_reserved_microcredits")
    refund_reserved = _strict_int(
        refund_reserved_microcredits, "refund_reserved_microcredits"
    )
    available = balance - ai_reserved - refund_reserved
    if available < 0:
        raise BillingContractError("钱包余额小于冻结微积分之和")
    return available


def refundable_fen(*, order_amount_fen: int, succeeded_refund_fen: int,
                   pending_refund_fen: int, balance_microcredits: int,
                   ai_reserved_microcredits: int,
                   refund_reserved_microcredits: int,
                   recharge_credits_per_cny: int) -> int:
    order = _positive_int(order_amount_fen, "order_amount_fen")
    succeeded = _strict_int(succeeded_refund_fen, "succeeded_refund_fen")
    pending = _strict_int(pending_refund_fen, "pending_refund_fen")
    if succeeded + pending > order:
        raise BillingContractError("订单已退款与待处理退款之和超过原订单金额")
    available = available_microcredits(
        balance_microcredits, ai_reserved_microcredits, refund_reserved_microcredits
    )
    unit = _positive_int(
        recharge_credits_per_cny, "recharge_credits_per_cny"
    ) * (
        MICROCREDITS_PER_CREDIT // FEN_PER_CNY
    )
    return min(order - succeeded - pending, available // unit)


def validate_refund_amount(amount_fen: int, **refundable_args: int) -> int:
    requested = _positive_int(amount_fen, "amount_fen")
    maximum = refundable_fen(**refundable_args)
    if requested > maximum:
        raise BillingError("INVALID_REFUND_AMOUNT", "退款金额超过订单或钱包可退额度")
    return recharge_microcredits(
        requested, refundable_args["recharge_credits_per_cny"]
    )


def validate_operation_transition(current: OperationStatus | str,
                                  target: OperationStatus | str) -> OperationStatus:
    try:
        source = OperationStatus(current)
        destination = OperationStatus(target)
    except ValueError:
        raise BillingContractError("未知AI操作状态") from None
    if source == destination:
        return destination
    if source in TERMINAL_OPERATION_STATUSES:
        raise BillingContractError("终态AI操作不可再次推进")
    if destination not in _ALLOWED_TRANSITIONS.get(source, frozenset()):
        raise BillingContractError(f"非法AI操作状态迁移：{source.value} -> {destination.value}")
    return destination


def held_microcredits(status: OperationStatus | str,
                      reserved_microcredits: int) -> int:
    try:
        parsed = OperationStatus(status)
    except ValueError:
        raise BillingContractError("未知AI操作状态") from None
    reserved = _strict_int(reserved_microcredits, "reserved_microcredits")
    return reserved if parsed in HELD_OPERATION_STATUSES else 0


def _validate_json_value(value: Any, path: str = "$") -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int) and not isinstance(value, bool):
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise BillingError("INVALID_INPUT", f"{path} 的JSON键必须是字符串")
            _validate_json_value(item, f"{path}.{key}")
        return
    raise BillingError(
        "INVALID_INPUT", f"{path} 含有不稳定的JSON类型 {type(value).__name__}"
    )


def canonical_json_bytes(value: Any) -> bytes:
    """按企划书固定的键排序、UTF-8、紧凑分隔符编码。"""
    _validate_json_value(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def stable_json_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def validate_idempotent_replay(existing_request_hash: str,
                               incoming_request_hash: str) -> None:
    if not isinstance(existing_request_hash, str) or not isinstance(
        incoming_request_hash, str
    ):
        raise BillingError("INVALID_INPUT", "request_hash 必须是字符串")
    if existing_request_hash != incoming_request_hash:
        raise BillingError("IDEMPOTENCY_CONFLICT", "同一幂等键对应了不同请求")


def validate_result_replay(existing_result_hash: str,
                           incoming_result_hash: str) -> None:
    """允许相同供应商结果重放，拒绝用不同响应覆写既有结果。"""
    if not isinstance(existing_result_hash, str) or not isinstance(
        incoming_result_hash, str
    ):
        raise BillingContractError("result_hash 必须是字符串")
    if existing_result_hash != incoming_result_hash:
        raise BillingContractError("同一AI操作收到不同模型结果")


def result_payload(*, content: str, response_id: str | None,
                   returned_model: str, created: int,
                   finish_reason: str, usage: Usage) -> dict[str, Any]:
    if not isinstance(content, str) or not content.strip():
        raise BillingContractError("模型结果正文为空")
    if response_id is not None and not isinstance(response_id, str):
        raise BillingContractError("response_id 必须是字符串或 null")
    if not isinstance(returned_model, str) or not returned_model:
        raise BillingContractError("returned_model 必须是非空字符串")
    created_value = _strict_int(created, "created")
    if finish_reason != "stop":
        raise BillingContractError("模型结果不是可交付的完整完成状态")
    return {
        "content": content,
        "response_id": response_id,
        "returned_model": returned_model,
        "created": created_value,
        "finish_reason": finish_reason,
        "usage": usage.as_dict(),
    }


def result_hash(**result_fields: Any) -> str:
    """计算只覆盖企划书规定六项的稳定模型结果指纹。"""
    return stable_json_hash(result_payload(**result_fields))


# PostgREST orchestration stays explicit and thin. Every mutation targets one
# transaction function; callers never update wallets, ledgers, or states with
# separate table requests.

async def _rpc_row(name: str, payload: dict) -> dict:
    from . import store

    data = await store.rpc(name, payload)
    if isinstance(data, list):
        return data[0] if data else {}
    return data if isinstance(data, dict) else {}


async def account(uid: str) -> dict:
    return await _rpc_row("get_billing_account", {"actor_uid": uid})


async def apply_demo_membership(*, uid: str, entitlement_id: str,
                                idempotency_key: str,
                                request_hash_value: str,
                                amount_fen: int) -> dict:
    """原子开通或续期一次演示会员；价格与期限由服务端固定。"""
    return await _rpc_row("apply_demo_membership", {
        "actor_uid": uid,
        "p_entitlement_id": entitlement_id,
        "p_idempotency_key": idempotency_key,
        "p_request_hash": request_hash_value,
        "p_amount_fen": amount_fen,
    })


async def ledger(uid: str, *, limit: int = 20, before_id: int | None = None) -> list:
    from . import store

    params = {
        "uid": f"eq.{uid}",
        "select": "id,kind,balance_delta,balance_after,created_at,ai_operation_id,payment_order_id,refund_id",
        "order": "id.desc",
        "limit": str(max(1, min(limit, 100))),
    }
    if before_id is not None:
        params["id"] = f"lt.{_positive_int(before_id, 'before_id')}"
    response = await store._get_client().get(f"{store._REST}/credit_ledger", params=params)
    response.raise_for_status()
    return response.json()


async def create_quote_record(*, uid: str, operation_id: str,
                              idempotency_key: str, request_hash_value: str,
                              intent: AIRequestIntent, prepared_request: Mapping[str, Any],
                              policy: TokenBillingPolicy, quote: QuoteBudget,
                              quoted_at: datetime, quote_expires_at: datetime) -> dict:
    return await _rpc_row("create_ai_quote", {
        "actor_uid": uid,
        "p_operation_id": operation_id,
        "p_idempotency_key": idempotency_key,
        "p_request_hash": request_hash_value,
        "p_intent": intent.fingerprint_payload(),
        "p_messages": dict(prepared_request),
        "p_billing_policy": policy.as_dict(),
        "p_quoted_at": _utc_datetime(quoted_at, "quoted_at").isoformat(),
        "p_quote_expires_at": _utc_datetime(quote_expires_at, "quote_expires_at").isoformat(),
        "p_budget_valid_until": quote.budget_valid_until.isoformat(),
        "p_quoted_input_token_upper_bound": quote.quoted_input_token_upper_bound,
        "p_quoted_microcredits": quote.quoted_microcredits,
    })


async def reserve_operation(uid: str, operation_id: str) -> dict:
    return await _rpc_row("reserve_ai_operation", {
        "actor_uid": uid, "p_operation_id": operation_id,
    })


async def get_operation(uid: str, operation_id: str) -> dict | None:
    from . import store

    response = await store._get_client().get(
        f"{store._REST}/ai_operations",
        params={"id": f"eq.{operation_id}", "uid": f"eq.{uid}", "limit": 1},
    )
    response.raise_for_status()
    rows = response.json()
    return rows[0] if rows else None


async def claim_operation(executor_token: str) -> dict | None:
    row = await _rpc_row("claim_ai_operation", {"p_executor_token": executor_token})
    return row or None


async def record_result(operation_id: str, executor_token: str,
                        result: Mapping[str, Any]) -> dict:
    usage = Usage.from_mapping(result.get("usage"))
    payload = result_payload(
        content=str(result.get("content") or ""),
        response_id=result.get("response_id"),
        returned_model=result.get("returned_model"),
        created=result.get("created"),
        finish_reason=result.get("finish_reason"),
        usage=usage,
    )
    return await _rpc_row("record_ai_result", {
        "p_operation_id": operation_id,
        "p_executor_token": executor_token,
        "p_result_json": payload,
        "p_result_hash": stable_json_hash(payload),
    })


async def finalize_operation(operation_id: str) -> dict:
    return await _rpc_row("finalize_ai_operation", {"p_operation_id": operation_id})


async def waive_operation(operation_id: str, reason: str) -> dict:
    return await _rpc_row("waive_ai_operation", {
        "p_operation_id": operation_id, "p_reason": reason,
    })
