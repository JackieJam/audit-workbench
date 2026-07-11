"""Agent 工具注册表 — 只返回聚合/限量明细。"""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

from audit_engine.analysis.drilldown import resolve_drilldown
from audit_engine.candidate_pool import add_candidate_group, build_candidate_group, pool_stats
from audit_engine.pipeline import AnalysisPipeline
from audit_engine.profiler import build_financial_summary, financials_to_summary_text
from audit_engine.agent.audit_questions import (
    MODULE_ID_TO_KEY,
    MODULE_KEY_TO_ID,
    load_module_questions,
    resolve_module_key,
)
from audit_engine.agent.rule_memory import (
    feedback_summary,
    list_rule_feedback,
    record_rule_feedback,
)
from audit_engine.agent.rule_ops import merge_rules_from_state, patch_rule
from audit_engine.agent.rule_tuning import apply_rule_tuning_suggestion, suggest_rule_tuning
from audit_engine.agent.ui_actions import attach_ui_actions, finance_module, navigate_main
from audit_engine.experience.column_aliases import learned_column_aliases
from audit_engine.module_insight import apply_recommendations, run_module_insight_pipeline
from audit_engine.module_insight_jobs import list_jobs
from audit_engine.store import ProjectStore


ANALYSIS_MODULES: list[dict[str, str]] = [
    {"id": "income", "label": "收入成本", "desc": "月度收入成本、客户Top10、钻取、AI风险分析"},
    {"id": "expense", "label": "费用", "desc": "跨年费用结构对比、钻取"},
    {"id": "working_capital", "label": "暂估往来", "desc": "应付暂估/其他应收/其他应付月度与钻取"},
    {"id": "balance_sheet", "label": "资产负债", "desc": "科目类别月度发生额与科目构成"},
    {"id": "adjustment", "label": "调账冲销", "desc": "调账冲销凭证摘要"},
    {"id": "profile", "label": "统计画像", "desc": "各年统计画像与财务摘要"},
    {"id": "cross", "label": "跨年稽核", "desc": "跨年异常发现列表"},
]

CROSS_YEAR_DETECTORS = [
    "预提冲回配对",
    "预提冲回金额不符",
    "收入跨年确认",
    "期末余额持续累积",
    "对手方跨年资金循环",
    "费用科目年度突变",
    "手工凭证占比持续上升",
    "新科目组合涌现",
]

SAMPLING_RULE_KEYS = [
    "splitting",
    "large_amount",
    "manual_entry",
    "accrual_anomaly",
    "yearend_surge",
    "financing_trade",
    "cash_pool",
    "user_concentration",
    "reversal_pattern",
    "sensitive_fees",
]

CROSS_YEAR_RULE_KEYS = ["cross_year_accrual", "cross_year_revenue", "cross_year_detection"]
EDITABLE_RULE_KEYS = SAMPLING_RULE_KEYS + CROSS_YEAR_RULE_KEYS + ["max_sample_size"]

TOOL_LABELS: dict[str, str] = {
    "get_project_overview": "项目概览",
    "query_drilldown": "钻取分录",
    "list_candidates": "疑点库列表",
    "add_to_candidate_pool": "加入疑点库",
    "list_analysis_modules": "分析模块目录",
    "get_rules_catalog": "规则目录（只读）",
    "focus_analysis_view": "打开分析页签",
    "run_cross_year_audit": "执行跨年稽核",
    "get_cross_year_findings": "跨年稽核结果",
    "describe_agent_capabilities": "助手能力说明",
    "update_rule": "更新规则参数",
    "toggle_rule": "启用/停用规则",
    "get_rule_hit_summary": "规则命中摘要",
    "run_sampling_rules": "执行抽样规则",
    "extract_samples": "生成抽样底稿",
    "record_rule_feedback": "规则打分反馈",
    "list_rule_feedback": "规则反馈记录",
    "get_column_mapping_status": "列名映射状态",
    "get_module_insight_cache": "模块 AI 分析缓存",
    "suggest_rule_tuning": "规则调参建议",
    "apply_rule_tuning": "应用调参建议",
    "run_module_insight": "生成模块 AI 分析",
    "apply_module_insight_recommendations": "应用 AI 抽样建议",
    "get_module_insight_jobs": "模块分析进度",
    "get_sampling_status": "抽样状态",
}


class ToolLlmContext(dict):
    """execute_tool 可选 LLM 运行时（api_key/model/base_url）。"""


def _sampling_nav_actions(project_id: str) -> list[dict[str, Any]]:
    return [
        navigate_main("sampling"),
        {"type": "invalidate_queries", "queryKey": ["samples", project_id]},
        {"type": "invalidate_queries", "queryKey": ["rule-results", project_id]},
    ]


def _suspects_nav_actions(project_id: str) -> list[dict[str, Any]]:
    return [
        navigate_main("suspects"),
        {"type": "invalidate_queries", "queryKey": ["candidates", project_id]},
    ]


def _insight_nav_actions(project_id: str, module_key: str) -> list[dict[str, Any]]:
    module_id = MODULE_KEY_TO_ID.get(module_key, "income")
    return [
        finance_module(module_id)[0],
        finance_module(module_id)[1],
        {"type": "invalidate_queries", "queryKey": ["module-insight", project_id, module_key]},
        {"type": "invalidate_queries", "queryKey": ["insight-jobs", project_id]},
    ]


def _insight_brief(insight: dict[str, Any]) -> dict[str, Any]:
    return {
        "executive_summary": insight.get("executive_summary"),
        "findings_count": len(insight.get("findings") or []),
        "recommendations_count": len(insight.get("recommendations") or []),
        "findings": (insight.get("findings") or [])[:3],
        "recommendations": (insight.get("recommendations") or [])[:3],
        "input_signature": insight.get("input_signature"),
    }


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_project_overview",
            "description": "获取项目年份、行数、列映射缺失、疑点库统计、财务摘要",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_drilldown",
            "description": "按 selector 钻取分录（限量）",
            "parameters": {
                "type": "object",
                "properties": {
                    "selector": {"type": "object"},
                    "limit": {"type": "integer"},
                },
                "required": ["selector"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_candidates",
            "description": "列出疑点库分组",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_to_candidate_pool",
            "description": "将 selector 范围加入疑点库",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "source_module": {"type": "string"},
                    "source_view": {"type": "string"},
                    "selector": {"type": "object"},
                    "reason": {"type": "string"},
                    "voucher_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "selector"],
            },
        },
    },

    {
        "type": "function",
        "function": {
            "name": "list_analysis_modules",
            "description": "列出左侧财务画像各分析模块及能力，用于回答「有哪些分析」或决定打开哪一页",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_rules_catalog",
            "description": "获取跨年稽核检测项与抽样规则配置（含阈值说明 rationale，只读）",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "focus_analysis_view",
            "description": "打开左侧分析模块页签（收入成本/费用/跨年稽核等），可选指定年度",
            "parameters": {
                "type": "object",
                "properties": {
                    "module": {
                        "type": "string",
                        "enum": ["income", "expense", "working_capital", "balance_sheet", "adjustment", "profile", "cross"],
                        "description": "分析模块 id",
                    },
                    "year": {"type": "integer"},
                },
                "required": ["module"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_cross_year_audit",
            "description": "执行跨年稽核并打开跨年页签展示结果",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_cross_year_findings",
            "description": "获取跨年稽核发现",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "describe_agent_capabilities",
            "description": "列出助手全部工具及用途，回答「你能做什么」",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_rule",
            "description": "更新单条规则参数（阈值、rationale 等）；不可删除规则，只能改参或停用",
            "parameters": {
                "type": "object",
                "properties": {
                    "rule_id": {"type": "string", "description": "规则 id，如 large_amount、cross_year_revenue"},
                    "patches": {"type": "object", "description": "要合并的字段，如 round_number_threshold 等"},
                },
                "required": ["rule_id", "patches"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "toggle_rule",
            "description": "启用或停用单条规则",
            "parameters": {
                "type": "object",
                "properties": {
                    "rule_id": {"type": "string"},
                    "enabled": {"type": "boolean"},
                },
                "required": ["rule_id", "enabled"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_rule_hit_summary",
            "description": "读取已缓存的规则命中统计；若未执行过规则则提示先 run_sampling_rules",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_sampling_rules",
            "description": "对全量序时账执行抽样规则引擎（大数据量可能较慢），并打开抽样页签",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "extract_samples",
            "description": "按规则命中或疑点库生成抽样底稿样本",
            "parameters": {
                "type": "object",
                "properties": {
                    "method": {"type": "string", "enum": ["by_rule", "random", "all"], "description": "抽样方法"},
                    "size": {"type": "integer", "description": "样本量上限，默认读 max_sample_size"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "record_rule_feedback",
            "description": "为规则打分 1-5 并附备注，用于迭代记忆",
            "parameters": {
                "type": "object",
                "properties": {
                    "rule_id": {"type": "string"},
                    "score": {"type": "integer", "minimum": 1, "maximum": 5},
                    "note": {"type": "string"},
                    "context": {"type": "string", "description": "触发场景，如某次误报/漏报"},
                },
                "required": ["rule_id", "score"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_rule_feedback",
            "description": "列出规则反馈记录与均分摘要",
            "parameters": {
                "type": "object",
                "properties": {"rule_id": {"type": "string"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_column_mapping_status",
            "description": "列名映射缺失项、已确认映射、全局学习别名数量",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_module_insight_cache",
            "description": "读取各分析模块 AI 洞察缓存（不触发新 LLM 调用）",
            "parameters": {
                "type": "object",
                "properties": {"module_key": {"type": "string"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "suggest_rule_tuning",
            "description": "基于规则反馈均分与命中量，生成阈值调参建议（不自动应用）",
            "parameters": {
                "type": "object",
                "properties": {"rule_id": {"type": "string", "description": "可选，指定单条规则"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_rule_tuning",
            "description": "应用 suggest_rule_tuning 生成的调参建议",
            "parameters": {
                "type": "object",
                "properties": {
                    "suggestion_id": {"type": "string"},
                    "rule_id": {"type": "string"},
                    "apply_all": {"type": "boolean", "description": "应用全部高置信建议"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_module_insight",
            "description": "对指定分析模块运行 AI 风险分析（需 LLM，耗时约 30-90s）",
            "parameters": {
                "type": "object",
                "properties": {
                    "module": {
                        "type": "string",
                        "description": "模块 id（income/expense/…）或中文名（费用/收入成本）",
                    },
                },
                "required": ["module"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_module_insight_recommendations",
            "description": "将模块 AI 分析中的抽样建议加入疑点库",
            "parameters": {
                "type": "object",
                "properties": {
                    "module": {"type": "string"},
                    "indices": {"type": "array", "items": {"type": "integer"}, "description": "建议下标，空则全部"},
                },
                "required": ["module"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_module_insight_jobs",
            "description": "查询各模块 AI 分析任务阶段与进度",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_sampling_status",
            "description": "抽样流水线状态：规则是否已跑、样本量、可否导出",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


def _records(df: pd.DataFrame, limit: int = 30) -> list[dict[str, Any]]:
    if df.empty:
        return []
    out = df.head(limit).copy()
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            out[col] = out[col].dt.strftime("%Y-%m-%d")
    return json.loads(out.to_json(orient="records", force_ascii=False))




def execute_tool(
    store: ProjectStore,
    project_id: str,
    name: str,
    arguments: dict[str, Any],
    *,
    llm: ToolLlmContext | None = None,
) -> dict[str, Any]:
    manifest = store.load_manifest(project_id)
    state = store.load_state(project_id)

    if name == "get_project_overview":
        financials = {}
        fin_error = None
        for year in manifest.years:
            work = store.get_work_df(project_id, year)
            if work.empty:
                continue
            try:
                financials[year] = build_financial_summary(work, year)
            except Exception as exc:
                fin_error = str(exc)
                break
        pool = store.load_candidate_pool(project_id)
        return {
            "project_name": manifest.project_name,
            "years": manifest.years,
            "total_rows": manifest.total_rows,
            "missing_columns": state.get("missing_columns") or [],
            "candidate_stats": pool_stats(pool),
            "financial_summary": financials_to_summary_text(financials) if financials else "",
            "financial_summary_error": fin_error,
        }

    if name == "query_drilldown":
        selector = arguments.get("selector") or {}
        year = selector.get("year")
        if year is None:
            return {"error": "selector 缺少 year"}
        limit = min(int(arguments.get("limit") or 30), 50)
        work = store.get_work_df(project_id, int(year))
        df = resolve_drilldown(work, selector, limit=limit)
        vids = sorted({str(v) for v in df["凭证编号"].dropna().astype(str).tolist()}) if not df.empty else []
        return {"row_count": len(df), "voucher_count": len(vids), "voucher_ids_sample": vids[:20], "rows": _records(df, limit)}

    if name == "list_candidates":
        pool = store.load_candidate_pool(project_id)
        brief = [{"title": g.get("title"), "status": g.get("status"), "voucher_count": g.get("voucher_count")} for g in pool]
        return {"groups": brief, "stats": pool_stats(pool)}

    if name == "add_to_candidate_pool":
        selector = arguments.get("selector") or {}
        year = selector.get("year")
        if year is None:
            return {"error": "selector 缺少 year"}
        work = store.get_work_df(project_id, int(year))
        detail = resolve_drilldown(work, selector)
        vids = {str(v).strip() for v in (arguments.get("voucher_ids") or []) if str(v).strip()}
        if vids:
            detail = detail[detail["凭证编号"].astype(str).isin(vids)].copy()
        if detail.empty:
            return {"error": "无匹配分录"}
        group = build_candidate_group(
            title=str(arguments.get("title") or "Agent 疑点"),
            source_module=str(arguments.get("source_module") or "Agent"),
            source_view=str(arguments.get("source_view") or "对话建议"),
            detail=detail,
            reason=str(arguments.get("reason") or ""),
            selector=selector,
            created_by="agent",
        )
        pool = add_candidate_group(store.load_candidate_pool(project_id), group)
        store.save_candidate_pool(project_id, pool)
        return {"ok": True, "group_id": group["group_id"], "voucher_count": group["voucher_count"]}


    if name == "list_analysis_modules":
        return {"modules": ANALYSIS_MODULES}

    if name == "get_rules_catalog":
        cfg = merge_rules_from_state(state)
        cross_cfg = {
            "cross_year_accrual": cfg.get("cross_year_accrual"),
            "cross_year_revenue": cfg.get("cross_year_revenue"),
            "cross_year_detection": cfg.get("cross_year_detection"),
        }
        sampling = []
        for key in SAMPLING_RULE_KEYS:
            if key in cfg and isinstance(cfg[key], dict):
                item = dict(cfg[key])
                sampling.append({
                    "rule_id": key,
                    "enabled": item.get("enabled", True),
                    "rationale": item.get("rationale", ""),
                    "params": {k: v for k, v in item.items() if k not in {"enabled", "rationale"}},
                })
        return {
            "cross_year_detectors": CROSS_YEAR_DETECTORS,
            "cross_year_config": cross_cfg,
            "sampling_rules": sampling,
        }

    if name == "focus_analysis_view":
        module = str(arguments.get("module") or "").strip()
        if module not in {m["id"] for m in ANALYSIS_MODULES}:
            return {"error": f"未知模块: {module}"}
        year = arguments.get("year")
        year_int = int(year) if year is not None else None
        if year_int is not None and year_int not in manifest.years:
            return {"error": f"年度 {year_int} 不在项目范围内"}
        return attach_ui_actions(
            {"ok": True, "module": module, "label": next(m["label"] for m in ANALYSIS_MODULES if m["id"] == module)},
            finance_module(module, year=year_int),
        )

    if name == "run_cross_year_audit":
        if len(manifest.years) < 2:
            return {"error": "需要至少两个年度"}
        pipeline = AnalysisPipeline(store)
        findings = pipeline.run_cross_year(project_id)
        brief = [
            {"category": f.get("category"), "severity": f.get("severity"), "description": f.get("description")}
            for f in findings[:20]
        ]
        return attach_ui_actions(
            {"ok": True, "count": len(findings), "findings": brief},
            finance_module("cross"),
        )


    if name == "get_cross_year_findings":
        cached = state.get("cross_year_findings")
        if cached:
            return attach_ui_actions({"count": len(cached), "findings": cached[:15]}, finance_module("cross"))
        if len(manifest.years) < 2:
            return {"error": "需要至少两个年度"}
        pipeline = AnalysisPipeline(store)
        findings = pipeline.run_cross_year(project_id)
        brief = [{"category": f.get("category"), "severity": f.get("severity"), "description": f.get("description")} for f in findings[:15]]
        return attach_ui_actions({"count": len(findings), "findings": brief}, finance_module("cross"))

    if name == "describe_agent_capabilities":
        caps = []
        for schema in TOOL_SCHEMAS:
            fn = schema.get("function") or {}
            caps.append({
                "name": fn.get("name"),
                "label": TOOL_LABELS.get(str(fn.get("name")), fn.get("name")),
                "description": fn.get("description"),
            })
        return {"tools": caps, "analysis_modules": ANALYSIS_MODULES, "cross_year_detectors": CROSS_YEAR_DETECTORS}

    if name == "update_rule":
        rule_id = str(arguments.get("rule_id") or "").strip()
        patches = arguments.get("patches") or {}
        if not isinstance(patches, dict) or not patches:
            return {"error": "patches 须为非空对象"}
        if rule_id not in EDITABLE_RULE_KEYS and rule_id != "max_sample_size":
            return {"error": f"不可编辑的规则: {rule_id}", "editable": EDITABLE_RULE_KEYS}
        try:
            updated = patch_rule(store, project_id, rule_id, patches)
        except ValueError as exc:
            return {"error": str(exc)}
        return {"ok": True, "rule_id": rule_id, "rule": updated}

    if name == "toggle_rule":
        rule_id = str(arguments.get("rule_id") or "").strip()
        if rule_id not in EDITABLE_RULE_KEYS:
            return {"error": f"未知规则: {rule_id}", "editable": EDITABLE_RULE_KEYS}
        enabled = bool(arguments.get("enabled"))
        updated = patch_rule(store, project_id, rule_id, {"enabled": enabled})
        return {"ok": True, "rule_id": rule_id, "enabled": enabled, "rule": updated}

    if name == "get_rule_hit_summary":
        results = list(state.get("rule_results") or [])
        by_rule = [
            {
                "rule_name": r.get("rule_name"),
                "count": int(r.get("count") or len(r.get("hits") or [])),
            }
            for r in results
        ]
        feedback = feedback_summary(store, project_id)
        return {
            "has_cached": bool(results),
            "total_rules": len(by_rule),
            "total_hits": sum(x["count"] for x in by_rule),
            "by_rule": by_rule,
            "feedback_summary": feedback,
            "hint": None if results else "尚未执行规则，可调用 run_sampling_rules",
        }

    if name == "run_sampling_rules":
        pipeline = AnalysisPipeline(store)
        results = pipeline.run_rules(project_id)
        brief = [
            {"rule_name": r.get("rule_name"), "count": int(r.get("count") or len(r.get("hits") or []))}
            for r in results
        ]
        return attach_ui_actions(
            {
                "ok": True,
                "rule_count": len(results),
                "total_hits": sum(b["count"] for b in brief),
                "by_rule": brief[:20],
                "note": "全量规则已执行并缓存，可在抽样页签生成底稿",
            },
            _sampling_nav_actions(project_id),
        )

    if name == "extract_samples":
        method = str(arguments.get("method") or "by_rule")
        size = arguments.get("size")
        size_int = int(size) if size is not None else None
        pipeline = AnalysisPipeline(store)
        try:
            out = pipeline.extract_samples(project_id, method=method, size=size_int)
        except Exception as exc:
            return {"error": str(exc)}
        return attach_ui_actions(
            {"ok": True, **out},
            _sampling_nav_actions(project_id),
        )

    if name == "record_rule_feedback":
        try:
            entry = record_rule_feedback(
                store,
                project_id,
                rule_id=str(arguments.get("rule_id") or ""),
                score=int(arguments.get("score") or 0),
                note=str(arguments.get("note") or ""),
                context=str(arguments.get("context") or ""),
            )
        except ValueError as exc:
            return {"error": str(exc)}
        return {"ok": True, "feedback": entry}

    if name == "list_rule_feedback":
        rule_id = str(arguments.get("rule_id") or "").strip()
        items = list_rule_feedback(store, project_id, rule_id=rule_id)
        return {"items": items[:30], "summary": feedback_summary(store, project_id)}

    if name == "get_column_mapping_status":
        mapping = dict(state.get("column_mapping") or {})
        missing = list(state.get("missing_columns") or [])
        learned = learned_column_aliases()
        return {
            "missing_columns": missing,
            "confirmed_mapping_count": len(mapping),
            "confirmed_mapping": mapping,
            "learned_alias_count": len(learned),
            "learned_aliases_sample": dict(list(learned.items())[:12]),
        }

    if name == "get_module_insight_cache":
        module_key = str(arguments.get("module_key") or arguments.get("module") or "").strip()
        if module_key:
            try:
                module_key = resolve_module_key(module_key)
            except ValueError:
                pass
        insights = dict(state.get("module_insights") or {})
        if module_key:
            cached = insights.get(module_key)
            if not cached:
                return {"module_key": module_key, "cached": False}
            return {"module_key": module_key, "cached": True, "insight": _insight_brief(cached)}
        modules = {
            k: {
                "executive_summary": (v or {}).get("executive_summary"),
                "findings_count": len((v or {}).get("findings") or []),
                "recommendations_count": len((v or {}).get("recommendations") or []),
            }
            for k, v in insights.items()
        }
        return {"cached_modules": list(modules.keys()), "modules": modules}

    if name == "suggest_rule_tuning":
        rule_id = str(arguments.get("rule_id") or "").strip()
        return suggest_rule_tuning(store, project_id, rule_id=rule_id)

    if name == "apply_rule_tuning":
        return apply_rule_tuning_suggestion(
            store,
            project_id,
            suggestion_id=str(arguments.get("suggestion_id") or ""),
            rule_id=str(arguments.get("rule_id") or ""),
            apply_all=bool(arguments.get("apply_all")),
        )

    if name == "run_module_insight":
        if not llm or not llm.get("api_key"):
            return {"error": "未配置 LLM API Key，无法运行模块分析"}
        try:
            module_key = resolve_module_key(str(arguments.get("module") or ""))
        except ValueError as exc:
            return {"error": str(exc)}
        questions = load_module_questions(module_key)
        if not questions:
            return {"error": f"模块 {module_key} 无风险问题配置"}
        try:
            insight = run_module_insight_pipeline(
                store,
                project_id,
                module_key,
                risk_questions=questions,
                api_key=str(llm.get("api_key")),
                model=llm.get("model"),
                base_url=llm.get("base_url"),
            )
        except Exception as exc:
            return {"error": f"模块分析失败: {exc}"}
        return attach_ui_actions(
            {"ok": True, "module_key": module_key, "insight": _insight_brief(insight)},
            _insight_nav_actions(project_id, module_key),
        )

    if name == "apply_module_insight_recommendations":
        try:
            module_key = resolve_module_key(str(arguments.get("module") or ""))
        except ValueError as exc:
            return {"error": str(exc)}
        insight = (store.load_state(project_id).get("module_insights") or {}).get(module_key)
        if not insight:
            return {"error": f"请先生成 {module_key} 的 AI 分析（run_module_insight）"}
        recs = insight.get("recommendations") or []
        if not recs:
            return {"error": "该模块无抽样建议"}
        indices = arguments.get("indices")
        idx_list = [int(i) for i in indices] if isinstance(indices, list) else None
        result = apply_recommendations(
            store,
            project_id,
            recs,
            module_key=module_key,
            indices=idx_list,
        )
        return attach_ui_actions({"ok": True, **result, "module_key": module_key}, _suspects_nav_actions(project_id))

    if name == "get_module_insight_jobs":
        return list_jobs(store, project_id)

    if name == "get_sampling_status":
        samples = list(state.get("samples") or [])
        rule_results = list(state.get("rule_results") or [])
        voucher_ids = {str(s.get("凭证编号")) for s in samples if s.get("凭证编号")}
        return {
            "rules_executed": bool(rule_results),
            "rule_hit_total": sum(int(r.get("count") or len(r.get("hits") or [])) for r in rule_results),
            "sample_rows": len(samples),
            "sample_vouchers": len(voucher_ids),
            "max_sample_size": merge_rules_from_state(state).get("max_sample_size", 50),
            "ready_for_export": len(samples) > 0,
            "hint": "可在抽样底稿页签导出 Excel" if samples else "请先 run_sampling_rules 与 extract_samples",
        }

    return {"error": f"未知工具: {name}"}
