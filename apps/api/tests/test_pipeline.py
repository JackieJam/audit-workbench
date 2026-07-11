from __future__ import annotations

import io
import json

import pandas as pd
from audit_api.deps import get_pipeline, get_store
from audit_api.main import app
from fastapi.testclient import TestClient
from openpyxl import load_workbook

client = TestClient(app)


def _minimal_xlsx() -> io.BytesIO:
    df = pd.DataFrame(
        {
            "凭证编号": ["1001", "1001", "2001", "2001"],
            "过账日期": ["2023-12-28", "2023-12-28", "2024-03-15", "2024-03-15"],
            "凭证货币价值": [500000.0, -500000.0, 1000.0, -1000.0],
            "借/贷标识": ["S", "H", "S", "H"],
            "总账科目": ["6001010000", "1122010000", "6001010000", "6401010000"],
            "总账科目：短文本": ["主营业务收入", "应收账款", "主营业务收入", "主营业务成本"],
            "凭证类型": ["SA", "SA", "AF", "AF"],
            "文本": ["销售", "销售", "成本", "成本"],
        }
    )
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    buf.seek(0)
    buf.name = "test.xlsx"  # type: ignore[attr-defined]
    return buf


def _ingest_two_years(tmp_path, monkeypatch) -> str:
    monkeypatch.setenv("AUDIT_WORKBENCH_DATA_ROOT", str(tmp_path))
    get_store.cache_clear()
    get_pipeline.cache_clear()

    pid = client.post("/projects", json={"name": "pipeline测试"}).json()["project_id"]

    files = []
    for year, month in [(2023, "12-28"), (2024, "03-15")]:
        df = pd.DataFrame(
            {
                "凭证编号": [f"{year}001", f"{year}001"],
                "过账日期": [f"{year}-{month}", f"{year}-{month}"],
                "凭证货币价值": [500000.0, -500000.0],
                "借/贷标识": ["S", "H"],
                "总账科目": ["6001010000", "1122010000"],
                "总账科目：短文本": ["主营业务收入", "应收账款"],
                "凭证类型": ["SA", "SA"],
                "文本": ["销售", "销售"],
            }
        )
        buf = io.BytesIO()
        df.to_excel(buf, index=False, engine="openpyxl")
        buf.seek(0)
        files.append((f"{year}.xlsx", buf.getvalue()))

    det = client.post(
        f"/projects/{pid}/ingest/detect",
        files=[("files", (name, data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")) for name, data in files],
    ).json()

    commit = client.post(
        f"/projects/{pid}/ingest/commit",
        files=[("files", (name, data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")) for name, data in files],
        data={"column_mapping": json.dumps(det["suggested_mapping"])},
    )
    assert commit.status_code == 200
    assert len(commit.json()["years"]) >= 2

    get_store.cache_clear()
    get_pipeline.cache_clear()
    return pid


def test_pipeline_profiles_rules_samples_export(tmp_path, monkeypatch) -> None:
    pid = _ingest_two_years(tmp_path, monkeypatch)

    prof = client.post(f"/projects/{pid}/pipeline/profiles")
    assert prof.status_code == 200
    assert prof.json()["years"]

    rules = client.post(f"/projects/{pid}/pipeline/rules/run")
    assert rules.status_code == 200

    cross = client.post(f"/projects/{pid}/pipeline/cross-year")
    assert cross.status_code == 200

    samples = client.post(
        f"/projects/{pid}/pipeline/samples",
        json={"method": "by_rule", "size": 10},
    )
    assert samples.status_code == 200

    export = client.get(f"/projects/{pid}/pipeline/export")
    assert export.status_code == 200
    assert "filename*=UTF-8''" in export.headers["content-disposition"]
    assert export.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert len(export.content) > 1000

    # 导出必须服从页面当前样本事实源，而不是重新按规则生成另一套样本。
    store = get_store()
    state = store.load_state(pid)
    selected_voucher = "2023001"
    state["samples"] = [{"凭证编号": selected_voucher}]
    store.save_state(pid, state)
    export = client.get(f"/projects/{pid}/pipeline/export")
    workbook = load_workbook(io.BytesIO(export.content), read_only=True)
    sample_sheet = workbook["样本清单"]
    exported_vouchers = {
        str(sample_sheet.cell(row=row, column=2).value)
        for row in range(2, sample_sheet.max_row + 1)
        if sample_sheet.cell(row=row, column=2).value
    }
    assert exported_vouchers == {selected_voucher}

    get_store.cache_clear()
    get_pipeline.cache_clear()
