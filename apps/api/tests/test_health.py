from audit_api.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


def test_health() -> None:
    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert "storage_root" in body


def test_foreign_stream_probe_returns_404() -> None:
    res = client.get("/stream?topics=roundtable:test")
    assert res.status_code == 404
    assert res.json()["detail"] == "audit-workbench-api"


def test_create_and_list_projects(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AUDIT_WORKBENCH_DATA_ROOT", str(tmp_path))
    from audit_api.deps import get_store

    get_store.cache_clear()

    res = client.post("/projects", json={"name": "测试项目"})
    assert res.status_code == 200
    pid = res.json()["project_id"]

    res2 = client.get("/projects")
    assert res2.status_code == 200
    ids = [p["project_id"] for p in res2.json()]
    assert pid in ids

    get_store.cache_clear()
