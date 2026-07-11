"""Agent 选中态与对话上下文协议。"""

from __future__ import annotations

from typing import Any, TypedDict


class AuditSelection(TypedDict, total=False):
    label: str
    source_module: str
    source_view: str
    selector: dict[str, Any]
    summary: dict[str, Any]


def selection_to_text(ctx: AuditSelection | None) -> str:
    if not ctx:
        return "（用户未在左侧选中具体图表/表格范围）"
    parts = [
        f"标签: {ctx.get('label', '')}",
        f"模块: {ctx.get('source_module', '')}",
        f"视图: {ctx.get('source_view', '')}",
        f"selector: {ctx.get('selector', {})}",
    ]
    summary = ctx.get("summary") or {}
    if summary:
        parts.append(f"摘要: {summary}")
    return "\n".join(parts)
