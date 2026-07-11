"""Agent 触发的左侧 UI 动作（由前端解释执行）。"""

from __future__ import annotations

from typing import Any


def navigate_main(tab: str) -> dict[str, Any]:
    return {"type": "navigate_main", "tab": tab}


def finance_module(module: str, *, year: int | None = None) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = [
        navigate_main("finance"),
        {"type": "finance_module", "module": module},
    ]
    if year is not None:
        actions.append({"type": "set_finance_year", "year": int(year)})
    return actions


def attach_ui_actions(result: dict[str, Any], actions: list[dict[str, Any]]) -> dict[str, Any]:
    if not actions:
        return result
    merged = list(result.get("ui_actions") or [])
    merged.extend(actions)
    result["ui_actions"] = merged
    return result
