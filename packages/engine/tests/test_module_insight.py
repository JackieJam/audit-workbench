from __future__ import annotations

import pandas as pd
from audit_engine.module_insight import build_module_payload, condition_to_selector


def test_condition_to_selector_expense() -> None:
    sel = condition_to_selector({"kind": "expense_category", "year": 2024, "expense_category": "人工"})
    assert sel["kind"] == "expense_category"
    assert sel["year"] == 2024
    assert sel["expense_category"] == "人工"


def test_build_expense_payload(tmp_path) -> None:
    from audit_engine.store import ProjectStore

    store = ProjectStore(root=tmp_path)
    manifest = store.create_project("payload测试")
    pid = manifest.project_id
    df = pd.DataFrame(
        {
            "凭证编号": ["1"],
            "过账日期": pd.to_datetime(["2024-06-01"]),
            "凭证货币价值": [1000.0],
            "借/贷标识": ["S"],
            "总账科目": ["660201"],
            "总账科目：长文本": ["管理费用"],
        }
    )
    store.ingest_journal(pid, {2024: df}, column_mapping={}, missing_columns=[], year_summary=[])
    payload = build_module_payload(
        store,
        pid,
        "费用",
        risk_questions=[{"id": "x", "text": "年末是否存在突击费用？"}],
    )
    assert payload["module"] == "费用"
    assert "年末是否存在突击费用" in payload["risk_focus"][0]
    assert "signature" in payload
