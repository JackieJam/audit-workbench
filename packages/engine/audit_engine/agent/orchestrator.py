"""审计 Agent 编排。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from audit_engine.llm_client import make_openai_client
from audit_engine.agent.context import AuditSelection, selection_to_text
from audit_engine.agent.tools import TOOL_SCHEMAS, execute_tool
from audit_engine.llm_runtime import resolve_llm_runtime
from audit_engine.store import ProjectStore

SYSTEM_PROMPT = (
    "你是序时账审计分析专家助手。基于工具数据作答，不编造凭证与金额。"
    "用户左侧选中范围是优先分析对象。用中文简洁专业回复。"
    "能力指引："
    "1) 架构/模块 → list_analysis_modules、describe_agent_capabilities；"
    "2) 规则说明（只读）→ get_rules_catalog；规则改参/启停 → update_rule、toggle_rule（改前说明影响）；"
    "3) 规则命中与抽样 → get_rule_hit_summary、run_sampling_rules、extract_samples（大数据量可能较慢，先提示）；"
    "4) 规则迭代记忆 → record_rule_feedback、list_rule_feedback、suggest_rule_tuning、apply_rule_tuning；"
    "5) 模块 AI 分析 → run_module_insight、get_module_insight_cache、apply_module_insight_recommendations、get_module_insight_jobs；"
    "6) 跨年 → run_cross_year_audit、get_cross_year_findings；"
    "7) 列名映射 → get_column_mapping_status；抽样状态 → get_sampling_status；"
    "8) 打开左侧页签 → focus_analysis_view（分析）、run_sampling_rules/extract_samples（抽样）、apply_module_insight_recommendations（疑点库）。"
    "修改规则阈值时必须保留或更新 rationale。应用调参建议前向用户说明变更内容。"
)

MAX_TOOL_ROUNDS = 6
MAX_HISTORY = 20


def _suggested_followups(ctx: AuditSelection | None) -> list[str]:
    if not ctx:
        return [
            "概览项目年份与规模",
            "抽样规则有哪些？",
            "对费用模块做 AI 风险分析",
            "根据反馈建议规则调参",
        ]
    label = ctx.get("label", "当前范围")
    return [f"「{label}」有何风险？", f"解释「{label}」代表性凭证", f"将「{label}」纳入疑点库"]


def run_agent_chat(
    store: ProjectStore,
    project_id: str,
    *,
    user_message: str,
    pinned_context: AuditSelection | None = None,
    api_key: str | None = None,
    profile_id: str | None = None,
) -> dict[str, Any]:
    runtime = resolve_llm_runtime(profile_id=profile_id, manual_key=api_key)
    if not runtime.api_key:
        return {
            "reply": "未配置 LLM API Key。请在「大模型」页签保存方案并填入 Key，或设置 DEEPSEEK_API_KEY。",
            "tool_calls": [],
            "needs_api_key": True,
        }

    state = store.load_state(project_id)
    thread = dict(state.get("agent_thread") or {})
    history: list[dict[str, Any]] = list(thread.get("messages") or [])
    if pinned_context:
        thread["pinned_context"] = pinned_context
    ctx = thread.get("pinned_context") or pinned_context

    model = runtime.model
    client = make_openai_client(api_key=runtime.api_key, base_url=runtime.base_url, max_retries=2, timeout=90.0)

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"## 当前选中\n{selection_to_text(ctx if isinstance(ctx, dict) else None)}\n\n## 问题\n{user_message}"},
    ]
    for m in history[-MAX_HISTORY:]:
        if m.get("role") in ("user", "assistant") and m.get("content"):
            messages.insert(-1, {"role": m["role"], "content": m["content"]})

    tool_log: list[dict[str, Any]] = []
    ui_actions: list[dict[str, Any]] = []
    reply = ""

    for _ in range(MAX_TOOL_ROUNDS):
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=TOOL_SCHEMAS,
            tool_choice="auto",
            temperature=0.2,
            max_tokens=2000,
            timeout=90.0,
        )
        msg = resp.choices[0].message
        if msg.tool_calls:
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in msg.tool_calls
                ],
            })
            for tc in msg.tool_calls:
                fn = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = execute_tool(
                    store,
                    project_id,
                    fn,
                    args,
                    llm={
                        "api_key": runtime.api_key,
                        "model": runtime.model,
                        "base_url": runtime.base_url,
                    },
                )
                tool_log.append({"tool": fn, "args": args, "result": result})
                for act in result.get("ui_actions") or []:
                    if isinstance(act, dict) and act not in ui_actions:
                        ui_actions.append(act)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result, ensure_ascii=False, default=str)[:12000]})
            continue
        reply = (msg.content or "").strip()
        break

    if not reply:
        reply = "已完成查询，请换个问法或查看工具结果。"

    now = datetime.now().isoformat(timespec="seconds")
    history.append({"role": "user", "content": user_message, "at": now})
    history.append({"role": "assistant", "content": reply, "at": now})
    thread["messages"] = history[-MAX_HISTORY:]
    thread["updated_at"] = now
    state["agent_thread"] = thread
    store.save_state(project_id, state)

    return {
        "reply": reply,
        "tool_calls": tool_log,
        "pinned_context": thread.get("pinned_context"),
        "suggestions": _suggested_followups(ctx if isinstance(ctx, dict) else None),
        "ui_actions": ui_actions,
        "needs_api_key": False,
    }


def get_agent_state(store: ProjectStore, project_id: str) -> dict[str, Any]:
    thread = store.load_state(project_id).get("agent_thread") or {}
    return {"messages": thread.get("messages") or [], "pinned_context": thread.get("pinned_context"), "updated_at": thread.get("updated_at")}


def set_pinned_context(store: ProjectStore, project_id: str, context: AuditSelection | None) -> dict[str, Any]:
    state = store.load_state(project_id)
    thread = dict(state.get("agent_thread") or {})
    thread["pinned_context"] = context
    thread["updated_at"] = datetime.now().isoformat(timespec="seconds")
    state["agent_thread"] = thread
    store.save_state(project_id, state)
    return {"pinned_context": context, "suggestions": _suggested_followups(context)}


def clear_agent_thread(store: ProjectStore, project_id: str) -> None:
    state = store.load_state(project_id)
    thread = dict(state.get("agent_thread") or {})
    thread["messages"] = []
    state["agent_thread"] = thread
    store.save_state(project_id, state)
