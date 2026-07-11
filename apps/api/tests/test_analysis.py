from __future__ import annotations

import io
import json
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from audit_api.deps import get_pipeline, get_store
from audit_api.main import app

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
