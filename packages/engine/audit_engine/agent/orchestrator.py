"""审计 Agent 编排。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from audit_engine.agent.context import AuditSelection, selection_to_text
from audit_engine.agent.tools import TOOL_SCHEMAS, execute_tool
from audit_engine.llm_client import make_openai_client
from audit_engine.llm_runtime import resolve_llm_runtime
from audit_engine.store import ProjectStore

SYSTEM_PROMPT = (
    "你是序时账审计分析专家助手。基于工具数据作答，不编造凭证与金额。"
    "凡涉及具体金额、科目、客户、供应商、月份或凭证的问题，优先调用 query_journal，"
    "并在答复中说明数据版本、筛选范围、命中行/凭证数与金额口径。"
    "数据质量问题调用 get_data_quality_review；分类决策只能通过 apply_classification_decisions 并等待用户批准。"
    "证据覆盖边界调用 get_evidence_inventory；疑点与结论的区分调用 get_audit_case_summary。"
    "用户左侧选中范围是优先分析对象。用中文简洁专业回复。"
    "能力指引："
    "1) 架构/模块 → list_analysis_modules、describe_agent_capabilities；"
    "2) 规则说明（只读）→ get_rules_catalog；规则改参/启停 → update_rule、toggle_rule（改前说明影响）；"
    "3) 规则命中与抽样 → get_rule_hit_summary、run_sampling_rules、extract_samples（大数据量可能较慢，先提示）；"
    "4) 规则迭代记忆 → record_rule_feedback、list_rule_feedback、suggest_rule_tuning、apply_rule_tuning；"
    "5) 模块 AI 分析 → run_module_insight、get_module_insight_cache、apply_module_insight_recommendations、get_module_insight_jobs；"
    "   若用户要求「对所有模块进行AI风险分析」，依次对有风险问题配置的模块执行 run_module_insight："
    "   收入成本、费用、营业外与投资收益、暂估往来、资产负债、调账冲销；"
    "   每完成一个模块简要汇报，全部完成后再汇总主要风险与可入库抽样建议；"
    "6) 跨年 → run_cross_year_audit、get_cross_year_findings；"
    "7) 列名映射 → get_column_mapping_status；抽样状态 → get_sampling_status；"
    "8) 打开左侧页签 → focus_analysis_view（分析）、run_sampling_rules/extract_samples（抽样）、apply_module_insight_recommendations（疑点库）。"
    "修改规则阈值时必须保留或更新 rationale。应用调参建议前向用户说明变更内容。"
)

MAX_TOOL_ROUNDS = 10
MAX_HISTORY = 40
MAX_AUDIT_EVENTS = 200
MUTATING_TOOLS = {
    "add_to_candidate_pool",
    "update_rule",
    "toggle_rule",
    "apply_rule_tuning",
    "apply_module_insight_recommendations",
    "apply_classification_decisions",
}


def _queue_pending_action(
    thread: dict[str, Any],
    tool: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    pending = list(thread.get("pending_actions") or [])
    signature = json.dumps({"tool": tool, "args": args}, ensure_ascii=False, sort_keys=True, default=str)
    existing = next(
        (item for item in pending if item.get("signature") == signature and item.get("status") == "pending"),
        None,
    )
    if existing:
        return existing
    now = datetime.now().isoformat(timespec="seconds")
    action_id = "act_" + hashlib.sha1(f"{signature}|{now}".encode()).hexdigest()[:12]
    action = {
        "action_id": action_id,
        "tool": tool,
        "args": args,
        "signature": signature,
        "status": "pending",
        "created_at": now,
    }
    pending.append(action)
    thread["pending_actions"] = pending[-50:]
    return action


def _compact_tool_log(tool_log: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compacted: list[dict[str, Any]] = []
    for item in tool_log:
        result = item.get("result") or {}
        raw = json.dumps(result, ensure_ascii=False, default=str)
        safe_result = result if len(raw) <= 4000 else {"truncated": True, "preview": raw[:4000]}
        compacted.append({"tool": item.get("tool"), "args": item.get("args") or {}, "result": safe_result})
    return compacted


def _history_content(message: dict[str, Any]) -> str:
    content = str(message.get("content") or "")
    tool_calls = list(message.get("tool_calls") or [])
    if tool_calls:
        summaries = []
        for call in tool_calls:
            result = call.get("result") or {}
            status = "失败" if result.get("error") else "完成"
            summaries.append(f"{call.get('tool')}: {status}")
        content += "\n\n[上一轮工具执行] " + "；".join(summaries)
    return content


DEFAULT_AGENT_SUGGESTIONS = [
    "对所有模块进行AI风险分析",
    "概览项目年份与规模",
    "抽样规则有哪些？",
    "根据反馈建议规则调参",
]


def _suggested_followups(ctx: AuditSelection | None) -> list[str]:
    if not ctx:
        return list(DEFAULT_AGENT_SUGGESTIONS)
    label = ctx.get("label", "当前范围")
    selector = ctx.get("selector") or {}
    if selector.get("kind") == "module_overview":
        module = selector.get("module")
        if module == "profile":
            return [
                "解释当前统计画像的异常信号",
                "本福特偏离应该如何复核？",
                "哪些月份存在期末集中风险？",
                "根据金额分布建议抽样层级",
            ]
        if module == "cross":
            return [
                "概览跨年稽核发现",
                "哪些跨年异常最值得优先复核？",
                "解释跨年稽核的规则口径",
                "查看跨年异常涉及的凭证",
            ]
        return [
            f"分析「{label}」的主要风险",
            f"解释「{label}」的图表口径",
            f"运行「{label}」AI 风险分析",
            "检查当前项目的数据质量与覆盖度",
        ]
    if selector.get("kind") == "workspace_overview":
        if selector.get("workspace") == "suspects":
            return [
                "概览当前疑点及风险等级",
                "哪些疑点最值得优先复核？",
                "解释疑点进入候选库的依据",
                "下一步应补充哪些审计证据？",
            ]
        return [
            "检查当前抽样是否可以导出",
            "概览规则命中与样本规模",
            "解释当前抽样规则",
            "根据复核反馈建议规则调参",
        ]
    return [
        "对所有模块进行AI风险分析",
        f"「{label}」有何风险？",
        f"解释「{label}」代表性凭证",
        f"将「{label}」纳入疑点库",
    ]


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
            messages.insert(-1, {"role": m["role"], "content": _history_content(m)})

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
                if fn in MUTATING_TOOLS:
                    action = _queue_pending_action(thread, fn, args)
                    result = {
                        "approval_required": True,
                        "action_id": action["action_id"],
                        "tool": fn,
                        "message": "该操作会修改项目状态，需用户确认后执行。",
                    }
                else:
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
    compact_tool_log = _compact_tool_log(tool_log)
    history.append({"role": "assistant", "content": reply, "at": now, "tool_calls": compact_tool_log})
    thread["messages"] = history[-MAX_HISTORY:]
    thread["updated_at"] = now
    events = list(thread.get("audit_events") or [])
    events.append({
        "at": now,
        "user_message": user_message,
        "tool_calls": compact_tool_log,
        "reply": reply,
    })
    thread["audit_events"] = events[-MAX_AUDIT_EVENTS:]
    # 工具执行可能已写入最新项目状态；必须重新加载，避免旧 state 覆盖工具结果。
    latest_state = store.load_state(project_id)
    latest_state["agent_thread"] = thread
    store.save_state(project_id, latest_state)

    return {
        "reply": reply,
        "tool_calls": compact_tool_log,
        "pinned_context": thread.get("pinned_context"),
        "suggestions": _suggested_followups(ctx if isinstance(ctx, dict) else None),
        "ui_actions": ui_actions,
        "needs_api_key": False,
    }


def get_agent_state(store: ProjectStore, project_id: str) -> dict[str, Any]:
    thread = store.load_state(project_id).get("agent_thread") or {}
    actions_by_id = {
        str(action.get("action_id")): action
        for action in thread.get("pending_actions") or []
        if action.get("action_id")
    }
    messages = json.loads(json.dumps(thread.get("messages") or [], ensure_ascii=False, default=str))
    for message in messages:
        for call in message.get("tool_calls") or []:
            result = call.get("result") or {}
            action = actions_by_id.get(str(result.get("action_id") or ""))
            if action and action.get("status") != "pending":
                result["approval_required"] = False
                result["action_status"] = action.get("status")
                result["resolved_result"] = action.get("result") or {}
    return {
        "messages": messages,
        "pinned_context": thread.get("pinned_context"),
        "suggestions": _suggested_followups(
            thread.get("pinned_context") if isinstance(thread.get("pinned_context"), dict) else None
        ),
        "updated_at": thread.get("updated_at"),
        "audit_events": thread.get("audit_events") or [],
        "pending_actions": [
            action for action in thread.get("pending_actions") or [] if action.get("status") == "pending"
        ],
    }


def resolve_pending_action(
    store: ProjectStore,
    project_id: str,
    action_id: str,
    *,
    approve: bool,
) -> dict[str, Any]:
    state = store.load_state(project_id)
    thread = dict(state.get("agent_thread") or {})
    pending = list(thread.get("pending_actions") or [])
    action = next((item for item in pending if item.get("action_id") == action_id), None)
    if not action:
        raise ValueError("待确认操作不存在或已过期")
    if action.get("status") != "pending":
        raise ValueError(f"操作已处理：{action.get('status')}")

    now = datetime.now().isoformat(timespec="seconds")
    if approve:
        result = execute_tool(store, project_id, str(action["tool"]), dict(action.get("args") or {}))
        status = "approved" if not result.get("error") else "failed"
    else:
        result = {"ok": True, "rejected": True}
        status = "rejected"
    action["status"] = status
    action["resolved_at"] = now
    action["result"] = result

    events = list(thread.get("audit_events") or [])
    events.append({
        "at": now,
        "action_id": action_id,
        "tool": action.get("tool"),
        "args": action.get("args") or {},
        "decision": status,
        "result": result,
    })
    thread["pending_actions"] = pending
    thread["audit_events"] = events[-MAX_AUDIT_EVENTS:]
    thread["updated_at"] = now
    latest_state = store.load_state(project_id)
    latest_state["agent_thread"] = thread
    store.save_state(project_id, latest_state)
    return {"action_id": action_id, "status": status, "result": result, "ui_actions": result.get("ui_actions") or []}


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
