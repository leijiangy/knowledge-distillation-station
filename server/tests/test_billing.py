# -*- coding: utf-8 -*-
"""固定 1 Token = 1 积分的计费核心与薄数据库编排测试。"""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import main
from core import billing
from core.billing import (
    AIRequestIntent,
    BillingConfigError,
    BillingContractError,
    BillingError,
    MAX_BIGINT,
    MICROCREDITS_PER_TOKEN,
    OperationStatus,
    QuoteBudget,
    TokenBillingPolicy,
    Usage,
    available_microcredits,
    calculate_actual_charge,
    canonical_json_bytes,
    charge_microcredits,
    held_microcredits,
    quote_budget,
    recharge_microcredits,
    refundable_fen,
    result_hash,
    stable_json_hash,
    validate_idempotent_replay,
    validate_operation_transition,
    validate_refund_amount,
    validate_result_replay,
)


UTC = timezone.utc
QUOTED_AT = datetime(2026, 9, 15, tzinfo=UTC)
QUOTE_EXPIRES_AT = QUOTED_AT + timedelta(seconds=60)
BUDGET_VALID_UNTIL = QUOTE_EXPIRES_AT + timedelta(seconds=300)


def policy(output_limit=100, models=("deepseek-flash",)):
    return TokenBillingPolicy(
        requested_model="deepseek-flash",
        allowed_returned_models=models,
        tokenizer_revision="test-tokenizer-v1",
        prompt_template_revision="test-prompt-v1",
        output_limit=output_limit,
    )


def sample_usage():
    return Usage(
        total_tokens=350, prompt_tokens=300, completion_tokens=50,
        cache_hit_tokens=100, cache_miss_tokens=200,
    )


def sample_quote(input_limit=300, output_limit=100):
    return quote_budget(
        policy(output_limit=output_limit),
        input_token_upper_bound=input_limit,
        quoted_at=QUOTED_AT,
        quote_expires_at=QUOTE_EXPIRES_AT,
        budget_valid_until=BUDGET_VALID_UNTIL,
    )


def assert_code(exc_info, code):
    assert exc_info.value.code == code


def test_each_response_token_costs_exactly_one_point():
    assert charge_microcredits(0) == 0
    assert charge_microcredits(350) == 350 * MICROCREDITS_PER_TOKEN


def test_daily_quota_resets_at_four_am_beijing():
    before = datetime.fromisoformat("2026-09-15T03:59:59+08:00")
    after = datetime.fromisoformat("2026-09-15T04:00:00+08:00")
    assert billing.quota_period_key(before) == "2026-09-14"
    assert billing.quota_period_key(after) == "2026-09-15"
    assert billing.daily_token_limit("standard") == 50_000
    assert billing.daily_token_limit("premium") == 500_000
    assert billing.PREMIUM_MONTHLY_PRICE_FEN == 1_990


def test_reservation_uses_daily_quota_then_recharge_balance():
    unit = billing.MICROCREDITS_PER_CREDIT
    split = billing.split_reservation(
        required_tokens=20_000,
        daily_limit_tokens=50_000,
        daily_used_tokens=40_000,
        daily_reserved_tokens=0,
        balance_microcredits=50_000 * unit,
        balance_reserved_microcredits=0,
        refund_reserved_microcredits=0,
    )
    assert split.daily_tokens == 10_000
    assert split.wallet_microcredits == 10_000 * unit
    settled = billing.split_settlement(
        charged_tokens=12_000,
        reserved_daily_tokens=split.daily_tokens,
        reserved_wallet_microcredits=split.wallet_microcredits,
    )
    assert settled.daily_tokens == 10_000
    assert settled.wallet_microcredits == 2_000 * unit


def test_reservation_rejects_when_daily_and_recharge_are_insufficient():
    unit = billing.MICROCREDITS_PER_CREDIT
    with pytest.raises(BillingError) as exc:
        billing.split_reservation(
            required_tokens=20_000,
            daily_limit_tokens=50_000,
            daily_used_tokens=45_000,
            daily_reserved_tokens=0,
            balance_microcredits=10_000 * unit,
            balance_reserved_microcredits=0,
            refund_reserved_microcredits=0,
        )
    assert_code(exc, "INSUFFICIENT_CREDITS")


@pytest.mark.parametrize("bad", [True, -1, 1.5, "1"])
def test_token_charge_rejects_bool_negative_float_and_string(bad):
    with pytest.raises(BillingError) as exc:
        charge_microcredits(bad)
    assert_code(exc, "INVALID_INPUT")


def test_token_charge_rejects_bigint_overflow_before_storage():
    largest = MAX_BIGINT // MICROCREDITS_PER_TOKEN
    assert charge_microcredits(largest) <= MAX_BIGINT
    with pytest.raises(BillingError) as exc:
        charge_microcredits(largest + 1)
    assert_code(exc, "INVALID_INPUT")


def test_usage_accepts_provider_aliases_and_reasoning_is_not_added_twice():
    usage = Usage.from_mapping({
        "prompt_cache_hit_tokens": 100,
        "prompt_cache_miss_tokens": 200,
        "prompt_tokens": 300,
        "completion_tokens": 50,
        "total_tokens": 350,
        "reasoning_tokens": 30,
    })
    assert usage == Usage(350, 300, 50, 100, 200, 30)
    assert charge_microcredits(usage.total_tokens) == 350_000_000


@pytest.mark.parametrize("data", [
    {"cache_hit_tokens": 1, "cache_miss_tokens": 2, "prompt_tokens": 3,
     "completion_tokens": 3, "total_tokens": 5},
    {"cache_hit_tokens": -1, "cache_miss_tokens": 2, "prompt_tokens": 1,
     "completion_tokens": 3, "total_tokens": 4},
    {"cache_hit_tokens": True, "cache_miss_tokens": 2, "prompt_tokens": 3,
     "completion_tokens": 3, "total_tokens": 6},
])
def test_usage_rejects_inconsistent_negative_and_bool_values(data):
    with pytest.raises(BillingContractError) as exc:
        Usage.from_mapping(data)
    assert_code(exc, "BILLING_CONTRACT_MISMATCH")


def test_usage_only_requires_total_and_does_not_reconcile_cache_categories():
    assert Usage.from_mapping({"total_tokens": 7}) == Usage(total_tokens=7)
    usage = Usage.from_mapping({
        "total_tokens": 7,
        "prompt_tokens": 4,
        "cache_hit_tokens": 99,
        "cache_miss_tokens": 88,
    })
    assert usage.total_tokens == 7
    assert usage.prompt_tokens == 4
    assert usage.cache_hit_tokens == 99


def test_usage_rejects_missing_fields_conflicting_aliases_and_reasoning_overrun():
    with pytest.raises(BillingContractError):
        Usage.from_mapping({"prompt_tokens": 0, "completion_tokens": 0})
    with pytest.raises(BillingContractError):
        Usage.from_mapping({
            "cache_hit_tokens": 1, "prompt_cache_hit_tokens": 2,
            "cache_miss_tokens": 0, "prompt_tokens": 1,
            "completion_tokens": 0, "total_tokens": 1,
        })
    with pytest.raises(BillingContractError):
        Usage(total_tokens=3, prompt_tokens=1, completion_tokens=2,
              reasoning_tokens=3)


def test_policy_round_trip_contains_no_supplier_price_or_service_fee():
    configured = policy()
    encoded = configured.as_dict()
    assert encoded["billing_rule"] == "total_tokens_1_to_1"
    assert encoded["microcredits_per_token"] == "1000000"
    assert "fee_bps" not in encoded
    assert "intervals" not in encoded
    assert "credits_per_cny" not in encoded
    assert TokenBillingPolicy.from_mapping(encoded) == configured


def test_policy_rejects_configurable_token_rate_and_missing_revision():
    raw = policy().as_dict()
    raw["microcredits_per_token"] = "2"
    with pytest.raises(BillingConfigError):
        TokenBillingPolicy.from_mapping(raw)
    raw = policy().as_dict()
    raw["tokenizer_revision"] = ""
    with pytest.raises(BillingConfigError):
        TokenBillingPolicy.from_mapping(raw)


def test_quote_is_input_upper_plus_output_limit_in_tokens():
    quote = sample_quote(input_limit=300, output_limit=100)
    assert quote.quoted_input_token_upper_bound == 300
    assert quote.output_limit == 100
    assert quote.quoted_microcredits == 400_000_000


def test_quote_requires_exact_lifetime_and_budget_deadline():
    with pytest.raises(BillingConfigError):
        quote_budget(
            policy(), input_token_upper_bound=300, quoted_at=QUOTED_AT,
            quote_expires_at=QUOTED_AT + timedelta(seconds=59),
            budget_valid_until=BUDGET_VALID_UNTIL,
        )
    with pytest.raises(BillingConfigError):
        quote_budget(
            policy(), input_token_upper_bound=300, quoted_at=QUOTED_AT,
            quote_expires_at=QUOTE_EXPIRES_AT,
            budget_valid_until=BUDGET_VALID_UNTIL - timedelta(seconds=1),
        )


def test_actual_charge_uses_usage_total_tokens():
    quote = sample_quote()
    result = calculate_actual_charge(
        policy(), usage=sample_usage(), returned_model="deepseek-flash",
        quote=quote, reserved_microcredits=quote.quoted_microcredits,
    )
    assert result.total_tokens == 350
    assert result.charged_microcredits == 350_000_000


def test_actual_charge_rejects_model_and_input_budget_mismatch():
    quote = sample_quote()
    with pytest.raises(BillingContractError):
        calculate_actual_charge(
            policy(), usage=sample_usage(), returned_model="different-model",
            quote=quote, reserved_microcredits=quote.quoted_microcredits,
        )
    over_input = Usage(total_tokens=302, prompt_tokens=301, completion_tokens=1)
    with pytest.raises(BillingContractError):
        calculate_actual_charge(
            policy(), usage=over_input, returned_model="deepseek-flash",
            quote=quote, reserved_microcredits=quote.quoted_microcredits,
        )


def test_actual_charge_rejects_output_quote_and_reservation_mismatch():
    quote = sample_quote()
    over_output = Usage(total_tokens=102, prompt_tokens=1, completion_tokens=101)
    with pytest.raises(BillingContractError):
        calculate_actual_charge(
            policy(), usage=over_output, returned_model="deepseek-flash",
            quote=quote, reserved_microcredits=quote.quoted_microcredits,
        )
    forged_quote = QuoteBudget(300, 100, 1, BUDGET_VALID_UNTIL)
    with pytest.raises(BillingContractError):
        calculate_actual_charge(
            policy(), usage=sample_usage(), returned_model="deepseek-flash",
            quote=forged_quote, reserved_microcredits=1,
        )
    with pytest.raises(BillingContractError):
        calculate_actual_charge(
            policy(), usage=sample_usage(), returned_model="deepseek-flash",
            quote=quote, reserved_microcredits=quote.quoted_microcredits - 1,
        )


def test_recharge_exchange_remains_independent_from_token_charge():
    assert recharge_microcredits(100, 1000) == 1_000_000_000


@pytest.mark.parametrize("bad", [True, -1, 1.5, "1"])
def test_recharge_rejects_invalid_integer_inputs(bad):
    with pytest.raises(BillingError):
        recharge_microcredits(bad, 1000)


def test_wallet_available_and_refund_floor_leave_sub_fen_remainder():
    unit = 1000 * 10_000
    balance = 6 * unit - 1
    assert available_microcredits(balance, unit, 0) == 5 * unit - 1
    assert refundable_fen(
        order_amount_fen=10, succeeded_refund_fen=2, pending_refund_fen=1,
        balance_microcredits=balance, ai_reserved_microcredits=unit,
        refund_reserved_microcredits=0, recharge_credits_per_cny=1000,
    ) == 4


def test_wallet_rejects_reserved_amounts_above_balance():
    with pytest.raises(BillingContractError):
        available_microcredits(9, 5, 5)


def test_refund_amount_validates_order_and_wallet_limits():
    args = dict(
        order_amount_fen=10, succeeded_refund_fen=2, pending_refund_fen=1,
        balance_microcredits=50_000_000, ai_reserved_microcredits=0,
        refund_reserved_microcredits=0, recharge_credits_per_cny=1000,
    )
    assert validate_refund_amount(5, **args) == 50_000_000
    with pytest.raises(BillingError) as exc:
        validate_refund_amount(6, **args)
    assert_code(exc, "INVALID_REFUND_AMOUNT")


def test_request_hash_expands_optional_fields_and_is_key_order_independent():
    intent = AIRequestIntent("init", "article", "commit", "segment")
    payload = intent.fingerprint_payload()
    assert payload["question"] is None
    assert payload["replace_node_id"] is None
    assert stable_json_hash(payload) == stable_json_hash(dict(reversed(list(payload.items()))))


def test_request_hash_preserves_exact_question_and_unicode_utf8():
    first = AIRequestIntent("ask", "文章", "commit", "segment", parent_id=1,
                            question="为什么？")
    second = AIRequestIntent("ask", "文章", "commit", "segment", parent_id=1,
                             question="为什么? ")
    assert first.request_hash() != second.request_hash()
    assert b"\\u6587" not in canonical_json_bytes(first.fingerprint_payload())


def test_canonical_json_rejects_float_and_non_string_keys():
    with pytest.raises(BillingError):
        stable_json_hash({"value": 0.1})
    with pytest.raises(BillingError):
        stable_json_hash({1: "bad"})


def test_prompt_token_bound_ignores_float_sampling_controls():
    prepared = main.ai.prepare_explain_segment("标题", "主旨", "需要解释的段落")
    assert isinstance(prepared["temperature"], float)
    assert main._prepared_input_token_upper_bound(prepared) > 256


def test_idempotent_and_result_replay_validation():
    validate_idempotent_replay("abc", "abc")
    with pytest.raises(BillingError) as exc:
        validate_idempotent_replay("abc", "def")
    assert_code(exc, "IDEMPOTENCY_CONFLICT")
    validate_result_replay("abc", "abc")
    with pytest.raises(BillingContractError):
        validate_result_replay("abc", "def")


def test_result_hash_covers_exact_six_fields_and_is_stable():
    fields = dict(
        content="解释", response_id="response-1", returned_model="deepseek-flash",
        created=1_789_430_400, finish_reason="stop", usage=sample_usage(),
    )
    assert result_hash(**fields) == result_hash(**fields)
    assert result_hash(**fields) != result_hash(**{**fields, "content": "另一解释"})


def test_result_hash_rejects_empty_truncated_or_invalid_created():
    base = dict(
        content="解释", response_id=None, returned_model="deepseek-flash",
        created=1, finish_reason="stop", usage=sample_usage(),
    )
    for changes in (
        {"content": "  "}, {"finish_reason": "length"}, {"created": True},
    ):
        with pytest.raises(BillingError):
            result_hash(**{**base, **changes})


@pytest.mark.parametrize("source,target", [
    ("quoted", "reserved"),
    ("reserved", "dispatched"),
    ("dispatched", "result_recorded"),
    ("result_recorded", "settled"),
    ("dispatched", "waived_unknown"),
])
def test_operation_state_machine_accepts_documented_transitions(source, target):
    assert validate_operation_transition(source, target) == OperationStatus(target)


def test_operation_state_machine_is_idempotent_but_terminal_cannot_advance():
    assert validate_operation_transition("settled", "settled") == OperationStatus.SETTLED
    with pytest.raises(BillingContractError):
        validate_operation_transition("settled", "reserved")
    with pytest.raises(BillingContractError):
        validate_operation_transition("quoted", "settled")


def test_held_amount_only_reflects_active_reserved_states():
    for status in ("reserved", "dispatched", "result_recorded"):
        assert held_microcredits(status, 123) == 123
    for status in ("quoted", "settled", "cache_hit", "waived_unknown"):
        assert held_microcredits(status, 123) == 0


def test_intent_validation_matches_action_shape_and_rejects_bool_offsets():
    AIRequestIntent("explain", "a", "c", "s", parent_id=1, pos_start=0, pos_end=2)
    AIRequestIntent("ask", "a", "c", "s", parent_id=1, question="问")
    with pytest.raises(BillingError):
        AIRequestIntent("explain", "a", "c", "s", parent_id=1)
    with pytest.raises(BillingError):
        AIRequestIntent("ask", "a", "c", "s", parent_id=1, question=" ")
    with pytest.raises(BillingError):
        AIRequestIntent("explain", "a", "c", "s", parent_id=1,
                        pos_start=True, pos_end=2)


def test_create_quote_rpc_persists_token_policy_not_price_snapshot(monkeypatch):
    captured = {}

    async def fake_rpc(name, payload):
        captured["name"] = name
        captured["payload"] = payload
        return {"status": "quoted"}

    monkeypatch.setattr(billing, "_rpc_row", fake_rpc)
    configured = policy()
    quote = sample_quote()
    result = asyncio.run(billing.create_quote_record(
        uid="123", operation_id="op", idempotency_key="idem",
        request_hash_value="hash",
        intent=AIRequestIntent("init", "article", "commit", "segment"),
        prepared_request={"messages": []}, policy=configured, quote=quote,
        quoted_at=QUOTED_AT, quote_expires_at=QUOTE_EXPIRES_AT,
    ))
    assert result == {"status": "quoted"}
    assert captured["name"] == "create_ai_quote"
    assert captured["payload"]["p_billing_policy"] == configured.as_dict()
    assert "p_price_snapshot" not in captured["payload"]


def test_record_result_normalizes_usage_before_rpc(monkeypatch):
    captured = {}

    async def fake_rpc(name, payload):
        captured["name"] = name
        captured["payload"] = payload
        return {"status": "result_recorded"}

    monkeypatch.setattr(billing, "_rpc_row", fake_rpc)
    result = asyncio.run(billing.record_result("op", "token", {
        "content": "解释", "response_id": "r", "returned_model": "deepseek-flash",
        "created": 1, "finish_reason": "stop",
        "usage": {
            "prompt_cache_hit_tokens": 100,
            "prompt_cache_miss_tokens": 200,
            "prompt_tokens": 300, "completion_tokens": 50, "total_tokens": 350,
        },
    }))
    assert result == {"status": "result_recorded"}
    assert captured["name"] == "record_ai_result"
    assert captured["payload"]["p_result_json"]["usage"]["total_tokens"] == 350


def test_membership_plan_is_single_server_owned_product(monkeypatch):
    monkeypatch.setattr(main, "require_account", lambda _request: ("42", None))
    monkeypatch.setattr(main.settings, "RECHARGE_ENABLED", True)
    response = asyncio.run(main.billing_plans(SimpleNamespace()))
    membership = response["membership"]
    assert membership == {
        "available": True,
        "tier": "premium",
        "duration_days": 30,
        "monthly_price_fen": 1990,
        "daily_token_limit": 500_000,
    }


def test_demo_membership_requires_login(monkeypatch):
    monkeypatch.setattr(
        main, "require_account",
        lambda _request: (None, {"code": "LOGIN_REQUIRED", "message": "请登录"}),
    )
    response = asyncio.run(main.billing_membership(SimpleNamespace()))
    assert response.status_code == 401


def test_demo_membership_uses_server_price_and_idempotency_key(monkeypatch):
    idem = "00000000-0000-4000-8000-000000000001"
    monkeypatch.setattr(main, "require_account", lambda _request: ("42", None))
    monkeypatch.setattr(main.settings, "RECHARGE_ENABLED", True)
    apply = AsyncMock(return_value={
        "id": "00000000-0000-4000-8000-000000000002",
        "activated": True,
        "membership_expires_at": "2026-10-15T00:00:00+00:00",
    })
    monkeypatch.setattr(main.billing, "apply_demo_membership", apply)
    request = SimpleNamespace(json=AsyncMock(return_value={
        "idempotency_key": idem, "amount_fen": 1, "duration_days": 999,
    }))
    result = asyncio.run(main.billing_membership(request))
    assert result["ok"] is True
    assert result["activated"] is True
    assert result["amount_fen"] == 1990
    assert result["duration_days"] == 30
    kwargs = apply.await_args.kwargs
    assert kwargs["uid"] == "42"
    assert kwargs["idempotency_key"] == idem
    assert kwargs["amount_fen"] == 1990


def test_demo_membership_rejects_invalid_idempotency_key(monkeypatch):
    monkeypatch.setattr(main, "require_account", lambda _request: ("42", None))
    monkeypatch.setattr(main.settings, "RECHARGE_ENABLED", True)
    apply = AsyncMock()
    monkeypatch.setattr(main.billing, "apply_demo_membership", apply)
    request = SimpleNamespace(json=AsyncMock(return_value={
        "idempotency_key": "not-a-uuid",
    }))
    response = asyncio.run(main.billing_membership(request))
    assert response.status_code == 422
    apply.assert_not_awaited()
