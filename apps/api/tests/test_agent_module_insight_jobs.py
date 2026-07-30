from __future__ import annotations

from types import SimpleNamespace

from audit_api.deps import get_pipeline, get_store
from audit_api.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


def _runtime():
    return SimpleNamespace(
        api_key="test-key",
        model="test-model",
        base_url="https://example.invalid/v1",
    )


def _fake_job(module_key: str) -> dict:
    return {
        "job_id": "ins_api_test",
        "module_key": module_key,
        "stage": "queued",
        "stage_label": "排队等待",
        "percent": 0,
        "status": "queued",
        "reused": False,
    }


def test_explicit_agent_analysis_returns_real_job_event(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_WORKBENCH_DATA_ROOT", str(tmp_path))
    get_store.cache_clear()
    get_pipeline.cache_clear()
    pid = client.post("/projects", json={"name": "agent-job"}).json()["project_id"]
    monkeypatch.setattr("audit_api.routers.agent.resolve_llm_runtime", lambda **_: _runtime())
    monkeypatch.setattr(
        "audit_api.routers.agent.enqueue_module_insight",
        lambda _background, _store, _pid, module_key, **_: _fake_job(module_key),
    )

    response = client.post(
        f"/projects/{pid}/agent/chat",
        json={"message": "请对收入成本模块做 AI 风险分析"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["tool_calls"][0]["tool"] == "run_module_insight"
    assert payload["tool_calls"][0]["result"]["job_id"] == "ins_api_test"
    assert "真实任务编号" in payload["reply"]
    stored = client.get(f"/projects/{pid}/agent/state").json()
    assert stored["messages"][-1]["tool_calls"][0]["result"]["status"] == "queued"
    get_store.cache_clear()
    get_pipeline.cache_clear()


def test_module_button_endpoint_returns_job(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_WORKBENCH_DATA_ROOT", str(tmp_path))
    get_store.cache_clear()
    get_pipeline.cache_clear()
    pid = client.post("/projects", json={"name": "module-job"}).json()["project_id"]
    monkeypatch.setattr("audit_api.routers.llm_insight.resolve_llm_runtime", lambda **_: _runtime())
    monkeypatch.setattr(
        "audit_api.routers.llm_insight.enqueue_module_insight",
        lambda _background, _store, _pid, module_key, **_: _fake_job(module_key),
    )

    response = client.post(
        f"/projects/{pid}/analysis/modules/收入成本/insight/jobs",
        json={"profile_id": "", "api_key": ""},
    )

    assert response.status_code == 200
    assert response.json()["job"]["job_id"] == "ins_api_test"
    get_store.cache_clear()
    get_pipeline.cache_clear()
