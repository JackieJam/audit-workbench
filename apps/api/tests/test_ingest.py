from __future__ import annotations

import io

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from audit_api.deps import get_pipeline, get_store
from audit_api.main import app

client = TestClient(app)


def _minimal_xlsx() -> io.BytesIO:
    df = pd.DataFrame(
        {
            "凭证编号": ["1001", "1001"],
            "过账日期": ["2024-03-15", "2024-03-15"],
            "凭证货币价值": [1000.0, -1000.0],
            "借/贷标识": ["S", "H"],
            "总账科目": ["600101", "112201"],
        }
    )
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    buf.seek(0)
    buf.name = "test.xlsx"  # type: ignore[attr-defined]
    return buf


def test_detect_and_commit_ingest(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AUDIT_WORKBENCH_DATA_ROOT", str(tmp_path))
    get_store.cache_clear()
    get_pipeline.cache_clear()

    create = client.post("/projects", json={"name": "导入测试"})
    assert create.status_code == 200
    pid = create.json()["project_id"]

    xlsx = _minimal_xlsx()
    det = client.post(
        f"/projects/{pid}/ingest/detect",
        files=[("files", ("test.xlsx", xlsx.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))],
    )
    assert det.status_code == 200
    body = det.json()
    assert "凭证编号" in body["suggested_mapping"]
    assert body["source_columns"]

    xlsx2 = _minimal_xlsx()
    commit = client.post(
        f"/projects/{pid}/ingest/commit",
        files=[("files", ("test.xlsx", xlsx2.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))],
        data={"column_mapping": __import__("json").dumps(body["suggested_mapping"])},
    )
    assert commit.status_code == 200
    result = commit.json()
    assert result["total_rows"] == 2
    assert 2024 in result["years"]

    page = client.get(f"/projects/{pid}/journal/2024?limit=10")
    assert page.status_code == 200
    assert page.json()["total"] == 2

    get_store.cache_clear()
    get_pipeline.cache_clear()
