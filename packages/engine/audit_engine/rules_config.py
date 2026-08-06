"""规则配置加载 — default_rules.json 基线。"""

from __future__ import annotations

import json
from functools import lru_cache

from audit_engine.paths import config_dir


@lru_cache(maxsize=1)
def default_rules_config() -> dict:
    path = config_dir() / "default_rules.json"
    if not path.exists():
        return {"max_sample_size": 50}
    return json.loads(path.read_text(encoding="utf-8"))


def merge_rules_config(base: dict | None, overrides: dict | None) -> dict:
    """浅合并规则配置（项目级覆盖 default）。"""
    merged = dict(default_rules_config())
    if base:
        merged.update(base)
    if overrides:
        for key, value in overrides.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = {**merged[key], **value}
            else:
                merged[key] = value
    return merged
