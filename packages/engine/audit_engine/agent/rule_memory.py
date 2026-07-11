"""规则迭代记忆 — 项目级打分与备注，供 Agent 与人工复盘。"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from audit_engine.store import ProjectStore

VALID_SCORES = {1, 2, 3, 4, 5}


def list_rule_feedback(store: ProjectStore, project_id: str, *, rule_id: str = "") -> list[dict[str, Any]]:
    items = list(store.load_state(project_id).get("rule_feedback") or [])
    if rule_id:
        items = [x for x in items if x.get("rule_id") == rule_id]
    return sorted(items, key=lambda x: x.get("at", ""), reverse=True)


def record_rule_feedback(
    store: ProjectStore,
    project_id: str,
    *,
    rule_id: str,
    score: int,
    note: str = "",
    context: str = "",
) -> dict[str, Any]:
    if score not in VALID_SCORES:
        raise ValueError("score 须为 1-5")
    rid = str(rule_id or "").strip()
    if not rid:
        raise ValueError("rule_id 不能为空")

    state = store.load_state(project_id)
    items = list(state.get("rule_feedback") or [])
    entry = {
        "feedback_id": f"rf_{uuid.uuid4().hex[:10]}",
        "rule_id": rid,
        "score": int(score),
        "note": str(note or "").strip(),
        "context": str(context or "").strip(),
        "at": datetime.now().isoformat(timespec="seconds"),
        "source": "agent",
    }
    items.append(entry)
    state["rule_feedback"] = items[-200:]
    store.save_state(project_id, state)
    return entry


def feedback_summary(store: ProjectStore, project_id: str) -> list[dict[str, Any]]:
    items = list_rule_feedback(store, project_id)
    by_rule: dict[str, list[int]] = {}
    for item in items:
        rid = str(item.get("rule_id", ""))
        if not rid:
            continue
        by_rule.setdefault(rid, []).append(int(item.get("score") or 0))
    out = []
    for rid, scores in by_rule.items():
        valid = [s for s in scores if s in VALID_SCORES]
        if not valid:
            continue
        out.append({
            "rule_id": rid,
            "count": len(valid),
            "avg_score": round(sum(valid) / len(valid), 2),
            "latest_note": next((i.get("note") for i in items if i.get("rule_id") == rid and i.get("note")), ""),
        })
    return sorted(out, key=lambda x: (-x["avg_score"], -x["count"]))
