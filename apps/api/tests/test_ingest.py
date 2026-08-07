from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pandas as pd
from audit_api.deps import get_pipeline, get_store
from audit_api.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


def _minimal_xlsx(amount: float = 1000.0) -> io.BytesIO:
    df = pd.DataFrame(
        {
            "凭证编号": ["1001", "1001"],
            "过账日期": ["2024-03-15", "2024-03-15"],
            "凭证货币价值": [amount, -amount],
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
    assert isinstance(body.get("per_file"), list)
    assert len(body["per_file"]) >= 1
    assert body["per_file"][0]["file_label"]

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

    store = get_store()
    manifest = store.load_manifest(pid)
    journal_source = next(source for source in manifest.sources if source.type == "journal")
    assert len(journal_source.assets) == 1
    asset = journal_source.assets[0]
    source_path = store.project_dir(pid) / asset["stored_path"]
    assert source_path.exists()
    assert hashlib.sha256(source_path.read_bytes()).hexdigest() == asset["sha256"]
    assert Path(asset["original_name"]).name == "test.xlsx"

    sources = client.get(f"/projects/{pid}/sources")
    assert sources.status_code == 200
    assert sources.json()["assets"][0]["asset_id"] == asset["asset_id"]
    download = client.get(f"/projects/{pid}/sources/{asset['asset_id']}/download")
    assert download.status_code == 200
    assert hashlib.sha256(download.content).hexdigest() == asset["sha256"]
    assert download.headers["x-source-sha256"] == asset["sha256"]

    page = client.get(f"/projects/{pid}/journal/2024?limit=10")
    assert page.status_code == 200
    assert page.json()["total"] == 2

    get_store.cache_clear()
    get_pipeline.cache_clear()


def test_reingest_keeps_append_only_source_and_ingest_history(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AUDIT_WORKBENCH_DATA_ROOT", str(tmp_path))
    get_store.cache_clear()
    get_pipeline.cache_clear()
    pid = client.post("/projects", json={"name": "来源历史"}).json()["project_id"]

    first_bytes = _minimal_xlsx(1000.0).getvalue()
    detected = client.post(
        f"/projects/{pid}/ingest/detect",
        files=[("files", ("first.xlsx", first_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))],
    ).json()
    first_commit = client.post(
        f"/projects/{pid}/ingest/commit",
        files=[("files", ("first.xlsx", first_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))],
        data={"column_mapping": __import__("json").dumps(detected["suggested_mapping"])},
    )
    assert first_commit.status_code == 200
    first_manifest = get_store().load_manifest(pid)
    first_asset = first_manifest.sources[0].assets[0]
    first_run = first_manifest.current_ingest_run_id

    second_bytes = _minimal_xlsx(2500.0).getvalue()
    second_commit = client.post(
        f"/projects/{pid}/ingest/commit",
        files=[("files", ("second.xlsx", second_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))],
        data={"column_mapping": __import__("json").dumps(detected["suggested_mapping"])},
    )
    assert second_commit.status_code == 200

    manifest = get_store().load_manifest(pid)
    assert len(manifest.ingest_runs) == 2
    assert manifest.current_ingest_run_id != first_run
    assert manifest.ingest_runs[0]["ingest_run_id"] == first_run
    assert manifest.ingest_runs[-1]["data_version"] == get_store().current_data_version(pid)
    assets = {
        asset["asset_id"]: asset
        for source in manifest.sources
        for asset in source.assets
    }
    assert len(assets) == 2
    assert assets[first_asset["asset_id"]]["active"] is False
    assert sum(bool(asset["active"]) for asset in assets.values()) == 1

    old_download = client.get(
        f"/projects/{pid}/sources/{first_asset['asset_id']}/download"
    )
    assert old_download.status_code == 200
    assert old_download.content == first_bytes

    old_path = get_store().project_dir(pid) / first_asset["stored_path"]
    old_path.write_bytes(b"tampered")
    rejected = client.get(
        f"/projects/{pid}/sources/{first_asset['asset_id']}/download"
    )
    assert rejected.status_code == 409
    assert "完整性" in rejected.json()["detail"]

    get_store.cache_clear()
    get_pipeline.cache_clear()


def test_rejects_legacy_xls_with_actionable_message(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AUDIT_WORKBENCH_DATA_ROOT", str(tmp_path))
    get_store.cache_clear()
    pid = client.post("/projects", json={"name": "旧格式"}).json()["project_id"]
    response = client.post(
        f"/projects/{pid}/ingest/detect",
        files=[("files", ("legacy.xls", b"not-an-xlsx", "application/vnd.ms-excel"))],
    )
    assert response.status_code == 400
    assert "另存为 .xlsx" in response.text
    get_store.cache_clear()
