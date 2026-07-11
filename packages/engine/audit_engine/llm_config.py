"""LLM profile fields and API key resolution."""

from __future__ import annotations

import os

from audit_engine import secret_store

DEFAULT_LLM_CONFIG: dict[str, str] = {
    "profile_id": "",
    "profile_name": "默认",
    "model": "deepseek-chat",
    "base_url": "https://api.deepseek.com",
    "keychain_account": "default",
}

ENV_KEY_VARS: tuple[str, ...] = ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "LLM_API_KEY")
PROFILE_FIELDS: tuple[str, ...] = (
    "profile_id",
    "profile_name",
    "model",
    "base_url",
    "keychain_account",
)

PROVIDER_PRESETS: list[dict[str, str]] = [
    {"id": "deepseek", "label": "DeepSeek", "base_url": "https://api.deepseek.com", "default_model": "deepseek-chat"},
    {"id": "openai", "label": "OpenAI", "base_url": "https://api.openai.com/v1", "default_model": "gpt-4o-mini"},
    {"id": "custom", "label": "自定义", "base_url": "", "default_model": ""},
]

STATIC_MODEL_HINTS: dict[str, list[str]] = {
    "https://api.deepseek.com": ["deepseek-chat", "deepseek-reasoner"],
    "https://api.openai.com/v1": ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini", "o3-mini"],
}


def _clean(value: object) -> str:
    return str(value).strip() if value is not None else ""


def normalize(raw: dict | None = None) -> dict[str, str]:
    src = raw or {}
    return {
        "profile_id": _clean(src.get("profile_id")),
        "profile_name": _clean(src.get("profile_name")) or DEFAULT_LLM_CONFIG["profile_name"],
        "model": _clean(src.get("model")) or DEFAULT_LLM_CONFIG["model"],
        "base_url": _clean(src.get("base_url")) or DEFAULT_LLM_CONFIG["base_url"],
        "keychain_account": _clean(src.get("keychain_account")) or DEFAULT_LLM_CONFIG["keychain_account"],
    }


def key_account(cfg: dict | None) -> str:
    norm = normalize(cfg)
    return norm["keychain_account"] or norm["profile_id"] or "default"


def env_api_key() -> tuple[str, str]:
    for name in ENV_KEY_VARS:
        value = os.environ.get(name, "").strip()
        if value:
            return value, f"环境变量 {name}"
    return "", ""


def resolve_key(keychain_account: str, *, manual: str = "") -> tuple[str, str]:
    """Global fallback order: env > manual > keychain."""
    key, source = env_api_key()
    if key:
        return key, source
    manual_key = (manual or "").strip()
    if manual_key:
        return manual_key, "本次会话输入"
    account = (keychain_account or "").strip() or "default"
    stored = secret_store.get_secret(account)
    if stored:
        return stored, f"本机存储：{account}"
    return "", "未配置"


def resolve_profile_key(profile: dict | None, *, manual: str = "") -> tuple[str, str]:
    """Per-profile order: manual > keychain > env — avoids stale env vars overriding saved keys."""
    manual_key = (manual or "").strip()
    if manual_key:
        return manual_key, "本次会话输入"
    if profile:
        account = key_account(profile)
        stored = secret_store.get_secret(account)
        if stored:
            return stored, f"本机存储：{account}"
    key, source = env_api_key()
    if key:
        return key, source
    return "", "未配置"


def remember_key(keychain_account: str, key: str) -> bool:
    account = (keychain_account or "").strip() or "default"
    return secret_store.set_secret(account, (key or "").strip())


def forget_key(keychain_account: str) -> bool:
    account = (keychain_account or "").strip() or "default"
    return secret_store.delete_secret(account)


def has_remembered_key(keychain_account: str) -> bool:
    account = (keychain_account or "").strip() or "default"
    return secret_store.has_secret(account)
