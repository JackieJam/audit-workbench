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


def test_build_balance_sheet_payload_and_selectors(tmp_path) -> None:
    from audit_engine.store import ProjectStore

    store = ProjectStore(root=tmp_path)
    manifest = store.create_project("资产负债payload测试")
    pid = manifest.project_id
    df = pd.DataFrame(
        {
            "凭证编号": ["1", "1"],
            "过账日期": pd.to_datetime(["2024-06-01", "2024-06-01"]),
            "凭证货币价值": [1000.0, 1000.0],
            "借/贷标识": ["S", "H"],
            "总账科目": ["100201", "220201"],
            "总账科目：长文本": ["银行存款", "应付账款"],
        }
    )
    store.ingest_journal(pid, {2024: df}, column_mapping={}, missing_columns=[], year_summary=[])

    payload = build_module_payload(
        store,
        pid,
        "资产负债",
        risk_questions=[{"id": "x", "text": "是否存在异常大额单边发生？"}],
    )

    assert payload["balance_sheet"][0]["year"] == 2024
    categories = payload["balance_sheet"][0]["categories"]
    assert categories
    assert all("monthly" in item and "top_accounts" in item for item in categories)

    month_selector = condition_to_selector(
        {
            "kind": "bs_category_month",
            "year": 2024,
            "category": categories[0]["category"],
            "month": 6,
            "direction": "net",
        }
    )
    assert month_selector["kind"] == "bs_category_month"
    assert month_selector["month"] == 6
    assert month_selector["direction"] == "net"

    account_selector = condition_to_selector(
        {
            "kind": "bs_category_account",
            "year": 2024,
            "category": categories[0]["category"],
            "account": "100201",
        }
    )
    assert account_selector["kind"] == "bs_category_account"
    assert account_selector["account"] == "100201"


def test_build_cost_variance_payload_and_selector(tmp_path) -> None:
    from audit_engine.store import ProjectStore

    store = ProjectStore(root=tmp_path)
    manifest = store.create_project("成本差异payload测试")
    pid = manifest.project_id
    df = pd.DataFrame(
        {
            "凭证编号": ["V1", "V1"],
            "过账日期": pd.to_datetime(["2024-12-31", "2024-12-31"]),
            "凭证货币价值": [1000.0, 1000.0],
            "借/贷标识": ["S", "H"],
            "总账科目": ["699009", "640198"],
            "总账科目：长文本": ["差异-差异结转", "主营业务成本-其他"],
        }
    )
    store.ingest_journal(pid, {2024: df}, column_mapping={}, missing_columns=[], year_summary=[])
    payload = build_module_payload(
        store,
        pid,
        "成本差异",
        risk_questions=[{"id": "x", "text": "是否存在异常利润调节？"}],
    )

    item = payload["yearly_cost_variance"][0]
    assert item["year"] == 2024
    assert item["summary"]["cogs_impact"] == -1000
    selector = condition_to_selector(
        {"kind": "cost_variance_month", "year": 2024, "month": 12, "metric": "cogs"}
    )
    assert selector == {
        "kind": "cost_variance_month",
        "year": 2024,
        "month": 12,
        "metric": "cogs",
    }
