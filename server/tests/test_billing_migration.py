import re
from pathlib import Path


SQL_PATH = Path(__file__).parents[1] / "migrations" / "20260915_billing_rpc.sql"


def _sql() -> str:
    return SQL_PATH.read_text(encoding="utf-8")


def _parameters(sql: str, function: str) -> list[str]:
    match = re.search(
        rf"create\s+or\s+replace\s+function\s+{function}\s*\((.*?)\)\s*"
        r"returns\b",
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    )
    assert match, f"missing RPC: {function}"
    declarations = [part.strip() for part in match.group(1).split(",") if part.strip()]
    return [part.split()[0].lower() for part in declarations]


def test_migration_matches_python_rpc_contracts():
    sql = _sql()
    expected = {
        "get_billing_account": ["actor_uid"],
        "create_ai_quote": [
            "actor_uid", "p_operation_id", "p_idempotency_key", "p_request_hash",
            "p_intent", "p_messages", "p_billing_policy", "p_quoted_at",
            "p_quote_expires_at", "p_budget_valid_until",
            "p_quoted_input_token_upper_bound", "p_quoted_microcredits",
        ],
        "reserve_ai_operation": ["actor_uid", "p_operation_id"],
        "claim_ai_operation": ["p_executor_token"],
        "record_ai_result": [
            "p_operation_id", "p_executor_token", "p_result_json", "p_result_hash",
        ],
        "finalize_ai_operation": ["p_operation_id"],
        "waive_ai_operation": ["p_operation_id", "p_reason"],
        "begin_content_update": [
            "actor_uid", "p_update_id", "p_idempotency_key", "p_article_key",
            "p_expected_commit", "p_request_hash", "p_candidate_json",
        ],
        "claim_content_update": ["p_executor_token"],
        "save_content_candidate": [
            "p_update_id", "p_executor_token", "p_candidate_json",
        ],
        "mark_content_git_saved": [
            "p_update_id", "p_executor_token", "p_candidate_commit",
        ],
        "publish_content_update": ["p_update_id", "p_executor_token"],
        "fail_content_update": [
            "p_update_id", "p_executor_token", "p_error_code",
        ],
        "list_private_segment_nodes": [
            "actor_uid", "p_article_key", "p_segment_id", "p_include_history",
        ],
        "get_private_node": [
            "actor_uid", "p_node_id", "p_article_key", "p_segment_id",
            "p_include_history",
        ],
        "delete_private_node_tree": [
            "actor_uid", "p_node_id", "p_article_key", "p_segment_id",
        ],
    }
    for name, parameters in expected.items():
        assert _parameters(sql, name) == parameters


def test_migration_pins_billing_and_recovery_contracts():
    sql = _sql()
    for fixed_value in (
        "deepseek-flash", "total_tokens_1_to_1", "Asia/Shanghai",
        "standard_period_tokens = 50000", "premium_period_tokens = 500000",
        "premium_monthly_price_fen = 1990", "quota_reset_hour = 4",
        "usage.total_tokens", "'advanced'", "recharge_enabled boolean not null default false",
    ):
        assert fixed_value in sql

    claim = sql[sql.index("create or replace function claim_ai_operation"):]
    claim = claim[:claim.index("create or replace function record_ai_result")]
    assert "status='reserved' and deadline_at<=clock_timestamp()" in claim
    assert "status='dispatched' and deadline_at<=clock_timestamp()" in claim
    assert "perform waive_ai_operation" in claim
    assert "'TIMEOUT_UNKNOWN'" in claim

    record = sql[sql.index("create or replace function record_ai_result"):]
    record = record[:record.index("create or replace function finalize_ai_operation")]
    assert "return waive_ai_operation(p_operation_id,'BILLING_CONTRACT_MISMATCH')" in record


def test_migration_rejects_direct_private_and_billing_access():
    sql = _sql()
    assert "request.jwt.claim.role" in sql
    assert "v_role is distinct from 'service_role'" in sql
    assert "revoke all on billing_settings" in sql
    assert "content_updates,reading_nodes from public" in sql
    assert "grant execute on function get_billing_account" in sql
    assert "to service_role" in sql
