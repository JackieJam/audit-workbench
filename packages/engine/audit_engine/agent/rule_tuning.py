"""基于反馈与命中统计的规则调参建议。"""

from __future__ import annotations

import uuid
from typing import Any

from audit_engine.agent.rule_memory import feedback_summary, list_rule_feedback
from audit_engine.agent.rule_ops import merge_rules_from_state, patch_rule
from audit_engine.store import ProjectStore

RULE_ID_TO_NAME: dict[str, str] = {
    "splitting": "化整为零",
    "large_amount": "大额异常",
    "manual_entry": "手工凭证",
    "accrual_anomaly": "计提异常",
    "yearend_surge": "收入突增",
    "financing_trade": "融资性贸易",
    "cash_pool": "资金池划转",
    "user_concentration": "用户集中度异常",
    "reversal_pattern": "冲销反记账异常",
    "sensitive_fees": "敏感费用筛查",
}

NAME_TO_RULE_ID: dict[str, str] = {v: k for k, v in RULE_ID_TO_NAME.items()}

# 可自动调节的数值型参数（乘性调整）
TUNABLE_NUMERIC: dict[str, list[str]] = {
    "large_amount": ["round_number_threshold", "repeat_threshold", "holiday_min_amount"],
    "splitting": ["max_single_amount", "min_total", "min_txn_count"],
    "manual_entry": ["pnl_amount_threshold"],
    "accrual_anomaly": ["min_amount"],
    "yearend_surge": ["multiplier"],
    "financing_trade": ["min_revenue_amount", "min_match_score"],
    "cash_pool": ["large_threshold"],
    "user_concentration": ["concentration_threshold"],
    "reversal_pattern": ["frequent_count", "large_threshold"],
    "sensitive_fees": ["baseline_multiplier"],
    "cross_year_revenue": ["dec_multiplier"],
    "cross_year_accrual": ["coverage_threshold"],
}

FALSE_POSITIVE_WORDS = ("误报", "太多", "过多", "噪音", "噪声", "太宽", "放宽")
MISS_WORDS = ("漏报", "漏掉", "遗漏", "太严", "收紧", "漏检")


def _hit_map(state: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for block in state.get("rule_results") or []:
        name = str(block.get("rule_name") or "")
        count = int(block.get("count") or len(block.get("hits") or []))
        rid = NAME_TO_RULE_ID.get(name, name)
        out[rid] = out.get(rid, 0) + count
    return out


def _scale_num(value: Any, factor: float, *, as_int: bool = True) -> Any:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return value
    scaled = num * factor
    if as_int and abs(num - round(num)) < 1e-9:
        return max(1, int(round(scaled)))
    return round(scaled, 4)


def _note_signals(items: list[dict[str, Any]]) -> tuple[bool, bool]:
    text = " ".join(str(i.get("note") or "") + str(i.get("context") or "") for i in items)
    false_pos = any(w in text for w in FALSE_POSITIVE_WORDS)
    miss = any(w in text for w in MISS_WORDS)
    return false_pos, miss


def suggest_rule_tuning(
    store: ProjectStore,
    project_id: str,
    *,
    rule_id: str = "",
) -> dict[str, Any]:
    state = store.load_state(project_id)
    cfg = merge_rules_from_state(state)
    hits = _hit_map(state)
    summaries = {s["rule_id"]: s for s in feedback_summary(store, project_id)}
    target_ids = [rule_id] if rule_id else sorted(set(list(summaries.keys()) + list(hits.keys())))

    suggestions: list[dict[str, Any]] = []
    for rid in target_ids:
        if rid not in TUNABLE_NUMERIC and rid not in cfg:
            continue
        fb = summaries.get(rid) or {}
        avg = float(fb.get("avg_score") or 0)
        count_fb = int(fb.get("count") or 0)
        hit_count = int(hits.get(rid) or 0)
        notes = list_rule_feedback(store, project_id, rule_id=rid)[:5]
        false_pos, miss = _note_signals(notes)

        rule_cfg = dict(cfg.get(rid) or {})
        if not rule_cfg:
            continue

        direction = ""
        factor = 1.0
        reason_parts: list[str] = []

        if count_fb and avg <= 2.5:
            reason_parts.append(f"反馈均分 {avg} 偏低")
        if false_pos or (count_fb and avg <= 2.5 and hit_count >= 30):
            direction = "raise"
            factor = 1.25 if hit_count >= 100 else 1.15
            reason_parts.append("疑似误报偏多，建议提高阈值")
        elif miss or (count_fb and avg >= 4 and hit_count <= 3):
            direction = "lower"
            factor = 0.85
            reason_parts.append("疑似漏报或命中过少，建议降低阈值")
        elif count_fb and avg <= 2 and hit_count >= 50:
            direction = "raise"
            factor = 1.2
            reason_parts.append("低分且命中量大")
        else:
            continue

        patches: dict[str, Any] = {}
        for key in TUNABLE_NUMERIC.get(rid, []):
            if key not in rule_cfg:
                continue
            val = rule_cfg[key]
            if isinstance(val, (int, float)):
                if direction == "raise":
                    patches[key] = _scale_num(val, factor)
                else:
                    patches[key] = _scale_num(val, factor)

        if not patches:
            continue

        old_rationale = str(rule_cfg.get("rationale") or "")
        note = "；".join(reason_parts)
        patches["rationale"] = (
            f"{old_rationale}（Agent调参建议：{note}，{direction}）"
            if old_rationale
            else f"Agent调参建议：{note}"
        )

        suggestions.append({
            "suggestion_id": f"rt_{uuid.uuid4().hex[:10]}",
            "rule_id": rid,
            "rule_name": RULE_ID_TO_NAME.get(rid, rid),
            "direction": direction,
            "patches": patches,
            "reason": note,
            "avg_score": avg or None,
            "hit_count": hit_count,
            "feedback_count": count_fb,
            "confidence": "high" if (false_pos or miss) else "medium",
        })

    state = store.load_state(project_id)
    state["rule_tuning_suggestions"] = suggestions
    store.save_state(project_id, state)
    return {"count": len(suggestions), "suggestions": suggestions}


def apply_rule_tuning_suggestion(
    store: ProjectStore,
    project_id: str,
    *,
    suggestion_id: str = "",
    rule_id: str = "",
    apply_all: bool = False,
) -> dict[str, Any]:
    state = store.load_state(project_id)
    cached = list(state.get("rule_tuning_suggestions") or [])
    if not cached:
        return {"error": "无缓存建议，请先调用 suggest_rule_tuning"}

    selected: list[dict[str, Any]] = []
    if apply_all:
        selected = [s for s in cached if s.get("confidence") == "high"] or cached
    elif suggestion_id:
        selected = [s for s in cached if s.get("suggestion_id") == suggestion_id]
    elif rule_id:
        selected = [s for s in cached if s.get("rule_id") == rule_id][:1]
    else:
        return {"error": "请提供 suggestion_id、rule_id 或 apply_all=true"}

    if not selected:
        return {"error": "未找到匹配的建议"}

    applied = []
    for sug in selected:
        rid = str(sug.get("rule_id") or "")
        patches = dict(sug.get("patches") or {})
        if not rid or not patches:
            continue
        updated = patch_rule(store, project_id, rid, patches)
        applied.append({"rule_id": rid, "rule": updated, "suggestion_id": sug.get("suggestion_id")})

    return {"ok": True, "applied": applied, "count": len(applied)}
