"""Persist LLM profiles to ~/.audit_tool/llm_profiles.json (no API keys)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from audit_engine.llm_config import DEFAULT_LLM_CONFIG
from audit_engine.locking import file_lock
from audit_engine.runtime import storage_root


def _profiles_path() -> Path:
    return storage_root() / "llm_profiles.json"


def _profiles_lock_path() -> Path:
    return storage_root() / ".llm_profiles.lock"


def _profile_id(profile_name: str) -> str:
    # ID 仅 ASCII，避免前端 fetch header 编码限制；显示名仍用 profile_name
    digest = hashlib.sha1(profile_name.strip().encode("utf-8")).hexdigest()[:12]
    return f"llm_{digest}"


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _load_profiles() -> list[dict[str, Any]]:
    data = _read_json(_profiles_path(), [])
    if not isinstance(data, list):
        return []
    return [dict(item) for item in data if isinstance(item, dict) and item.get("profile_name")]


def _save_profiles(profiles: list[dict[str, Any]]) -> None:
    path = _profiles_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    safe: list[dict[str, Any]] = []
    for profile in profiles:
        pid = str(profile.get("profile_id", "")).strip()
        safe.append({
            "profile_id": pid,
            "profile_name": str(profile.get("profile_name", "")).strip(),
            "base_url": str(profile.get("base_url", "")).strip() or DEFAULT_LLM_CONFIG["base_url"],
            "model": str(profile.get("model", "")).strip() or DEFAULT_LLM_CONFIG["model"],
            "keychain_account": str(profile.get("keychain_account", "")).strip() or pid or "default",
            "is_default": bool(profile.get("is_default", False)),
            "updated_at": str(profile.get("updated_at", "")),
        })
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    try:
        path.chmod(0o600)
    except Exception:
        pass


def list_profiles() -> list[dict[str, Any]]:
    profiles = _load_profiles()
    return sorted(profiles, key=lambda p: (bool(p.get("is_default")), p.get("updated_at", "")), reverse=True)


def get_profile(profile_id: str) -> dict[str, Any] | None:
    clean_id = str(profile_id or "").strip()
    if not clean_id:
        return None
    for profile in _load_profiles():
        if profile.get("profile_id") == clean_id:
            return dict(profile)
    return None


def get_default_profile() -> dict[str, Any] | None:
    profiles = list_profiles()
    if not profiles:
        return None
    for profile in profiles:
        if profile.get("is_default"):
            return dict(profile)
    return dict(profiles[0])


def save_profile(profile: dict[str, Any], *, set_default: bool = False) -> dict[str, Any]:
    from audit_engine.llm_endpoint_policy import assert_llm_endpoint_allowed

    profile_name = str(profile.get("profile_name", "")).strip()
    if not profile_name:
        raise ValueError("方案名称不能为空")
    base_url = str(profile.get("base_url", "")).strip() or DEFAULT_LLM_CONFIG["base_url"]
    assert_llm_endpoint_allowed(base_url)

    with file_lock(_profiles_lock_path(), exclusive=True):
        profiles = _load_profiles()
        profile_id = str(profile.get("profile_id", "")).strip()
        if not profile_id:
            existing = next((p for p in profiles if p.get("profile_name") == profile_name), None)
            profile_id = existing.get("profile_id") if existing else _profile_id(profile_name)

        saved = {
            "profile_id": profile_id,
            "profile_name": profile_name,
            "base_url": str(profile.get("base_url", "")).strip() or DEFAULT_LLM_CONFIG["base_url"],
            "model": str(profile.get("model", "")).strip() or DEFAULT_LLM_CONFIG["model"],
            "keychain_account": str(profile.get("keychain_account", "")).strip() or profile_id,
            "is_default": bool(set_default or profile.get("is_default", False)),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }

        profiles = [p for p in profiles if p.get("profile_id") != profile_id]
        if saved["is_default"] or not profiles:
            for item in profiles:
                item["is_default"] = False
            saved["is_default"] = True
        profiles.append(saved)
        _save_profiles(profiles)
        return dict(saved)


def delete_profile(profile_id: str) -> bool:
    clean_id = str(profile_id or "").strip()
    if not clean_id:
        return False
    with file_lock(_profiles_lock_path(), exclusive=True):
        profiles = _load_profiles()
        kept = [p for p in profiles if p.get("profile_id") != clean_id]
        if len(kept) == len(profiles):
            return False
        if kept and not any(p.get("is_default") for p in kept):
            kept[0]["is_default"] = True
        _save_profiles(kept)
        return True


def set_default_profile(profile_id: str) -> dict[str, Any] | None:
    profile = get_profile(profile_id)
    if not profile:
        return None
    return save_profile(profile, set_default=True)
