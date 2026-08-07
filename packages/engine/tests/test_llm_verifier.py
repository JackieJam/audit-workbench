"""Tests for LLM verifier serialization helpers (no live API calls)."""

from __future__ import annotations

import pytest
from audit_engine.json_utils import parse_json_list
from audit_engine.llm_verifier import (
    JUDGMENT_CONFIRMED,
    JUDGMENT_PENDING_REVIEW,
    JUDGMENT_REJECTED,
    LLMJudgment,
    _build_judgments_from_response,
    _fallback_judgments_for_hits,
    confirmation_rate,
    judgments_from_state,
    judgments_to_state,
    summarize_judgments,
)
from audit_engine.rule_engine import RuleHit


def test_parse_json_list_from_markdown_fence() -> None:
    text = """```json
[{"voucher_id": "V1", "confirmed": true, "risk_level": "高", "reason": "r", "audit_procedures": "a"}]
```"""
    data = parse_json_list(text)
    assert len(data) == 1
    assert data[0]["voucher_id"] == "V1"


def test_judgments_roundtrip_and_summary() -> None:
    raw = {
        "化整为零": [
            LLMJudgment("A1", True, "高", "拆分", "查合同", "llm"),
            LLMJudgment("A2", False, "待核验", "调用失败", "人工复核", "fallback"),
        ],
        "大额异常": [],
    }
    state = judgments_to_state(raw)
    restored = judgments_from_state(state)
    assert restored["化整为零"][0].voucher_id == "A1"
    assert restored["化整为零"][0].risk_level == "高"
    summary = summarize_judgments(restored)
    assert summary["confirmed"] == 1
    assert summary["pending_review"] == 1
    assert summary["unverified"] == 1  # 仅 pending，不含 rejected
    assert summary["high"] == 1
    assert summary["fallback"] == 1
    # pending 不进确认率分母：仅 1 条已决且确认 → 100%
    assert summary["confirmation_rate"] == 1.0
    assert confirmation_rate(restored["化整为零"]) == 1.0


def test_build_judgments_records_rejected_and_fills_omissions() -> None:
    text = """[
      {"voucher_id": "X1", "confirmed": false, "risk_level": "高", "reason": "no", "audit_procedures": ""},
      {"voucher_id": "X2", "confirmed": true, "risk_level": "中", "reason": "yes", "audit_procedures": "查"}
    ]"""
    judgments = _build_judgments_from_response(
        text,
        allowed_voucher_ids={"X1", "X2", "X3"},
    )
    by_id = {j.voucher_id: j for j in judgments}
    assert set(by_id) == {"X1", "X2", "X3"}
    assert by_id["X1"].status == JUDGMENT_REJECTED
    assert by_id["X2"].status == JUDGMENT_CONFIRMED
    assert by_id["X3"].status == JUDGMENT_REJECTED  # 模型省略 → 驳回
    assert confirmation_rate(judgments) == pytest.approx(1 / 3)


def test_build_judgments_rejects_out_of_batch_and_duplicates() -> None:
    text = """[
      {"voucher_id": "X1", "confirmed": true, "risk_level": "高", "reason": "ok", "audit_procedures": "查"},
      {"voucher_id": "X1", "confirmed": true, "risk_level": "高", "reason": "duplicate", "audit_procedures": "查"},
      {"voucher_id": "OTHER", "confirmed": true, "risk_level": "高", "reason": "hallucinated", "audit_procedures": "查"}
    ]"""
    judgments = _build_judgments_from_response(text, allowed_voucher_ids={"X1"})
    assert [item.voucher_id for item in judgments] == ["X1"]


def test_fallback_is_unverified_not_confirmed() -> None:
    hit = RuleHit(
        voucher_id="V1",
        rule_type="大额整数",
        evidence="金额达到阈值",
        line_indices=(0,),
        priority=3,
    )
    judgments = _fallback_judgments_for_hits([hit], "LLM 调用失败")
    assert len(judgments) == 1
    assert judgments[0].confirmed is False
    assert judgments[0].status == JUDGMENT_PENDING_REVIEW
    assert judgments[0].risk_level == "待核验"
    assert judgments[0].source == "fallback"
    assert confirmation_rate(judgments) is None
