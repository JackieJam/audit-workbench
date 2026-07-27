"""规则配置读写 — Agent 工具共享。"""

from __future__ import annotations

from typing import Any

from audit_engine.rules_config import merge_rules_config
from audit_engine.store import ProjectStore


def merge_rules_from_state(state: dict[str, Any]) -> dict[str, Any]:
    return merge_rules_config(None, state.get("rules_config"))


def patch_rule(store: ProjectStore, project_id: str, rule_id: str, patches: dict[str, Any]) -> dict[str, Any]:
    from audit_engine.pipeline import _cross_year_config_changed, _invalidate_rule_derived_state

    rid = str(rule_id or "").strip()
    if not rid:
        raise ValueError("rule_id 不能为空")
    state = store.load_state(project_id)
    previous = merge_rules_from_state(state)
    overrides = dict(state.get("rules_config") or {})
    current = overrides.get(rid)
    if isinstance(current, dict) and isinstance(patches, dict):
        merged = {**current, **patches}
    elif isinstance(patches, dict):
        merged = dict(patches)
    else:
        merged = patches
    overrides[rid] = merged
    state["rules_config"] = overrides
    after = merge_rules_config(None, overrides)
    _invalidate_rule_derived_state(
        state,
        clear_cross_year=_cross_year_config_changed(previous, after),
    )
    store.save_state(project_id, state)
    return after.get(rid, merged)
