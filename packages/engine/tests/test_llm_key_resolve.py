from audit_engine.llm_config import remember_key, resolve_profile_key
from audit_engine.llm_profiles import save_profile
from audit_engine.llm_runtime import resolve_llm_runtime


def test_profile_keychain_beats_env(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIT_WORKBENCH_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("AUDIT_WORKBENCH_SECRET_BACKEND", "file")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "env-wrong")

    profile = save_profile(
        {"profile_name": "测试", "base_url": "https://api.deepseek.com", "model": "deepseek-chat"},
        set_default=True,
    )
    assert remember_key(profile["profile_id"], "sk-saved-profile-key")

    api_key, source = resolve_profile_key(profile)
    assert api_key == "sk-saved-profile-key"
    assert "本机存储" in source

    runtime = resolve_llm_runtime(profile_id=profile["profile_id"])
    assert runtime.api_key == "sk-saved-profile-key"
