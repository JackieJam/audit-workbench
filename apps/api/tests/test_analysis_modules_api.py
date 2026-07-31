"""分析子模块 API 集成测试。"""

from __future__ import annotations

import io
import json

import pandas as pd
from audit_api.deps import get_pipeline, get_store
from audit_api.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


def _ingest_sample(tmp_path, monkeypatch) -> str:
    monkeypatch.setenv("AUDIT_WORKBENCH_DATA_ROOT", str(tmp_path))
    get_store.cache_clear()
    get_pipeline.cache_clear()

    pid = client.post("/projects", json={"name": "分析模块"}).json()["project_id"]
    df = pd.DataFrame(
        [
            {
                "凭证编号": "100",
                "过账日期": "2023-01-15",
                "凭证类型": "SA",
                "文本": "销售",
                "总账科目": "600101",
                "总账科目：长文本": "主营业务收入",
                "借/贷标识": "H",
                "凭证货币价值": 100000,
                "客户": "C1",
                "客户科目：姓名 1": "客户甲",
            },
            {
                "凭证编号": "200",
                "过账日期": "2023-02-10",
                "凭证类型": "SA",
                "文本": "管理费用",
                "总账科目": "660201",
                "总账科目：长文本": "管理费用-差旅",
                "借/贷标识": "S",
                "凭证货币价值": 5000,
            },
            {
                "凭证编号": "300",
                "过账日期": "2023-03-01",
                "凭证类型": "SA",
                "文本": "应付暂估",
                "总账科目": "220201",
                "总账科目：长文本": "应付账款-暂估",
                "借/贷标识": "H",
                "凭证货币价值": 8000,
                "供应商编号": "V1",
                "供应商科目：名称 1": "供应商A",
            },
            {
                "凭证编号": "400",
                "过账日期": "2023-12-31",
                "凭证类型": "SA",
                "文本": "年末调整",
                "总账科目": "100101",
                "总账科目：长文本": "银行存款",
                "借/贷标识": "S",
                "凭证货币价值": 1000,
            },
        ]
    )
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    buf.seek(0)
    det = client.post(
        f"/projects/{pid}/ingest/detect",
        files=[("files", ("journal_2023.xlsx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))],
    ).json()
    buf.seek(0)
    commit = client.post(
        f"/projects/{pid}/ingest/commit",
        files=[("files", ("journal_2023.xlsx", buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))],
        data={"column_mapping": json.dumps(det["suggested_mapping"])},
    )
    assert commit.status_code == 200
    return pid


def test_analysis_module_endpoints(tmp_path, monkeypatch) -> None:
    pid = _ingest_sample(tmp_path, monkeypatch)

    assert client.get(f"/projects/{pid}/analysis/expense/cross-year").status_code == 200
    assert client.get(
        f"/projects/{pid}/analysis/working-capital/ap-accrual/monthly",
        params={"year": 2023},
    ).status_code == 200
    assert client.get(
        f"/projects/{pid}/analysis/balance-sheet/categories",
        params={"year": 2023},
    ).status_code == 200
    other_pnl = client.get(
        f"/projects/{pid}/analysis/other-pnl/monthly",
        params={"year": 2023},
    )
    assert other_pnl.status_code == 200
    assert len(other_pnl.json()["rows"]) == 12
    cost_variance = client.get(
        f"/projects/{pid}/analysis/cost-variance/monthly",
        params={"year": 2023},
    )
    assert cost_variance.status_code == 200
    assert len(cost_variance.json()["rows"]) == 12
    assert "interpretation" in cost_variance.json()["summary"]
    assert client.get(
        f"/projects/{pid}/analysis/adjustment/summary",
        params={"year": 2023},
    ).status_code == 200

    drill = client.post(
        f"/projects/{pid}/analysis/drilldown",
        json={"selector": {"kind": "monthly_income_cost", "year": 2023, "month": 1, "metric": "revenue", "category": "总计"}},
    )
    assert drill.status_code == 200
    assert drill.json()["row_count"] >= 1

    get_store.cache_clear()
    get_pipeline.cache_clear()
