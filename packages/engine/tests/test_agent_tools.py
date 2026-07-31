from __future__ import annotations

import pandas as pd
from audit_engine.agent.orchestrator import (
    _sanitize_unverified_execution_claim,
    detect_module_insight_request,
    get_agent_state,
    record_module_insight_dispatch,
    resolve_pending_action,
    set_pinned_context,
    update_agent_module_insight_job,
)
from audit_engine.agent.rule_memory import record_rule_feedback
from audit_engine.agent.tools import execute_tool
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


def test_query_journal_is_filtered_bounded_and_traceable(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_WORKBENCH_DATA_ROOT", str(tmp_path))
    store = ProjectStore(root=tmp_path)
    pid = _seed_project(store)

    out = execute_tool(
        store,
        pid,
        "query_journal",
        {
            "years": [2024],
            "text_contains": "收入",
            "group_by": "account",
            "sample_limit": 5,
        },
    )

    assert out["metrics"]["row_count"] == 1
    assert out["metrics"]["voucher_count"] == 1
    assert len(out["groups"]) == 1
    assert len(out["sample_rows"]) == 1
    assert out["provenance"]["data_version"]
    assert out["provenance"]["classification_revision"]
    assert out["provenance"]["query_spec"]["text_contains"] == "收入"


def test_query_journal_counts_and_filters_stable_voucher_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_WORKBENCH_DATA_ROOT", str(tmp_path))
    store = ProjectStore(root=tmp_path)
    pid = store.create_project("跨年同号查询").project_id

    def year_frame(year: int) -> pd.DataFrame:
        return pd.DataFrame({
            "公司代码": ["1000", "1000"],
            "会计年度": [year, year],
            "凭证编号": ["1001", "1001"],
            "过账日期": pd.to_datetime([f"{year}-01-01", f"{year}-01-01"]),
            "借/贷标识": ["S", "H"],
            "凭证货币价值": [100.0, 100.0],
            "总账科目": ["112201", "600101"],
            "文本": [f"{year}应收", f"{year}收入"],
        })

    store.ingest_journal(
        pid,
        {2023: year_frame(2023), 2024: year_frame(2024)},
        column_mapping={},
        missing_columns=[],
        year_summary=[],
    )
    all_rows = execute_tool(
        store,
        pid,
        "query_journal",
        {"years": [2023, 2024], "sample_limit": 20},
    )
    assert all_rows["metrics"]["voucher_count"] == 2
    voucher_keys = {row["凭证键"] for row in all_rows["sample_rows"]}
    assert len(voucher_keys) == 2

    selected_key = next(key for key in voucher_keys if "|2024|" in key)
    selected = execute_tool(
        store,
        pid,
        "query_journal",
        {"voucher_keys": [selected_key], "sample_limit": 20},
    )
    assert selected["metrics"]["voucher_count"] == 1
    assert {row["年度"] for row in selected["sample_rows"]} == {2024}


def test_agent_can_compare_and_select_document_currency(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_WORKBENCH_DATA_ROOT", str(tmp_path))
    store = ProjectStore(root=tmp_path)
    manifest = store.create_project("Agent 多币种")
    pid = manifest.project_id
    frame = pd.DataFrame({
        "凭证编号": ["C1", "C1", "U1", "U1"],
        "过账日期": pd.to_datetime(["2024-01-01", "2024-01-01", "2024-02-01", "2024-02-01"]),
        "借/贷标识": ["S", "H", "S", "H"],
        "凭证货币价值": [100.0, 100.0, 10.0, 10.0],
        "凭证货币代码": ["CNY", "CNY", "USD", "USD"],
        "总账科目": ["112201", "600101", "112201", "600101"],
        "总账科目：短文本": ["应收账款", "主营业务收入", "应收账款", "主营业务收入"],
    })
    store.ingest_journal(pid, {2024: frame}, column_mapping={}, missing_columns=[], year_summary=[])

    compared = execute_tool(
        store,
        pid,
        "query_journal",
        {"years": [2024], "group_by": "currency", "sample_limit": 0},
    )
    assert {row["group_value"] for row in compared["groups"]} == {"CNY", "USD"}
    assert compared["metrics"]["amounts_comparable"] is False
    assert compared["metrics"]["absolute_entry_amount"] is None

    filtered = execute_tool(
        store,
        pid,
        "query_journal",
        {"years": [2024], "currencies": ["USD"], "sample_limit": 5},
    )
    assert filtered["metrics"]["currencies"] == ["USD"]
    assert filtered["metrics"]["absolute_entry_amount"] == 20.0
    assert {row["币种"] for row in filtered["sample_rows"]} == {"USD"}

    selected = execute_tool(
        store,
        pid,
        "set_analysis_currency_scope",
        {"currency": "CNY"},
    )
    assert selected["ok"] is True
    assert selected["selected_currency"] == "CNY"
    assert any(action["type"] == "invalidate_project_analysis" for action in selected["ui_actions"])

    scoped = execute_tool(store, pid, "query_journal", {"years": [2024], "sample_limit": 5})
    assert scoped["metrics"]["currencies"] == ["CNY"]
    assert scoped["provenance"]["active_analysis_currency"] == "CNY"


def test_data_quality_review_separates_intentional_exclusions(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_WORKBENCH_DATA_ROOT", str(tmp_path))
    store = ProjectStore(root=tmp_path)
    manifest = store.create_project("质量复核")
    pid = manifest.project_id
    frame = pd.DataFrame({
        "凭证编号": ["1", "2", "3", "4", "5"],
        "过账日期": pd.to_datetime([
            "2024-01-01",
            "2024-02-01",
            "2024-03-01",
            "2024-04-01",
            "2024-05-01",
        ]),
        "借/贷标识": ["S", "S", "H", "H", "S"],
        "凭证货币价值": [100.0, 200.0, 300.0, 400.0, 500.0],
        "总账科目": ["500101", "999901", "611101", "630101", "671101"],
        "总账科目：短文本": [
            "生产成本",
            "神秘科目",
            "投资收益",
            "营业外收入",
            "营业外支出",
        ],
    })
    store.ingest_journal(pid, {2024: frame}, column_mapping={}, missing_columns=[], year_summary=[])

    out = execute_tool(store, pid, "get_data_quality_review", {})
    year = out["years"]["2024"]
    reasons = {item["account_code"]: item["reason"] for item in year["review_accounts"]}
    assert "500101" not in reasons
    assert reasons["999901"] == "needs_mapping"
    assert {"611101", "630101", "671101"}.isdisjoint(reasons)
    assert year["review_required_amount"] == 200.0
    assert year["excluded_amount"] == 0.0
    assert {"投资收益", "营业外收入", "营业外支出"}.issubset(out["allowed_categories"])


def test_agent_classification_decision_tool_returns_invalidation(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_WORKBENCH_DATA_ROOT", str(tmp_path))
    store = ProjectStore(root=tmp_path)
    pid = _seed_project(store)
    out = execute_tool(
        store,
        pid,
        "apply_classification_decisions",
        {"decisions": [{"account_code": "660201", "decision": "map", "category": "研发费用"}]},
    )
    assert out["ok"] is True
    assert any(action["type"] == "invalidate_project_analysis" for action in out["ui_actions"])
    assert store.load_state(pid)["account_category_overrides"]["660201"] == "研发费用"


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
    assert "get_audit_case_detail" in names
    assert "manage_audit_case" in names
    assert "ERP 科目主数据与系统配置" in caps["evidence_boundary"]["not_connected"]
    assert caps["execution_contract"]["truth_source"] == "tool_calls.result 与后台 job_id/status"

    col = execute_tool(store, pid, "get_column_mapping_status", {})
    assert "missing_columns" in col
    assert "learned_alias_count" in col


def test_agent_can_drive_audit_case_with_readiness_and_approval_contract(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("AUDIT_WORKBENCH_DATA_ROOT", str(tmp_path))
    store = ProjectStore(root=tmp_path)
    pid = _seed_project(store, "Agent 审计事项")
    work = store.get_work_df(pid, 2024)
    voucher_key = str(work["_voucher_key"].iat[0])

    def add_candidate(state: dict) -> None:
        state["candidate_pool"] = [{
            "group_id": "cand_agent",
            "title": "费用发生认定异常",
            "reason": "费用凭证需核验原始依据",
            "source_module": "费用",
            "source_view": "费用结构",
            "selector": {"kind": "expense", "year": 2024},
            "voucher_ids": ["1"],
            "voucher_keys": [voucher_key],
            "row_count": 1,
            "voucher_count": 1,
        }]

    store.update_state(pid, add_candidate)
    created = execute_tool(
        store,
        pid,
        "manage_audit_case",
        {
            "action": "create_from_candidate",
            "group_id": "cand_agent",
            "actor": "preparer-a",
            "payload": {"formal": True},
        },
    )
    assert created["ok"] is True
    case_id = created["case"]["case_id"]
    assertion_id = created["case"]["assertions"][0]["assertion_id"]
    assert created["readiness"]["ready"] is False

    missing_version = execute_tool(
        store,
        pid,
        "manage_audit_case",
        {
            "action": "add_evidence",
            "case_id": case_id,
            "payload": {},
        },
    )
    assert "expected_version" in missing_version["error"]

    evidence = execute_tool(
        store,
        pid,
        "manage_audit_case",
        {
            "action": "add_evidence",
            "case_id": case_id,
            "expected_version": 1,
            "actor": "preparer-a",
            "payload": {
                "source_type": "journal",
                "source_ref": {"voucher_key": voucher_key},
                "description": "已回查序时账",
                "status": "verified",
                "assertion_ids": [assertion_id],
            },
        },
    )
    evidence_id = evidence["case"]["evidence_refs"][-1]["evidence_id"]
    procedure = execute_tool(
        store,
        pid,
        "manage_audit_case",
        {
            "action": "add_procedure",
            "case_id": case_id,
            "expected_version": 2,
            "actor": "preparer-a",
            "payload": {
                "title": "检查原始凭证",
                "description": "核对合同与审批",
                "assertion_ids": [assertion_id],
                "status": "completed",
                "result": "核对一致",
                "performed_by": "preparer-a",
            },
        },
    )
    procedure_id = procedure["case"]["procedures"][-1]["procedure_id"]
    assertion = execute_tool(
        store,
        pid,
        "manage_audit_case",
        {
            "action": "update_assertion",
            "case_id": case_id,
            "expected_version": 3,
            "actor": "preparer-a",
            "payload": {
                "assertion_id": assertion_id,
                "status": "supported",
            },
        },
    )
    assert assertion["readiness"]["ready"] is True
    no_conclusion_actor = execute_tool(
        store,
        pid,
        "manage_audit_case",
        {
            "action": "set_conclusion",
            "case_id": case_id,
            "expected_version": 4,
            "payload": {
                "outcome": "no_exception",
                "summary": "未发现例外",
                "basis_evidence_ids": [evidence_id],
                "procedure_ids": [procedure_id],
            },
        },
    )
    assert "明确提供 actor" in no_conclusion_actor["error"]
    conclusion = execute_tool(
        store,
        pid,
        "manage_audit_case",
        {
            "action": "set_conclusion",
            "case_id": case_id,
            "expected_version": 4,
            "actor": "preparer-a",
            "payload": {
                "outcome": "no_exception",
                "summary": "未发现例外",
                "basis_evidence_ids": [evidence_id],
                "procedure_ids": [procedure_id],
            },
        },
    )
    assert conclusion["case"]["status"] == "concluded"
    no_actor = execute_tool(
        store,
        pid,
        "manage_audit_case",
        {
            "action": "signoff",
            "case_id": case_id,
            "expected_version": 5,
            "payload": {"note": "复核通过"},
        },
    )
    assert "明确提供 actor" in no_actor["error"]
    signed = execute_tool(
        store,
        pid,
        "manage_audit_case",
        {
            "action": "signoff",
            "case_id": case_id,
            "expected_version": 5,
            "actor": "reviewer-b",
            "payload": {"note": "证据链完整，复核通过"},
        },
    )
    assert signed["case"]["signoffs"][-1]["status"] == "active"
    closed = execute_tool(
        store,
        pid,
        "manage_audit_case",
        {
            "action": "close",
            "case_id": case_id,
            "expected_version": 6,
            "actor": "reviewer-b",
            "payload": {"note": "完成归档"},
        },
    )
    assert closed["case"]["status"] == "closed"
    detail = execute_tool(
        store,
        pid,
        "get_audit_case_detail",
        {"case_id": case_id},
    )
    assert detail["integrity"]["valid"] is True
    assert detail["case"]["version"] == 7


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


def test_module_overview_context_avoids_unsupported_candidate_suggestion(tmp_path):
    store = ProjectStore(root=tmp_path)
    pid = _seed_project(store)

    result = set_pinned_context(
        store,
        pid,
        {
            "label": "费用 · 2024年",
            "source_module": "费用",
            "source_view": "模块概览",
            "selector": {"kind": "module_overview", "module": "expense", "year": 2024},
        },
    )

    assert any("运行" in item and "AI 风险分析" in item for item in result["suggestions"])
    assert not any("纳入疑点库" in item for item in result["suggestions"])

    suspects = set_pinned_context(
        store,
        pid,
        {
            "label": "疑点工作台",
            "source_module": "疑点工作台",
            "source_view": "候选疑点与复核状态",
            "selector": {"kind": "workspace_overview", "workspace": "suspects"},
        },
    )
    assert any("审计证据" in item for item in suspects["suggestions"])

    profile = set_pinned_context(
        store,
        pid,
        {
            "label": "统计画像 · 2024年",
            "source_module": "统计画像",
            "source_view": "模块概览",
            "selector": {"kind": "module_overview", "module": "profile", "year": 2024},
        },
    )
    assert any("本福特" in item for item in profile["suggestions"])
    assert not any("运行" in item and "AI 风险分析" in item for item in profile["suggestions"])


def test_pending_mutation_requires_resolution_and_preserves_audit_event(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_WORKBENCH_DATA_ROOT", str(tmp_path))
    store = ProjectStore(root=tmp_path)
    pid = _seed_project(store)
    state = store.load_state(pid)
    state["agent_thread"] = {
        "messages": [],
        "pending_actions": [{
            "action_id": "act_test",
            "tool": "update_rule",
            "args": {"rule_id": "large_amount", "patches": {"round_number_threshold": 900000}},
            "status": "pending",
        }],
    }
    store.save_state(pid, state)

    resolved = resolve_pending_action(store, pid, "act_test", approve=True)
    assert resolved["status"] == "approved"
    assert execute_tool(store, pid, "get_rules_catalog", {})["sampling_rules"]
    agent_state = get_agent_state(store, pid)
    assert agent_state["pending_actions"] == []
    assert agent_state["audit_events"][-1]["decision"] == "approved"


def test_detect_explicit_module_insight_request_uses_text_and_context():
    assert detect_module_insight_request("请对收入成本模块做 AI 风险分析") == ["收入成本"]
    assert detect_module_insight_request("对所有模块进行AI风险分析") == [
        "收入成本",
        "费用",
        "营业外与投资收益",
        "成本差异",
        "暂估往来",
        "资产负债",
        "调账冲销",
    ]
    assert detect_module_insight_request(
        "那你进行分析",
        {
            "label": "其他应收 · 2024年",
            "source_module": "暂估往来",
            "source_view": "月度钻取",
            "selector": {"kind": "working_capital_month"},
        },
    ) == ["暂估往来"]
    assert detect_module_insight_request("解释一下当前图表口径") == []


def test_unverified_execution_claim_is_replaced():
    reply = _sanitize_unverified_execution_claim(
        "正在执行 run_module_insight('income')，请稍候。",
        [],
    )
    assert "没有产生可核验的工具调用" in reply
    assert "正在执行" not in reply


def test_agent_job_dispatch_and_completion_are_persisted(tmp_path):
    store = ProjectStore(root=tmp_path)
    pid = _seed_project(store)
    response = record_module_insight_dispatch(
        store,
        pid,
        user_message="分析收入成本风险",
        pinned_context=None,
        jobs=[
            {
                "job_id": "ins_test",
                "module_key": "收入成本",
                "status": "queued",
                "stage": "queued",
                "percent": 0,
                "reused": False,
            }
        ],
    )
    assert response["tool_calls"][0]["result"]["job_id"] == "ins_test"
    assert "真实任务编号" in response["reply"]

    update_agent_module_insight_job(
        store,
        pid,
        job_id="ins_test",
        status="done",
        insight={
            "executive_summary": "测试摘要",
            "findings": [{"severity": "中"}],
            "recommendations": [{"title": "建议"}],
        },
    )
    state = get_agent_state(store, pid)
    result = state["messages"][-1]["tool_calls"][0]["result"]
    assert result["status"] == "done"
    assert result["insight"]["findings_count"] == 1
