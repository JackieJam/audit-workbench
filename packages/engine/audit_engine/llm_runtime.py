"""Resolve effective LLM runtime (key + model + base_url) from profile."""

from __future__ import annotations

import os
from dataclasses import dataclass

from audit_engine.llm_config import DEFAULT_LLM_CONFIG, normalize, resolve_key, resolve_profile_key
from audit_engine.llm_profiles import get_default_profile, get_profile


@dataclass
class LlmRuntime:
    profile_id: str
    profile_name: str
    model: str
    base_url: str
    api_key: str
    key_source: str


def resolve_llm_runtime(*, profile_id: str | None = None, manual_key: str | None = None) -> LlmRuntime:
    profile = None
    if profile_id:
        profile = get_profile(profile_id)
    if profile is None:
        profile = get_default_profile()

    if profile:
        norm = normalize(profile)
        api_key, key_source = resolve_profile_key(profile, manual=manual_key or "")
        return LlmRuntime(
            profile_id=norm["profile_id"],
            profile_name=norm["profile_name"],
            model=norm["model"] or os.environ.get("LLM_MODEL", DEFAULT_LLM_CONFIG["model"]),
            base_url=norm["base_url"] or os.environ.get("LLM_BASE_URL", DEFAULT_LLM_CONFIG["base_url"]),
            api_key=api_key,
            key_source=key_source,
        )

    api_key, key_source = resolve_key("default", manual=manual_key or "")
    return LlmRuntime(
        profile_id="",
        profile_name="环境变量",
        model=os.environ.get("LLM_MODEL", DEFAULT_LLM_CONFIG["model"]),
        base_url=os.environ.get("LLM_BASE_URL", DEFAULT_LLM_CONFIG["base_url"]),
        api_key=api_key,
        key_source=key_source,
    )
