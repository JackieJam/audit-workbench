from audit_engine.llm_profiles import delete_profile, list_profiles, save_profile


def test_save_list_delete_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_WORKBENCH_DATA_ROOT", str(tmp_path))
    saved = save_profile(
        {"profile_name": "测试方案", "base_url": "https://api.deepseek.com", "model": "deepseek-chat"},
        set_default=True,
    )
    assert saved["profile_id"]
    assert saved["is_default"]
    profiles = list_profiles()
    assert len(profiles) == 1
    assert profiles[0]["profile_name"] == "测试方案"
    assert delete_profile(saved["profile_id"])
    assert list_profiles() == []
