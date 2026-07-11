from __future__ import annotations

import pandas as pd
import pytest

from audit_engine.agent.rule_memory import record_rule_feedback
from audit_engine.agent.tools import EDITABLE_RULE_KEYS, execute_tool
from audit_engine.store import ProjectStore


def _seed_project(store: ProjectStore, name: str = "agent测试") -> str:
    manifest = store.create_project(name)
    pid = manifest.project_id
    df = pd.DataFrame({
        "凭证编号": ["1", "2"],
        "过账日期": pd.to_datetime(["2024-01-01", "2024-12-31"]),
        "借/贷标识": ["S", "H"],
        "凭证货币价值": [100.0, 200.0],
        "总账科目": ["660201", "600101"],
        "凭证类型": ["SA", "SA"],
        "文本": ["费用", "收入"],
    })
    store.ingest_journal(pid, {2024: df}, column_mapping={}, missing_columns=[], year_summary=[])
    return pid


def test_get_project_overview_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_WORKBENCH_DATA_ROOT", str(tmp_path))
    store = ProjectStore(root=tmp_path)
    pid = _seed_project(store)
    out = execute_tool(store, pid, "get_project_overview", {})
    assert out["years"] == [2024]
    assert out["total_rows"] == 2


def test_get_rules_catalog_and_update_rule(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_WORKBENCH_DATA_ROOT", str(tmp_path))
    store = ProjectStore(root=tmp_path)
    pid = _seed_project(store)
    catalog = execute_tool(store, pid, "get_rules_catalog", {})
    assert "sampling_rules" in catalog
    assert any(r["rule_id"] == "large_amount" for r in catalog["sampling_rules"])

    updated = execute_tool(
        store,
        pid,
        "update_rule",
        {"rule_id": "large_amount", "patches": {"round_number_threshold": 800000, "rationale": "测试阈值"}},
    )
    assert updated["ok"] is True
    assert updated["rule"]["round_number_threshold"] == 800000

    toggled = execute_tool(store, pid, "toggle_rule", {"rule_id": "large_amount", "enabled": False})
    assert toggled["enabled"] is False


def test_rule_feedback_and_hit_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_WORKBENCH_DATA_ROOT", str(tmp_path))
    store = ProjectStore(root=tmp_path)
    pid = _seed_project(store)

    fb = record_rule_feedback(store, pid, rule_id="large_amount", score=4, note="较准")
    assert fb["score"] == 4

    listed = execute_tool(store, pid, "list_rule_feedback", {"rule_id": "large_amount"})
    assert listed["items"][0]["rule_id"] == "large_amount"
    assert listed["summary"][0]["avg_score"] == 4.0

    summary = execute_tool(store, pid, "get_rule_hit_summary", {})
    assert summary["has_cached"] is False
    assert "run_sampling_rules" in (summary.get("hint") or "")


def test_describe_capabilities_and_column_status(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_WORKBENCH_DATA_ROOT", str(tmp_path))
    store = ProjectStore(root=tmp_path)
    pid = _seed_project(store)

    caps = execute_tool(store, pid, "describe_agent_capabilities", {})
    names = {t["name"] for t in caps["tools"]}
    assert "update_rule" in names
    assert "record_rule_feedback" in names
    assert "run_module_insight" in names
    assert "suggest_rule_tuning" in names

    col = execute_tool(store, pid, "get_column_mapping_status", {})
    assert "missing_columns" in col
    assert "learned_alias_count" in col


def test_update_rule_rejects_unknown(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_WORKBENCH_DATA_ROOT", str(tmp_path))
    store = ProjectStore(root=tmp_path)
    pid = _seed_project(store)
    out = execute_tool(store, pid, "update_rule", {"rule_id": "not_a_rule", "patches": {"enabled": False}})
    assert "error" in out


def test_suggest_and_apply_rule_tuning(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_WORKBENCH_DATA_ROOT", str(tmp_path))
    store = ProjectStore(root=tmp_path)
    pid = _seed_project(store)

    record_rule_feedback(store, pid, rule_id="large_amount", score=2, note="误报太多")
    state = store.load_state(pid)
    state["rule_results"] = [{"rule_name": "大额异常", "count": 120, "hits": []}]
    store.save_state(pid, state)

    sug = execute_tool(store, pid, "suggest_rule_tuning", {"rule_id": "large_amount"})
    assert sug["count"] >= 1
    assert sug["suggestions"][0]["rule_id"] == "large_amount"

    sid = sug["suggestions"][0]["suggestion_id"]
    applied = execute_tool(store, pid, "apply_rule_tuning", {"suggestion_id": sid})
    assert applied["ok"] is True
    assert applied["count"] == 1


def test_get_sampling_status(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_WORKBENCH_DATA_ROOT", str(tmp_path))
    store = ProjectStore(root=tmp_path)
    pid = _seed_project(store)
    out = execute_tool(store, pid, "get_sampling_status", {})
    assert out["rules_executed"] is False
    assert out["ready_for_export"] is False


def test_resolve_module_key_and_questions():
    from audit_engine.agent.audit_questions import load_module_questions, resolve_module_key

    assert resolve_module_key("expense") == "费用"
    qs = load_module_questions("费用")
    assert len(qs) >= 1
