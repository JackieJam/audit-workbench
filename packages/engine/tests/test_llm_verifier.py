"""Tests for LLM verifier serialization helpers (no live API calls)."""

from __future__ import annotations

from audit_engine.json_utils import parse_json_list
from audit_engine.llm_verifier import (
    LLMJudgment,
    _build_judgments_from_response,
    judgments_from_state,
    judgments_to_state,
    summarize_judgments,
)


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
            LLMJudgment("A2", True, "中", "可疑", "问询", "fallback"),
        ],
        "大额异常": [],
    }
    state = judgments_to_state(raw)
    restored = judgments_from_state(state)
    assert restored["化整为零"][0].voucher_id == "A1"
    assert restored["化整为零"][0].risk_level == "高"
    summary = summarize_judgments(restored)
    assert summary["confirmed"] == 2
    assert summary["high"] == 1
    assert summary["fallback"] == 1


def test_build_judgments_skips_unconfirmed() -> None:
    text = """[
      {"voucher_id": "X1", "confirmed": false, "risk_level": "高", "reason": "no", "audit_procedures": ""},
      {"voucher_id": "X2", "confirmed": true, "risk_level": "中", "reason": "yes", "audit_procedures": "查"}
    ]"""
    judgments = _build_judgments_from_response(text)
    assert len(judgments) == 1
    assert judgments[0].voucher_id == "X2"
