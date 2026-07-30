from __future__ import annotations

import io
import json
from pathlib import Path

import pandas as pd
import pytest
from audit_api.deps import get_pipeline, get_store
from audit_api.main import app
from fastapi.testclient import TestClient

client = TestClient(app)

TEST_DATA_DIR = Path("/Users/jackie_m/Downloads/序时账测试案例")
JOURNAL_2022 = TEST_DATA_DIR / "2022年6-12月序时账.XLSX"


def _minimal_xlsx() -> io.BytesIO:
    df = pd.DataFrame(
        {
            "凭证编号": ["1001", "1001"],
            "过账日期": ["2024-03-15", "2024-03-15"],
            "凭证货币价值": [1000.0, -1000.0],
            "借/贷标识": ["S", "H"],
            "总账科目": ["6001010000", "6401010000"],
            "总账科目：短文本": ["主营业务收入-第三方", "主营业务成本-第三方"],
        }
    )
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    buf.seek(0)
    buf.name = "test.xlsx"  # type: ignore[attr-defined]
    return buf


def test_income_cost_analysis_api(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AUDIT_WORKBENCH_DATA_ROOT", str(tmp_path))
    get_store.cache_clear()
    get_pipeline.cache_clear()

    pid = client.post("/projects", json={"name": "分析测试"}).json()["project_id"]
    xlsx = _minimal_xlsx()
    det = client.post(
        f"/projects/{pid}/ingest/detect",
        files=[("files", ("t.xlsx", xlsx.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))],
    ).json()
    xlsx2 = _minimal_xlsx()
    client.post(
        f"/projects/{pid}/ingest/commit",
        files=[("files", ("t.xlsx", xlsx2.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))],
        data={"column_mapping": json.dumps(det["suggested_mapping"])},
    )

    q = client.get(f"/projects/{pid}/analysis/modules/收入成本/questions")
    assert q.status_code == 200
    assert len(q.json()["questions"]) >= 1

    cats = client.get(f"/projects/{pid}/analysis/income-cost/categories", params={"year": 2024})
    assert cats.status_code == 200
    assert "总计" in cats.json()["categories"]

    monthly = client.get(
        f"/projects/{pid}/analysis/income-cost/monthly",
        params={"year": 2024, "category": "总计"},
    )
    assert monthly.status_code == 200
    assert len(monthly.json()["rows"]) == 12

    drill = client.get(
        f"/projects/{pid}/analysis/income-cost/drilldown/monthly",
        params={"year": 2024, "month": 3, "metric": "revenue", "category": "总计"},
    )
    assert drill.status_code == 200
    assert drill.json()["row_count"] >= 1

    add = client.post(
        f"/projects/{pid}/candidates",
        json={
            "title": "2024年3月净收入",
            "source_module": "收入成本",
            "source_view": "月度收入成本",
            "selector": {
                "kind": "monthly_income_cost",
                "year": 2024,
                "month": 3,
                "metric": "revenue",
                "category": "总计",
            },
            "reason": "测试入库",
            "tags": ["月度"],
        },
    )
    assert add.status_code == 200
    group_id = add.json()["group"]["group_id"]

    listed = client.get(f"/projects/{pid}/candidates")
    assert listed.status_code == 200
    assert len(listed.json()["groups"]) == 1

    deleted = client.delete(f"/projects/{pid}/candidates/{group_id}")
    assert deleted.status_code == 200
    assert client.get(f"/projects/{pid}/candidates").json()["stats"]["groups"] == 0

    quality = client.get(f"/projects/{pid}/analysis/quality")
    assert quality.status_code == 200
    assert "classification_revision" in quality.json()
    assert "allowed_categories" in quality.json()
    assert {"投资收益", "营业外收入", "营业外支出"}.issubset(
        quality.json()["allowed_categories"]
    )

    get_store.cache_clear()
    get_pipeline.cache_clear()


def test_quality_decision_api_rebuilds_classification(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AUDIT_WORKBENCH_DATA_ROOT", str(tmp_path))
    get_store.cache_clear()
    get_pipeline.cache_clear()

    store = get_store()
    manifest = store.create_project("口径决策 API")
    pid = manifest.project_id
    frame = pd.DataFrame({
        "凭证编号": ["1"],
        "过账日期": pd.to_datetime(["2024-01-01"]),
        "借/贷标识": ["S"],
        "凭证货币价值": [100.0],
        "总账科目": ["999901"],
        "总账科目：短文本": ["待分类"],
    })
    store.ingest_journal(pid, {2024: frame}, column_mapping={}, missing_columns=[], year_summary=[])

    response = client.post(
        f"/projects/{pid}/analysis/quality/decisions",
        json={
            "decisions": [{
                "account_code": "999901",
                "account_name": "待分类",
                "decision": "map",
                "category": "费用",
                "rationale": "测试确认",
            }],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["recalculation"]["applied_count"] == 1
    assert payload["years"]["2024"]["review_required_amount"] == 0
    assert store.get_work_df(pid, 2024)["_acct_category"].eq("费用").all()
    get_store.cache_clear()
    get_pipeline.cache_clear()


def test_multi_currency_scope_unblocks_charts_without_mixing_amounts(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AUDIT_WORKBENCH_DATA_ROOT", str(tmp_path))
    get_store.cache_clear()
    get_pipeline.cache_clear()

    store = get_store()
    manifest = store.create_project("多币种画像")
    pid = manifest.project_id
    frame = pd.DataFrame([
        {
            "凭证编号": "CNY-1", "过账日期": pd.Timestamp("2024-01-10"),
            "借/贷标识": "S", "凭证货币价值": 100.0, "凭证货币代码": "CNY",
            "总账科目": "112201", "总账科目：短文本": "应收账款",
        },
        {
            "凭证编号": "CNY-1", "过账日期": pd.Timestamp("2024-01-10"),
            "借/贷标识": "H", "凭证货币价值": 100.0, "凭证货币代码": "CNY",
            "总账科目": "600101", "总账科目：短文本": "主营业务收入",
        },
        {
            "凭证编号": "USD-1", "过账日期": pd.Timestamp("2024-01-11"),
            "借/贷标识": "S", "凭证货币价值": 10.0, "凭证货币代码": "USD",
            "总账科目": "112201", "总账科目：短文本": "应收账款",
        },
        {
            "凭证编号": "USD-1", "过账日期": pd.Timestamp("2024-01-11"),
            "借/贷标识": "H", "凭证货币价值": 10.0, "凭证货币代码": "USD",
            "总账科目": "600101", "总账科目：短文本": "主营业务收入",
        },
    ])
    store.ingest_journal(
        pid,
        {2024: frame},
        column_mapping={},
        missing_columns=[],
        year_summary=[],
    )

    quality = client.get(f"/projects/{pid}/analysis/quality").json()
    assert quality["analysis_currency_scope"] is None
    assert quality["currency_overview"]["2024"]["mixed_document_currency"] is True
    assert {
        item["currency"]
        for item in quality["currency_overview"]["2024"]["currency_distribution"]
    } == {"CNY", "USD"}

    blocked = client.get(
        f"/projects/{pid}/analysis/income-cost/monthly",
        params={"year": 2024, "category": "总计"},
    )
    assert blocked.status_code == 409

    selected = client.post(
        f"/projects/{pid}/analysis/currency-scope",
        json={"currency": "CNY"},
    )
    assert selected.status_code == 200
    assert selected.json()["analysis_currency_scope"] == "CNY"

    cny = client.get(
        f"/projects/{pid}/analysis/income-cost/monthly",
        params={"year": 2024, "category": "总计"},
    ).json()["rows"]
    assert sum(row["净收入"] for row in cny) == 100.0

    switched = client.post(
        f"/projects/{pid}/analysis/currency-scope",
        json={"currency": "USD"},
    )
    assert switched.status_code == 200
    usd = client.get(
        f"/projects/{pid}/analysis/income-cost/monthly",
        params={"year": 2024, "category": "总计"},
    ).json()["rows"]
    assert sum(row["净收入"] for row in usd) == 10.0

    get_store.cache_clear()
    get_pipeline.cache_clear()


@pytest.mark.slow
@pytest.mark.skipif(not JOURNAL_2022.exists(), reason="本地测试案例目录不可用")
def test_real_journal_2022_monthly(tmp_path, monkeypatch) -> None:
    """使用 Downloads 序时账测试案例做集成验证（较慢）。"""
    monkeypatch.setenv("AUDIT_WORKBENCH_DATA_ROOT", str(tmp_path))
    get_store.cache_clear()
    get_pipeline.cache_clear()

    pid = client.post("/projects", json={"name": "真实案例_2022"}).json()["project_id"]
    with open(JOURNAL_2022, "rb") as f:
        data = f.read()
    det = client.post(
        f"/projects/{pid}/ingest/detect",
        files=[("files", (JOURNAL_2022.name, data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))],
    )
    assert det.status_code == 200
    mapping = det.json()["suggested_mapping"]
    assert "过账日期" in mapping

    commit = client.post(
        f"/projects/{pid}/ingest/commit",
        files=[("files", (JOURNAL_2022.name, data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))],
        data={"column_mapping": json.dumps(mapping)},
    )
    assert commit.status_code == 200
    years = commit.json()["years"]
    assert years

    year = years[0]
    monthly = client.get(
        f"/projects/{pid}/analysis/income-cost/monthly",
        params={"year": year, "category": "总计"},
    )
    assert monthly.status_code == 200
    rows = monthly.json()["rows"]
    assert len(rows) == 12
    assert any(abs(r["净收入"]) > 0 for r in rows)

    get_store.cache_clear()
    get_pipeline.cache_clear()
