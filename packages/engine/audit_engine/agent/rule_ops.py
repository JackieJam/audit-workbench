"""规则配置读写 — Agent 工具共享。"""

from __future__ import annotations

from typing import Any

from audit_engine.rules_config import merge_rules_config
from audit_engine.store import ProjectStore


def merge_rules_from_state(state: dict[str, Any]) -> dict[str, Any]:
    return merge_rules_config(None, state.get("rules_config"))


def patch_rule(store: ProjectStore, project_id: str, rule_id: str, patches: dict[str, Any]) -> dict[str, Any]:
    from audit_engine.pipeline import _cross_year_config_changed, _invalidate_rule_derived_state
    from audit_engine.sampling_plan import rule_revision

    rid = str(rule_id or "").strip()
    if not rid:
        raise ValueError("rule_id 不能为空")
    result: dict[str, Any] = {}

    def update(state: dict[str, Any]) -> None:
        from audit_engine.audit_case import mark_case_evidence_stale_in_state

        previous = merge_rules_from_state(state)
        overrides = dict(state.get("rules_config") or {})
        current = overrides.get(rid)
        if isinstance(current, dict) and isinstance(patches, dict):
            merged: Any = {**current, **patches}
        elif isinstance(patches, dict):
            merged = dict(patches)
        else:
            merged = patches
        overrides[rid] = merged
        after = merge_rules_config(None, overrides)
        state["rules_config"] = overrides
        _invalidate_rule_derived_state(
            state,
            clear_cross_year=_cross_year_config_changed(previous, after),
        )
        if rule_revision(previous) != rule_revision(after):
            mark_case_evidence_stale_in_state(
                state,
                reason="抽样规则配置已变更；原规则命中证据仍保留，但需按新规则重新执行。",
                trigger="rules_changed",
                source_types={"rule_hit"},
            )
        result["rule"] = after.get(rid, merged)

    store.update_state(project_id, update)
    return result["rule"]
