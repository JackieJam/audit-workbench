"""LLM endpoint 白名单 API。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def _client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.delenv("AUDIT_WORKBENCH_LLM_ENDPOINT_ALLOWLIST", raising=False)
    monkeypatch.setenv("AUDIT_WORKBENCH_DATA_ROOT", str(tmp_path / "data"))
    from audit_api.main import app

    return TestClient(app)


def test_endpoint_allowlist_get_put_roundtrip(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    initial = client.get("/llm/endpoint-allowlist")
    assert initial.status_code == 200
    body = initial.json()
    assert body["enabled"] is False
    assert "seed_hosts" in body

    saved = client.put(
        "/llm/endpoint-allowlist",
        json={"enabled": True, "hosts": ["api.deepseek.com", "127.0.0.1"], "allow_all": False},
    )
    assert saved.status_code == 200
    assert saved.json()["enabled"] is True
    assert "api.deepseek.com" in saved.json()["hosts"]

    blocked = client.post(
        "/llm/profiles",
        json={
            "profile_name": "evil",
            "base_url": "https://evil.example.com",
            "model": "x",
        },
    )
    assert blocked.status_code == 400
    assert "不在允许列表" in blocked.json()["detail"]

    ok = client.post(
        "/llm/profiles",
        json={
            "profile_name": "deepseek",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-chat",
        },
    )
    assert ok.status_code == 200


def test_endpoint_allowlist_env_overrides_put(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AUDIT_WORKBENCH_LLM_ENDPOINT_ALLOWLIST", "api.deepseek.com")
    monkeypatch.setenv("AUDIT_WORKBENCH_DATA_ROOT", str(tmp_path / "data"))
    from audit_api.main import app

    client = TestClient(app)
    res = client.put(
        "/llm/endpoint-allowlist",
        json={"enabled": True, "hosts": ["localhost"], "allow_all": False},
    )
    assert res.status_code == 400
    assert "环境变量" in res.json()["detail"]
