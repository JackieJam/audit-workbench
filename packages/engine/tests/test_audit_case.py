from __future__ import annotations

import copy
from pathlib import Path

import pandas as pd
import pytest
from audit_engine.audit_case import (
    AUDIT_CASE_STATE_KEY,
    EVIDENCE_STATUS_STALE,
    AuditCaseService,
    CaseVersionConflict,
)
from audit_engine.store import ProjectStore


@pytest.fixture
def store(tmp_path: Path) -> ProjectStore:
    return ProjectStore(root=tmp_path)


def _journal(amount: float = 100.0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "公司代码": ["1000", "1000"],
            "会计年度": ["2024", "2024"],
            "凭证编号": ["900001", "900001"],
            "行项目": ["1", "2"],
            "过账日期": pd.to_datetime(["2024-01-15", "2024-01-15"]),
            "借/贷标识": ["S", "H"],
            "凭证货币价值": [amount, -amount],
            "总账科目": ["600101", "100201"],
            "总账科目：长文本": ["管理费用", "银行存款"],
            "文本": ["咨询费", "咨询费付款"],
        }
    )


def _ingest(store: ProjectStore, project_id: str, amount: float = 100.0) -> None:
    store.ingest_journal(
        project_id,
        {2024: _journal(amount)},
        column_mapping={},
        missing_columns=[],
        year_summary=[],
    )


def _voucher_key(store: ProjectStore, project_id: str) -> str:
    return str(store.get_work_df(project_id, 2024)["_voucher_key"].iat[0])


def _project_with_candidate(store: ProjectStore) -> tuple[str, str]:
    pid = store.create_project("案件测试").project_id
    _ingest(store, pid)
    group_id = "cand_001"

    def update(state: dict) -> None:
        state["candidate_pool"] = [
            {
                "group_id": group_id,
                "title": "年末收入异常",
                "source_module": "收入成本",
                "source_view": "月度收入",
                "selector": {
                    "kind": "monthly_income_cost",
                    "year": 2024,
                    "month": 12,
                },
                "tags": ["收入截止"],
                "reason": "12 月收入显著高于其他月份",
                "voucher_ids": ["900001"],
                "row_count": 2,
                "voucher_count": 1,
                "amount_total": 200.0,
            }
        ]

    store.update_state(pid, update)
    return pid, group_id


def _formal_case(
    store: ProjectStore,
    *,
    title: str = "大额手工分录",
) -> tuple[str, AuditCaseService, str, str]:
    pid = store.create_project(title).project_id
    _ingest(store, pid)
    service = AuditCaseService(store)
    created = service.create_case(
        pid,
        title=title,
        risk_statement="管理层可能通过手工分录调节利润",
        financial_statement_assertions=["发生", "准确性"],
        proposal=False,
        materiality="high",
        actor="preparer-a",
    )
    case = created["case"]
    return pid, service, case["case_id"], case["assertions"][0]["assertion_id"]


def _complete_case(
    store: ProjectStore,
) -> tuple[str, AuditCaseService, str, str, str, str]:
    pid, service, case_id, assertion_id = _formal_case(store)
    evidence_result = service.add_evidence(
        pid,
        case_id,
        source_type="journal",
        source_ref={"voucher_key": _voucher_key(store, pid)},
        description="已回查序时账凭证及其成对分录",
        status="verified",
        assertion_ids=[assertion_id],
        actor="preparer-a",
    )
    evidence_id = evidence_result["case"]["evidence_refs"][-1]["evidence_id"]
    procedure_result = service.add_procedure(
        pid,
        case_id,
        title="检查原始凭证和审批",
        description="核对合同、发票、审批链与入账期间",
        assertion_ids=[assertion_id],
        status="completed",
        result="合同、发票与审批链一致",
        performed_by="preparer-a",
        actor="preparer-a",
    )
    procedure_id = procedure_result["case"]["procedures"][-1]["procedure_id"]
    service.update_assertion(
        pid,
        case_id,
        assertion_id,
        status="supported",
        actor="preparer-a",
    )
    service.set_conclusion(
        pid,
        case_id,
        outcome="no_exception",
        summary="已执行程序，未发现例外",
        basis_evidence_ids=[evidence_id],
        procedure_ids=[procedure_id],
        actor="preparer-a",
    )
    return pid, service, case_id, assertion_id, evidence_id, procedure_id


def test_candidate_proposal_is_idempotent_traceable_and_not_evidence(
    store: ProjectStore,
) -> None:
    pid, group_id = _project_with_candidate(store)
    service = AuditCaseService(store)
    created = service.create_from_candidate(
        pid,
        group_id,
        financial_statement_assertions=["发生", "截止"],
        materiality="high",
        actor="preparer-a",
    )
    case = created["case"]
    locator = case["evidence_refs"][0]["source_ref"]["locator"]

    assert case["kind"] == "proposal"
    assert case["status"] == "draft"
    assert locator["voucher_ids"] == ["900001"]
    assert locator["voucher_keys"] == [_voucher_key(store, pid)]
    assert locator["candidate_snapshot_digest"]
    assert created["readiness"]["ready"] is False
    assert any(
        blocker["code"] == "missing_verified_evidence"
        for blocker in created["readiness"]["blockers"]
    )

    repeated = service.create_from_candidate(pid, group_id, actor="preparer-a")
    assert repeated["case"]["case_id"] == case["case_id"]
    assert repeated["case"]["version"] == 1

    with pytest.raises(ValueError, match="expected_version"):
        service.create_from_candidate(
            pid,
            group_id,
            formal=True,
            actor="reviewer-b",
        )
    promoted = service.promote_case(
        pid,
        case["case_id"],
        actor="reviewer-b",
        comment="同意立项",
        expected_version=1,
    )
    assert promoted["case"]["kind"] == "case"
    assert promoted["case"]["status"] == "planned"
    with pytest.raises(ValueError, match="实质性结论"):
        service.set_conclusion(
            pid,
            case["case_id"],
            outcome="no_exception",
            summary="风险信号不能直接成为结论",
        )


def test_substantive_conclusion_requires_linked_evidence_and_procedure(
    store: ProjectStore,
) -> None:
    pid, service, case_id, assertion_id = _formal_case(store)
    second = service.add_assertion(
        pid,
        case_id,
        title="授权审批",
        risk_statement="凭证可能缺少适当审批",
        actor="preparer-a",
    )["case"]["assertions"][-1]["assertion_id"]
    evidence_id = service.add_evidence(
        pid,
        case_id,
        source_type="journal",
        source_ref={"voucher_key": _voucher_key(store, pid)},
        description="已回查序时账",
        status="verified",
        assertion_ids=[assertion_id],
        actor="preparer-a",
    )["case"]["evidence_refs"][-1]["evidence_id"]
    procedure_id = service.add_procedure(
        pid,
        case_id,
        title="检查凭证",
        description="检查原始凭证与审批",
        assertion_ids=[assertion_id],
        status="completed",
        result="检查完成",
        performed_by="preparer-a",
        actor="preparer-a",
    )["case"]["procedures"][-1]["procedure_id"]
    service.update_assertion(
        pid, case_id, assertion_id, status="supported", actor="preparer-a"
    )
    service.update_assertion(
        pid, case_id, second, status="supported", actor="preparer-a"
    )

    readiness = service.case_readiness(pid, case_id)
    assert readiness["ready"] is False
    assert second in readiness["evidence_coverage"]
    assert readiness["evidence_coverage"][second] == []
    with pytest.raises(ValueError, match="部分认定"):
        service.set_conclusion(
            pid,
            case_id,
            outcome="no_exception",
            summary="第二项认定未覆盖",
            basis_evidence_ids=[evidence_id],
            procedure_ids=[procedure_id],
        )


def test_complete_case_requires_independent_signoff_and_then_becomes_immutable(
    store: ProjectStore,
) -> None:
    pid, service, case_id, _assertion_id, _evidence_id, _procedure_id = (
        _complete_case(store)
    )
    case = service.get_case(pid, case_id)["case"]
    assert case["status"] == "concluded"
    assert service.case_readiness(pid, case_id)["ready"] is True

    with pytest.raises(ValueError, match="复核意见"):
        service.record_review(
            pid,
            case_id,
            action="review",
            actor="reviewer-b",
            note="",
            expected_version=case["version"],
        )
    with pytest.raises(ValueError, match="独立"):
        service.record_review(
            pid,
            case_id,
            action="signoff",
            actor="PREPARER-A",
            note="自行复核",
            expected_version=case["version"],
        )
    signed = service.record_review(
        pid,
        case_id,
        action="signoff",
        actor="reviewer-b",
        note="证据与程序覆盖充分，复核通过",
        expected_version=case["version"],
    )
    assert signed["case"]["signoffs"][-1]["status"] == "active"
    closed = service.update_case(
        pid,
        case_id,
        status="closed",
        actor="reviewer-b",
        note="归档",
        expected_version=signed["case"]["version"],
    )
    assert closed["case"]["status"] == "closed"
    with pytest.raises(ValueError, match="不可再修改"):
        service.update_case(
            pid,
            case_id,
            owner="new-owner",
            expected_version=closed["case"]["version"],
        )


def test_pending_conclusion_is_not_signable(store: ProjectStore) -> None:
    pid, service, case_id, assertion_id = _formal_case(store)
    with pytest.raises(ValueError, match="至少一项未解决认定"):
        service.set_conclusion(
            pid,
            case_id,
            outcome="scope_limitation",
            summary="没有明确范围的范围受限结论",
            unresolved_assertion_ids=[],
            actor="preparer-a",
        )
    with pytest.raises(ValueError, match="错报金额"):
        service.set_conclusion(
            pid,
            case_id,
            outcome="misstatement",
            summary="未量化的错报",
            actor="preparer-a",
        )
    with pytest.raises(ValueError, match="不支持的结论类型"):
        service.set_conclusion(
            pid,
            case_id,
            outcome="looks_fine",
            summary="非法结论枚举",
            actor="preparer-a",
        )
    pending = service.set_conclusion(
        pid,
        case_id,
        outcome="scope_limitation",
        summary="关键合同尚未提供，当前范围受限",
        unresolved_assertion_ids=[assertion_id],
        actor="preparer-a",
    )
    assert pending["case"]["status"] == "pending_evidence"
    assert pending["case"]["conclusion"]["unresolved_assertion_ids"] == [assertion_id]
    with pytest.raises(ValueError, match="有效的实质性结论"):
        service.record_review(
            pid,
            case_id,
            action="signoff",
            actor="reviewer-b",
            note="不能签署",
        )


def test_evidence_resolver_rejects_fake_or_row_only_references(
    store: ProjectStore,
) -> None:
    pid, service, case_id, assertion_id = _formal_case(store)
    with pytest.raises(ValueError, match="行号不是稳定"):
        service.add_evidence(
            pid,
            case_id,
            source_type="journal",
            source_ref={"source_row": 18},
            description="孤立行号",
            assertion_ids=[assertion_id],
        )
    with pytest.raises(ValueError, match="来源资产不存在"):
        service.add_evidence(
            pid,
            case_id,
            source_type="journal",
            source_ref={
                "source_asset_id": "src_fake",
                "sheet": "序时账",
                "source_row": 18,
            },
            description="伪造资产",
            assertion_ids=[assertion_id],
        )
    with pytest.raises(ValueError, match="无法在当前事实层"):
        service.add_evidence(
            pid,
            case_id,
            source_type="journal",
            source_ref={"voucher_key": "不存在的凭证键"},
            description="错误凭证",
            assertion_ids=[assertion_id],
        )

    valid = service.add_evidence(
        pid,
        case_id,
        source_type="journal",
        source_ref={"voucher_key": _voucher_key(store, pid)},
        description="当前事实层凭证",
        status="verified",
        assertion_ids=[assertion_id],
    )
    assert valid["case"]["evidence_refs"][-1]["source_ref"]["voucher_key"]

    missing = service.add_evidence(
        pid,
        case_id,
        source_type="attachment",
        source_ref={},
        description="尚待取得原始合同",
        status="missing",
        assertion_ids=[assertion_id],
    )
    assert missing["case"]["status"] == "pending_evidence"


def test_management_explanation_cannot_be_the_only_substantive_evidence(
    store: ProjectStore,
) -> None:
    pid, service, case_id, assertion_id = _formal_case(store)
    with pytest.raises(ValueError, match="不支持的证据来源类型"):
        service.add_evidence(
            pid,
            case_id,
            source_type="model_claim",
            source_ref={"locator": {"message_id": "m-1"}},
            description="模型自行生成的说明",
            status="verified",
            assertion_ids=[assertion_id],
        )

    service.add_evidence(
        pid,
        case_id,
        source_type="management_explanation",
        source_ref={"locator": {"meeting": "2024-01-20-CFO"}},
        description="管理层解释称该笔交易具有商业实质",
        status="verified",
        assertion_ids=[assertion_id],
        actor="preparer-a",
    )
    service.add_procedure(
        pid,
        case_id,
        title="询问管理层",
        description="了解交易背景并记录解释",
        assertion_ids=[assertion_id],
        status="completed",
        result="已取得管理层解释，但尚无独立佐证",
        performed_by="preparer-a",
        actor="preparer-a",
    )
    service.update_assertion(
        pid,
        case_id,
        assertion_id,
        status="supported",
        actor="preparer-a",
    )

    readiness = service.case_readiness(pid, case_id)
    assert readiness["verified_evidence_count"] == 0
    assert any(
        blocker["code"] == "missing_verified_evidence"
        for blocker in readiness["blockers"]
    )
    with pytest.raises(ValueError, match="实质性证据"):
        service.set_conclusion(
            pid,
            case_id,
            outcome="no_exception",
            summary="不能仅凭管理层解释形成无异常结论",
            actor="preparer-a",
        )


def test_conclusion_outcome_must_match_assertion_judgments(
    store: ProjectStore,
) -> None:
    pid, service, case_id, assertion_id = _formal_case(store)
    service.add_evidence(
        pid,
        case_id,
        source_type="journal",
        source_ref={"voucher_key": _voucher_key(store, pid)},
        description="已回查序时账凭证",
        status="verified",
        assertion_ids=[assertion_id],
        actor="preparer-a",
    )
    service.add_procedure(
        pid,
        case_id,
        title="检查支持性资料",
        description="检查合同、发票和审批记录",
        assertion_ids=[assertion_id],
        status="completed",
        result="发现审批记录缺失",
        performed_by="preparer-a",
        actor="preparer-a",
    )
    service.update_assertion(
        pid,
        case_id,
        assertion_id,
        status="exception",
        actor="preparer-a",
    )

    with pytest.raises(ValueError, match="未发现异常"):
        service.set_conclusion(
            pid,
            case_id,
            outcome="no_exception",
            summary="与认定判断矛盾的结论",
            actor="preparer-a",
        )
    concluded = service.set_conclusion(
        pid,
        case_id,
        outcome="finding",
        summary="审批控制执行存在例外",
        actor="preparer-a",
    )
    assert concluded["case"]["status"] == "concluded"
    assert concluded["case"]["conclusion"]["outcome"] == "finding"


def test_expected_version_prevents_lost_updates_and_noop_has_no_event(
    store: ProjectStore,
) -> None:
    pid, service, case_id, _assertion_id = _formal_case(store)
    initial = service.get_case(pid, case_id)["case"]
    updated = service.update_case(
        pid,
        case_id,
        owner="preparer-a",
        expected_version=initial["version"],
    )
    with pytest.raises(CaseVersionConflict, match="版本冲突"):
        service.update_case(
            pid,
            case_id,
            materiality="low",
            expected_version=initial["version"],
        )
    event_count = len(service.list_events(pid, case_id))
    no_op = service.update_case(
        pid,
        case_id,
        owner="preparer-a",
        expected_version=updated["case"]["version"],
    )
    assert no_op["case"]["version"] == updated["case"]["version"]
    assert len(service.list_events(pid, case_id)) == event_count


def test_reingest_stales_data_evidence_conclusion_and_signoff(
    store: ProjectStore,
) -> None:
    pid, service, case_id, assertion_id, _evidence_id, _procedure_id = (
        _complete_case(store)
    )
    explanation = service.add_evidence(
        pid,
        case_id,
        source_type="management_explanation",
        source_ref={"locator": {"meeting": "2024-01-20-CFO"}},
        description="管理层书面解释已由项目组留档",
        status="verified",
        assertion_ids=[assertion_id],
        actor="preparer-a",
    )
    # Adding evidence invalidates the previous conclusion, so reform it with all current evidence.
    reconcluded = service.set_conclusion(
        pid,
        case_id,
        outcome="no_exception",
        summary="补充说明与原始凭证相互印证",
        actor="preparer-a",
        expected_version=explanation["case"]["version"],
    )
    signed = service.record_review(
        pid,
        case_id,
        action="signoff",
        actor="reviewer-b",
        note="独立复核通过",
        expected_version=reconcluded["case"]["version"],
    )
    assert signed["case"]["signoffs"][-1]["status"] == "active"

    _ingest(store, pid, amount=250.0)
    case = service.get_case(pid, case_id)["case"]
    journal_evidence = next(
        item for item in case["evidence_refs"] if item["source_type"] == "journal"
    )
    explanation_evidence = next(
        item
        for item in case["evidence_refs"]
        if item["source_type"] == "management_explanation"
    )
    assert journal_evidence["status"] == EVIDENCE_STATUS_STALE
    assert explanation_evidence["status"] == "verified"
    assert case["status"] == "pending_evidence"
    assert case["conclusion"]["status"] == "stale"
    assert case["signoffs"][-1]["status"] == "stale"


@pytest.mark.parametrize(
    ("tamper", "failed_check"),
    [
        (
            lambda raw: raw["events"][0]["payload"].update({"source": "tampered"}),
            "event_chain",
        ),
        (
            lambda raw: raw["cases"][next(iter(raw["cases"]))].update(
                {"title": "tampered"}
            ),
            "latest_snapshot_matches_case",
        ),
        (
            lambda raw: raw["events"].pop(),
            "anchor_matches",
        ),
        (
            lambda raw: raw["snapshots"][0]["case_state"].update(
                {"title": "tampered"}
            ),
            "snapshot_digests",
        ),
    ],
)
def test_integrity_verifier_detects_event_case_tail_and_snapshot_tampering(
    store: ProjectStore,
    tamper,
    failed_check: str,
) -> None:
    pid, service, case_id, _assertion_id = _formal_case(store)
    service.update_case(pid, case_id, owner="preparer-a")
    assert service.verify_event_chain(pid, case_id)["valid"] is True
    state = copy.deepcopy(store.load_state(pid))
    raw = state[AUDIT_CASE_STATE_KEY]
    tamper(raw)
    store.save_state(pid, state)
    invalid = service.verify_event_chain(pid, case_id)
    assert invalid["valid"] is False
    assert invalid["checks"][failed_check] is False
    current_version = service.get_case(pid, case_id)["case"]["version"]
    with pytest.raises(ValueError, match="完整性校验失败"):
        service.update_case(
            pid,
            case_id,
            owner="attacker",
            expected_version=current_version,
        )
