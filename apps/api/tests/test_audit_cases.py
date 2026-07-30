from __future__ import annotations

import pandas as pd
from audit_api.deps import get_pipeline, get_store
from audit_api.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


def _journal() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "公司代码": ["1000", "1000"],
            "会计年度": ["2024", "2024"],
            "凭证编号": ["1001", "1001"],
            "行项目": ["1", "2"],
            "过账日期": pd.to_datetime(["2024-12-20", "2024-12-20"]),
            "借/贷标识": ["S", "H"],
            "凭证货币价值": [100000.0, -100000.0],
            "总账科目": ["660201", "100201"],
            "总账科目：长文本": ["咨询费", "银行存款"],
            "文本": ["年末咨询费", "支付咨询费"],
        }
    )


def _project(tmp_path, monkeypatch) -> tuple[str, str]:
    monkeypatch.setenv("AUDIT_WORKBENCH_DATA_ROOT", str(tmp_path))
    get_store.cache_clear()
    get_pipeline.cache_clear()
    pid = client.post("/projects", json={"name": "案件 API 测试"}).json()["project_id"]
    store = get_store()
    store.ingest_journal(
        pid,
        {2024: _journal()},
        column_mapping={},
        missing_columns=[],
        year_summary=[],
    )
    voucher_key = str(store.get_work_df(pid, 2024)["_voucher_key"].iat[0])
    return pid, voucher_key


def test_audit_case_api_enforces_full_evidence_lifecycle(
    tmp_path,
    monkeypatch,
) -> None:
    pid, voucher_key = _project(tmp_path, monkeypatch)
    created = client.post(
        f"/projects/{pid}/audit-cases",
        json={
            "title": "敏感费用异常",
            "risk_statement": "年末咨询费可能缺乏商业实质",
            "financial_statement_assertions": ["发生", "准确性"],
            "proposal": False,
            "actor": "preparer-a",
        },
    )
    assert created.status_code == 200
    case = created.json()
    case_id = case["case_id"]
    assertion_id = case["assertions"][0]["assertion_id"]
    assert case["version"] == 1
    assert case["readiness"]["ready"] is False

    missing_version = client.post(
        f"/projects/{pid}/audit-cases/{case_id}/evidence",
        json={
            "source_type": "journal",
            "source_ref": {"voucher_key": voucher_key},
            "description": "缺少并发版本的写入",
            "assertion_ids": [assertion_id],
        },
    )
    assert missing_version.status_code == 422

    evidence = client.post(
        f"/projects/{pid}/audit-cases/{case_id}/evidence",
        json={
            "source_type": "journal",
            "source_ref": {"voucher_key": voucher_key},
            "description": "已回查原始序时账凭证",
            "status": "active",
            "assertion_ids": [assertion_id],
            "actor": "preparer-a",
            "expected_version": 1,
        },
    )
    assert evidence.status_code == 200
    evidence_id = evidence.json()["evidence_refs"][-1]["evidence_id"]
    assert evidence.json()["evidence"][-1]["status"] == "active"

    verified = client.patch(
        f"/projects/{pid}/audit-cases/{case_id}/evidence/{evidence_id}",
        json={
            "status": "verified",
            "actor": "preparer-a",
            "expected_version": 2,
        },
    )
    assert verified.status_code == 200
    assert verified.json()["evidence"][-1]["status"] == "verified"

    procedure = client.post(
        f"/projects/{pid}/audit-cases/{case_id}/procedures",
        json={
            "title": "检查合同、发票及审批",
            "description": "核对合同、发票、审批链与入账期间",
            "assertion_ids": [assertion_id],
            "status": "planned",
            "actor": "preparer-a",
            "expected_version": 3,
        },
    )
    assert procedure.status_code == 200
    procedure_id = procedure.json()["procedures"][-1]["procedure_id"]

    completed = client.patch(
        f"/projects/{pid}/audit-cases/{case_id}/procedures/{procedure_id}",
        json={
            "status": "completed",
            "result": "合同、发票与审批链一致",
            "performed_by": "preparer-a",
            "actor": "preparer-a",
            "expected_version": 4,
        },
    )
    assert completed.status_code == 200
    assert completed.json()["procedures"][-1]["status"] == "completed"

    assertion = client.patch(
        f"/projects/{pid}/audit-cases/{case_id}/assertions/{assertion_id}",
        json={
            "status": "supported",
            "actor": "preparer-a",
            "expected_version": 5,
        },
    )
    assert assertion.status_code == 200
    assert assertion.json()["readiness"]["ready"] is True

    stale_write = client.patch(
        f"/projects/{pid}/audit-cases/{case_id}",
        json={
            "owner": "somebody",
            "actor": "preparer-a",
            "expected_version": 4,
        },
    )
    assert stale_write.status_code == 409
    assert "版本冲突" in stale_write.json()["detail"]

    missing_conclusion_actor = client.put(
        f"/projects/{pid}/audit-cases/{case_id}/conclusion",
        json={
            "outcome": "no_exception",
            "summary": "未记录结论编制人",
            "basis_evidence_ids": [evidence_id],
            "procedure_ids": [procedure_id],
            "expected_version": 6,
        },
    )
    assert missing_conclusion_actor.status_code == 422

    conclusion = client.put(
        f"/projects/{pid}/audit-cases/{case_id}/conclusion",
        json={
            "outcome": "no_exception",
            "summary": "已执行程序，未发现例外",
            "basis_evidence_ids": [evidence_id],
            "procedure_ids": [procedure_id],
            "actor": "preparer-a",
            "expected_version": 6,
        },
    )
    assert conclusion.status_code == 200
    assert conclusion.json()["status"] == "concluded"

    self_sign = client.post(
        f"/projects/{pid}/audit-cases/{case_id}/events",
        json={
            "event_type": "signoff",
            "actor": "preparer-a",
            "note": "自行复核",
            "expected_version": 7,
        },
    )
    assert self_sign.status_code == 400
    assert "独立" in self_sign.json()["detail"]

    signoff = client.post(
        f"/projects/{pid}/audit-cases/{case_id}/events",
        json={
            "event_type": "signoff",
            "actor": "reviewer-b",
            "note": "证据链覆盖完整，复核通过",
            "expected_version": 7,
        },
    )
    assert signoff.status_code == 200
    assert signoff.json()["event"]["canonical_event_type"] == "case.signed_off"
    assert signoff.json()["integrity"]["valid"] is True

    closed = client.patch(
        f"/projects/{pid}/audit-cases/{case_id}",
        json={
            "status": "closed",
            "actor": "reviewer-b",
            "note": "完成归档",
            "expected_version": 8,
        },
    )
    assert closed.status_code == 200
    assert closed.json()["status"] == "closed"

    blocked = client.patch(
        f"/projects/{pid}/audit-cases/{case_id}",
        json={
            "owner": "new-owner",
            "actor": "preparer-a",
            "expected_version": 9,
        },
    )
    assert blocked.status_code == 400
    assert "不可再修改" in blocked.json()["detail"]

    events = client.get(f"/projects/{pid}/audit-cases/{case_id}/events")
    snapshots = client.get(f"/projects/{pid}/audit-cases/{case_id}/snapshots")
    integrity = client.get(f"/projects/{pid}/audit-cases/{case_id}/integrity")
    assert events.json()["count"] == 9
    assert snapshots.json()["count"] == 9
    assert integrity.json()["valid"] is True

    get_store.cache_clear()
    get_pipeline.cache_clear()


def test_audit_case_api_rejects_fake_source_asset(tmp_path, monkeypatch) -> None:
    pid, _voucher_key = _project(tmp_path, monkeypatch)
    created = client.post(
        f"/projects/{pid}/audit-cases",
        json={
            "title": "来源核验",
            "risk_statement": "验证伪来源",
            "proposal": False,
            "actor": "preparer-a",
        },
    ).json()
    response = client.post(
        f"/projects/{pid}/audit-cases/{created['case_id']}/evidence",
        json={
            "source_type": "journal",
            "source_ref": {
                "source_asset_id": "src_fake",
                "sheet": "序时账",
                "source_row": 5,
            },
            "description": "伪来源",
            "assertion_ids": [created["assertions"][0]["assertion_id"]],
            "expected_version": 1,
        },
    )
    assert response.status_code == 400
    assert "来源资产不存在" in response.json()["detail"]
    get_store.cache_clear()
    get_pipeline.cache_clear()


def test_create_case_from_missing_candidate_returns_404(tmp_path, monkeypatch) -> None:
    pid, _voucher_key = _project(tmp_path, monkeypatch)
    response = client.post(
        f"/projects/{pid}/audit-cases/from-candidate/missing",
        json={},
    )
    assert response.status_code == 404
    assert "疑点组不存在" in response.json()["detail"]
    get_store.cache_clear()
    get_pipeline.cache_clear()
