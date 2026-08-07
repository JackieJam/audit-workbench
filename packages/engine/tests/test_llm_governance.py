"""LLM 数据边界 / 脱敏 / endpoint 白名单。"""

from __future__ import annotations

import pytest
from audit_engine.llm_endpoint_policy import (
    assert_llm_endpoint_allowed,
    load_endpoint_allowlist,
    save_endpoint_allowlist,
)
from audit_engine.llm_verifier import (
    _build_judgments_from_response,
    _pseudo_token,
    default_redaction_mode,
    describe_llm_verify_boundary,
    redact_llm_row,
)
from audit_engine.runtime import assert_storage_isolation, require_storage_namespace


def test_pseudonym_redaction_hides_parties(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUDIT_WORKBENCH_LLM_REDACTION", raising=False)
    assert default_redaction_mode() == "pseudonym"
    row = {
        "供应商": "甲公司",
        "客户": "乙客户",
        "用户名": "zhangsan",
        "金额": 1000,
        "文本": "咨询费",
    }
    out = redact_llm_row(row, "pseudonym")
    assert out["供应商"].startswith("供应商_")
    assert out["客户"].startswith("客户_")
    assert out["用户名"].startswith("用户名_")
    assert out["金额"] == 1000
    assert out["文本"] == "咨询费"
    assert out["供应商"] != "甲公司"


def test_endpoint_allowlist_blocks_unknown_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUDIT_WORKBENCH_LLM_ENDPOINT_ALLOWLIST", "api.deepseek.com,localhost")
    assert_llm_endpoint_allowed("https://api.deepseek.com/v1")
    with pytest.raises(ValueError, match="不在允许列表"):
        assert_llm_endpoint_allowed("https://evil.example.com/v1")


def test_endpoint_allowlist_file_roundtrip(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    monkeypatch.delenv("AUDIT_WORKBENCH_LLM_ENDPOINT_ALLOWLIST", raising=False)
    monkeypatch.setenv("AUDIT_WORKBENCH_DATA_ROOT", str(tmp_path / "data"))
    policy = save_endpoint_allowlist(
        enabled=True,
        hosts=["https://api.deepseek.com/v1", "localhost"],
        allow_all=False,
    )
    assert policy["enabled"] is True
    assert policy["source"] == "file"
    assert "api.deepseek.com" in policy["hosts"]
    assert "localhost" in policy["hosts"]
    assert_llm_endpoint_allowed("https://api.deepseek.com")
    with pytest.raises(ValueError, match="不在允许列表"):
        assert_llm_endpoint_allowed("https://evil.example.com")


def test_endpoint_allowlist_env_blocks_file_save(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    monkeypatch.setenv("AUDIT_WORKBENCH_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("AUDIT_WORKBENCH_LLM_ENDPOINT_ALLOWLIST", "api.deepseek.com")
    loaded = load_endpoint_allowlist()
    assert loaded["source"] == "env"
    assert loaded["env_overrides_file"] is True
    with pytest.raises(ValueError, match="环境变量"):
        save_endpoint_allowlist(enabled=True, hosts=["localhost"])


def test_boundary_describes_redaction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUDIT_WORKBENCH_LLM_ENDPOINT_ALLOWLIST", raising=False)
    boundary = describe_llm_verify_boundary(
        "https://api.deepseek.com", "deepseek-chat", redaction="pseudonym",
    )
    assert boundary["redaction"] == "pseudonym"
    assert "伪名化" in boundary["warning"]
    assert "供应商" in boundary["fields"]
    assert "allowlist_enabled" in boundary


def test_display_to_raw_remaps_pseudonym_voucher_ids() -> None:
    display = _pseudo_token("凭证编号", "V-REAL")
    text = f'[{{"voucher_id": "{display}", "confirmed": true, "risk_level": "高", "reason": "r", "audit_procedures": "a"}}]'
    judgments = _build_judgments_from_response(
        text,
        allowed_voucher_ids={"V-REAL"},
        display_to_raw={display: "V-REAL"},
    )
    assert len(judgments) == 1
    assert judgments[0].voucher_id == "V-REAL"


def test_require_namespace_blocks_shared_root(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("AUDIT_WORKBENCH_REQUIRE_NAMESPACE", "1")
    monkeypatch.delenv("AUDIT_WORKBENCH_NAMESPACE", raising=False)
    monkeypatch.delenv("AUDIT_WORKBENCH_USER_ID", raising=False)
    monkeypatch.delenv("AUDIT_WORKBENCH_DATA_ROOT", raising=False)
    assert require_storage_namespace() is True
    with pytest.raises(RuntimeError, match="REQUIRE_NAMESPACE"):
        assert_storage_isolation()

    monkeypatch.setenv("AUDIT_WORKBENCH_NAMESPACE", "auditor_a")
    monkeypatch.setenv("AUDIT_WORKBENCH_DATA_ROOT", str(tmp_path / "data"))
    assert_storage_isolation()  # 不应抛
