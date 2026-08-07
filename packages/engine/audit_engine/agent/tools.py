"""Agent 工具注册表 — 只返回聚合/限量明细。"""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

from audit_engine.account_classifier import ALL_CATEGORIES, CAT_UNCATEGORIZED
from audit_engine.agent.audit_questions import (
    MODULE_KEY_TO_ID,
    load_module_questions,
    resolve_module_key,
)
from audit_engine.agent.journal_query import run_journal_query
from audit_engine.agent.rule_memory import (
    feedback_summary,
    list_rule_feedback,
    record_rule_feedback,
)
from audit_engine.agent.rule_ops import merge_rules_from_state, patch_rule
from audit_engine.agent.rule_tuning import apply_rule_tuning_suggestion, suggest_rule_tuning
from audit_engine.agent.ui_actions import attach_ui_actions, finance_module, navigate_main
from audit_engine.analysis.drilldown import resolve_drilldown
from audit_engine.audit_case import AuditCaseService
from audit_engine.candidate_pool import add_candidate_group, build_candidate_group, pool_stats
from audit_engine.data_columns import analysis_quality_summary
from audit_engine.experience.column_aliases import learned_column_aliases
from audit_engine.module_insight import apply_recommendations, run_module_insight_pipeline
from audit_engine.module_insight_jobs import list_jobs
from audit_engine.pipeline import AnalysisPipeline
from audit_engine.profiler import build_financial_summary, financials_to_summary_text
from audit_engine.store import ProjectStore

ANALYSIS_MODULES: list[dict[str, str]] = [
    {"id": "income", "label": "收入成本", "desc": "月度收入成本、客户Top10、钻取、AI风险分析"},
    {"id": "expense", "label": "费用", "desc": "跨年费用结构对比、钻取"},
    {"id": "other_pnl", "label": "其他损益", "desc": "投资、公允价值、其他收益、营业外、减值与所得税分析"},
    {"id": "cost_variance", "label": "成本差异", "desc": "标准成本差异结转去向、期末集中度与异常波动"},
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
    "应收累计净发生持续累积",
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
    "query_journal": "查询序时账",
    "set_analysis_currency_scope": "切换财务画像币种",
    "get_data_quality_review": "数据质量复核",
    "apply_classification_decisions": "应用科目分类决策",
    "get_evidence_inventory": "证据来源清单",
    "get_audit_case_summary": "审计事项摘要",
    "get_audit_case_detail": "审计事项详情",
    "manage_audit_case": "推进审计事项",
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


def _cases_nav_actions(project_id: str) -> list[dict[str, Any]]:
    return [
        navigate_main("cases"),
        {"type": "invalidate_queries", "queryKey": ["audit-cases", project_id]},
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
            "name": "query_journal",
            "description": "按白名单条件查询全项目序时账，返回聚合、限量样本与可核验的数据版本/筛选口径；适合回答任意科目、摘要、客户、供应商、金额和期间问题",
            "parameters": {
                "type": "object",
                "properties": {
                    "years": {"type": "array", "items": {"type": "integer"}},
                    "months": {"type": "array", "items": {"type": "integer", "minimum": 1, "maximum": 13}},
                    "date_from": {"type": "string", "description": "YYYY-MM-DD"},
                    "date_to": {"type": "string", "description": "YYYY-MM-DD"},
                    "account_codes": {"type": "array", "items": {"type": "string"}},
                    "account_name_contains": {"type": "string"},
                    "category": {"type": "string"},
                    "text_contains": {"type": "string"},
                    "voucher_ids": {"type": "array", "items": {"type": "string"}},
                    "voucher_keys": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "按公司代码+会计年度+凭证编号组成的稳定凭证键精确筛选",
                    },
                    "debit_credit": {"type": "string", "enum": ["S", "H"]},
                    "currencies": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "按凭证币种筛选，如 CNY、USD；多币种金额不会直接合计",
                    },
                    "customer_contains": {"type": "string"},
                    "supplier_contains": {"type": "string"},
                    "min_absolute_amount": {"type": "number"},
                    "max_absolute_amount": {"type": "number"},
                    "group_by": {
                        "type": "string",
                        "enum": ["year", "month", "account", "category", "customer", "supplier", "debit_credit", "user", "currency"],
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                    "sample_limit": {"type": "integer", "minimum": 0, "maximum": 20},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_analysis_currency_scope",
            "description": "将财务画像、钻取、规则与模块 AI 切换到一个凭证币种独立分析；不做汇率折算。用户明确选择 CNY、USD 等币种时调用",
            "parameters": {
                "type": "object",
                "properties": {
                    "currency": {
                        "type": "string",
                        "description": "项目中存在的凭证货币代码，如 CNY 或 USD",
                    },
                },
                "required": ["currency"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_data_quality_review",
            "description": "获取按年度、原因和科目拆分的数据质量复核清单，以及既有用户决策",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_classification_decisions",
            "description": "批量应用科目归类、确认排除、暂缓或撤销决策；会使旧画像、规则命中、抽样和疑点结果失效，必须先由用户批准",
            "parameters": {
                "type": "object",
                "properties": {
                    "decisions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "account_code": {"type": "string"},
                                "account_name": {"type": "string"},
                                "decision": {"type": "string", "enum": ["map", "exclude", "defer", "reset"]},
                                "category": {"type": "string"},
                                "rationale": {"type": "string"},
                            },
                            "required": ["account_code", "decision"],
                        },
                    },
                },
                "required": ["decisions"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_evidence_inventory",
            "description": "列出当前项目已接入与缺失的证据来源，明确序时账、科目余额表、财务报表、银行流水、发票合同等覆盖边界",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_audit_case_summary",
            "description": "把疑点库按审计事项视角汇总为状态、凭证范围、来源选择器与证据缺口，避免把候选疑点直接当审计结论",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_audit_case_detail",
            "description": "读取单个审计事项的认定、证据、程序、结论、版本、就绪阻断项与完整性校验；任何推进操作前应先调用",
            "parameters": {
                "type": "object",
                "properties": {
                    "case_id": {"type": "string"},
                },
                "required": ["case_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_audit_case",
            "description": (
                "在用户批准后推进审计事项。action 支持 create_from_candidate、promote、"
                "update_case、add_assertion、update_assertion、add_evidence、update_evidence、"
                "add_procedure、update_procedure、set_conclusion、review、signoff、close。"
                "已有案件操作必须提供 get_audit_case_detail 返回的 expected_version；"
                "业务字段放入 payload，证据必须提供可回查 source_ref；形成结论、复核、"
                "签署和关闭都必须由用户明确提供真实 actor，且签署 actor 必须是独立复核人"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "create_from_candidate",
                            "promote",
                            "update_case",
                            "add_assertion",
                            "update_assertion",
                            "add_evidence",
                            "update_evidence",
                            "add_procedure",
                            "update_procedure",
                            "set_conclusion",
                            "review",
                            "signoff",
                            "close",
                        ],
                    },
                    "case_id": {"type": "string"},
                    "group_id": {"type": "string"},
                    "expected_version": {"type": "integer", "minimum": 1},
                    "actor": {
                        "type": "string",
                        "description": "真实执行人；签署时必须由用户明确提供独立复核人",
                    },
                    "payload": {
                        "type": "object",
                        "description": "对应服务字段；例如 assertion_id/evidence_id/procedure_id、source_ref、status、summary、note",
                    },
                },
                "required": ["action"],
            },
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
                        "enum": ["income", "expense", "other_pnl", "cost_variance", "working_capital", "balance_sheet", "adjustment", "profile", "cross"],
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
            "description": "按版本化抽样计划，从完整总体或风险信号总体生成可重放底稿；不得把风险定向样本表述为统计代表性样本",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_name": {"type": "string"},
                    "population_scope": {
                        "type": "string",
                        "enum": ["full_population", "risk_signals"],
                    },
                    "strategy": {
                        "type": "string",
                        "enum": ["risk_directed", "random", "monetary_unit", "stratified", "unpredictable"],
                    },
                    "method": {
                        "type": "string",
                        "enum": ["by_rule", "random", "monetary_unit", "stratified"],
                    },
                    "size": {"type": "integer", "minimum": 1, "maximum": 500},
                    "seed": {"type": "integer"},
                    "years": {"type": "array", "items": {"type": "integer"}},
                    "coverage_constraints": {
                        "type": "object",
                        "properties": {
                            "min_per_month": {"type": "integer", "minimum": 1},
                            "min_per_account_category": {"type": "integer", "minimum": 1},
                            "max_same_risk_signal_ratio": {
                                "type": "number",
                                "exclusiveMinimum": 0,
                                "maximum": 1,
                            },
                        },
                    },
                    "stratify_by": {
                        "type": "string",
                        "enum": ["account_category", "month", "voucher_type"],
                    },
                    "stratify_mode": {
                        "type": "string",
                        "enum": ["proportional", "equal"],
                    },
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
        data_quality = {}
        fin_error = None
        for year in manifest.years:
            raw_work = store.get_work_df(project_id, year)
            work = store.get_analysis_work_df(project_id, year)
            if raw_work.empty:
                continue
            data_quality[year] = analysis_quality_summary(raw_work)
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
            "data_quality": data_quality,
            "provenance": {
                "project_id": project_id,
                "data_version": store.current_data_version(project_id),
                "classification_revision": store.current_classification_revision(project_id),
                "analysis_currency": store.current_analysis_currency(project_id),
                "years": manifest.years,
            },
        }

    if name == "query_journal":
        return run_journal_query(store, project_id, arguments)

    if name == "set_analysis_currency_scope":
        currency = str(arguments.get("currency") or "").strip()
        if not currency:
            return {"error": "currency 不能为空"}
        try:
            result = store.set_analysis_currency(project_id, currency, actor="agent")
        except ValueError as exc:
            return {"error": str(exc)}
        return attach_ui_actions(
            {"ok": True, **result},
            [
                {"type": "navigate_main", "tab": "finance"},
                {"type": "invalidate_project_analysis", "project_id": project_id},
            ],
        )

    if name == "get_data_quality_review":
        decisions = state.get("account_classification_decisions") or {}
        years: dict[str, Any] = {}
        for year in manifest.years:
            summary = analysis_quality_summary(
                store.get_work_df(project_id, year),
                classification_decisions=decisions if isinstance(decisions, dict) else {},
            )
            summary["review_accounts"] = list(summary.get("review_accounts") or [])[:20]
            years[str(year)] = summary
        return {
            "data_version": store.current_data_version(project_id),
            "classification_revision": store.current_classification_revision(project_id),
            "allowed_categories": [
                category for category in ALL_CATEGORIES if category != CAT_UNCATEGORIZED
            ],
            "classification_decisions": list(decisions.values()) if isinstance(decisions, dict) else [],
            "years": years,
            "provenance": {
                "denominator": "序时账逐行统一金额绝对值之和",
                "review_required": "待补充分类 + 缺少科目身份 + 用户暂缓决策",
                "excluded": "系统口径排除 + 用户确认排除",
                "currency_safety": (
                    "若 mixed_document_currency=true，跨币种金额及其占比不可直接比较；"
                    "应读取 currency_distribution 或先切换单币种分析口径"
                ),
            },
        }

    if name == "apply_classification_decisions":
        decisions = arguments.get("decisions")
        if not isinstance(decisions, list) or not decisions:
            return {"error": "decisions 须为非空数组"}
        try:
            result = store.apply_account_classification_decisions(
                project_id,
                [dict(item) for item in decisions if isinstance(item, dict)],
                actor="agent-approved",
            )
        except ValueError as exc:
            return {"error": str(exc)}
        return attach_ui_actions(
            {"ok": True, **result},
            [
                {"type": "navigate_main", "tab": "finance"},
                {"type": "invalidate_project_analysis", "project_id": project_id},
            ],
        )

    if name == "get_evidence_inventory":
        source_types = {source.type for source in manifest.sources}
        expected = [
            ("journal", "序时账", True),
            ("trial_balance", "科目余额表", False),
            ("financial_statement", "财务报表", False),
            ("bank_statement", "银行流水/对账单", False),
            ("invoice", "发票", False),
            ("contract", "合同及订单", False),
        ]
        inventory = [
            {
                "source_type": source_type,
                "label": label,
                "status": "connected" if source_type in source_types else "missing",
                "required_for_current_charts": required,
            }
            for source_type, label, required in expected
        ]
        return {
            "sources": [source.to_dict() for source in manifest.sources],
            "inventory": inventory,
            "current_answer_boundary": (
                "当前可核验回答基于序时账及其派生画像；未接入来源不能作为已取得的审计证据。"
            ),
            "data_version": store.current_data_version(project_id),
        }

    if name == "get_audit_case_summary":
        pool = store.load_candidate_pool(project_id)
        case_result = AuditCaseService(store).list_cases(project_id)
        readiness_by_id = case_result.get("readiness") or {}
        cases = [
            {
                "case_id": case.get("case_id"),
                "title": case.get("title"),
                "kind": case.get("kind"),
                "status": case.get("status"),
                "risk_summary": case.get("risk_summary") or case.get("risk_domain"),
                "materiality": case.get("materiality", "unassessed"),
                "assertion_count": len(case.get("assertions") or []),
                "active_evidence_count": sum(
                    1
                    for evidence in case.get("evidence_refs") or []
                    if evidence.get("status") in {"active", "verified"}
                ),
                "stale_evidence_count": sum(
                    1
                    for evidence in case.get("evidence_refs") or []
                    if evidence.get("status") == "stale"
                ),
                "procedure_count": len(case.get("procedures") or []),
                "has_conclusion": bool(case.get("conclusion")),
                "source_candidate_group_id": case.get("source_candidate_group_id"),
                "version_scope": case.get("version_scope") or {},
                "version": case.get("version"),
                "readiness": readiness_by_id.get(str(case.get("case_id") or "")) or {},
            }
            for case in case_result.get("cases", [])[:30]
        ]
        return {
            "case_count": case_result.get("count", len(cases)),
            "candidate_proposal_count": len(pool),
            "candidate_stats": pool_stats(pool),
            "cases": cases,
            "current_scope": case_result.get("current_scope") or {},
            "evidence_boundary": (
                "候选疑点不是审计结论；仅已写入案件、绑定来源并完成程序/复核的事项可形成结论。"
            ),
            "next_step": (
                "尚无正式案件，可从疑点创建案件草稿。"
                if not cases and pool
                else "检查过期证据、未完成程序和待复核结论。"
            ),
        }

    if name == "get_audit_case_detail":
        case_id = str(arguments.get("case_id") or "").strip()
        if not case_id:
            return {"error": "case_id 不能为空"}
        try:
            detail = AuditCaseService(store).get_case(project_id, case_id)
        except (KeyError, ValueError) as exc:
            return {"error": str(exc.args[0] if isinstance(exc, KeyError) else exc)}
        return {
            **detail,
            "events": AuditCaseService(store).list_events(project_id, case_id)[-30:],
            "boundary": "完整性校验用于发现本地状态不一致；它不替代外部不可篡改存证。",
        }

    if name == "manage_audit_case":
        action = str(arguments.get("action") or "").strip()
        case_id = str(arguments.get("case_id") or "").strip()
        group_id = str(arguments.get("group_id") or "").strip()
        payload = arguments.get("payload") or {}
        if not isinstance(payload, dict):
            return {"error": "payload 必须是对象"}
        expected_version = arguments.get("expected_version")
        expected = int(expected_version) if expected_version is not None else None
        explicit_actor = str(arguments.get("actor") or "").strip()
        actor = explicit_actor or "agent-preparer"
        if action in {"set_conclusion", "review", "signoff", "close"} and not explicit_actor:
            return {
                "error": (
                    f"{action} 必须由用户明确提供 actor（真实姓名或工号）；"
                    "Agent 不能代填结论编制人、复核人或归档责任人"
                )
            }
        service = AuditCaseService(store)
        try:
            if action == "create_from_candidate":
                if not group_id:
                    return {"error": "create_from_candidate 需要 group_id"}
                result = service.create_from_candidate(
                    project_id,
                    group_id,
                    formal=bool(payload.get("formal")),
                    title=str(payload.get("title") or ""),
                    risk_statement=str(payload.get("risk_statement") or ""),
                    financial_statement_assertions=list(
                        payload.get("financial_statement_assertions") or []
                    ),
                    materiality=str(payload.get("materiality") or "unassessed"),
                    owner=str(payload.get("owner") or ""),
                    actor=actor,
                    expected_version=expected,
                )
            else:
                if not case_id:
                    return {"error": f"{action or '该操作'} 需要 case_id"}
                if expected is None:
                    return {
                        "error": (
                            "已有案件操作必须提供 expected_version；"
                            "请先调用 get_audit_case_detail 获取当前版本"
                        )
                    }
                if action == "promote":
                    result = service.promote_case(
                        project_id,
                        case_id,
                        actor=actor,
                        comment=str(payload.get("comment") or ""),
                        expected_version=expected,
                    )
                elif action == "update_case":
                    result = service.update_case(
                        project_id,
                        case_id,
                        title=payload.get("title"),
                        risk_summary=payload.get("risk_summary"),
                        materiality=payload.get("materiality"),
                        owner=payload.get("owner"),
                        status=payload.get("status"),
                        actor=actor,
                        note=str(payload.get("note") or ""),
                        expected_version=expected,
                    )
                elif action == "add_assertion":
                    result = service.add_assertion(
                        project_id,
                        case_id,
                        title=str(payload.get("title") or ""),
                        risk_statement=str(payload.get("risk_statement") or ""),
                        financial_statement_assertions=list(
                            payload.get("financial_statement_assertions") or []
                        ),
                        affected_accounts=list(payload.get("affected_accounts") or []),
                        actor=actor,
                        expected_version=expected,
                    )
                elif action == "update_assertion":
                    result = service.update_assertion(
                        project_id,
                        case_id,
                        str(payload.get("assertion_id") or ""),
                        status=payload.get("status"),
                        title=payload.get("title"),
                        risk_statement=payload.get("risk_statement"),
                        actor=actor,
                        expected_version=expected,
                    )
                elif action == "add_evidence":
                    result = service.add_evidence(
                        project_id,
                        case_id,
                        source_type=str(payload.get("source_type") or ""),
                        source_ref=dict(payload.get("source_ref") or {}),
                        description=str(payload.get("description") or ""),
                        status=str(payload.get("status") or "active"),
                        assertion_ids=payload.get("assertion_ids"),
                        procedure_id=str(payload.get("procedure_id") or ""),
                        actor=actor,
                        expected_version=expected,
                    )
                elif action == "update_evidence":
                    result = service.update_evidence(
                        project_id,
                        case_id,
                        str(payload.get("evidence_id") or ""),
                        status=payload.get("status"),
                        description=payload.get("description"),
                        source_ref=payload.get("source_ref"),
                        assertion_ids=payload.get("assertion_ids"),
                        procedure_id=payload.get("procedure_id"),
                        actor=actor,
                        expected_version=expected,
                    )
                elif action == "add_procedure":
                    result = service.add_procedure(
                        project_id,
                        case_id,
                        title=str(payload.get("title") or ""),
                        description=str(payload.get("description") or ""),
                        assertion_ids=payload.get("assertion_ids"),
                        status=str(payload.get("status") or "planned"),
                        result=str(payload.get("result") or ""),
                        performed_by=str(payload.get("performed_by") or ""),
                        performed_at=str(payload.get("performed_at") or ""),
                        actor=actor,
                        expected_version=expected,
                    )
                elif action == "update_procedure":
                    result = service.update_procedure(
                        project_id,
                        case_id,
                        str(payload.get("procedure_id") or ""),
                        title=payload.get("title"),
                        description=payload.get("description"),
                        assertion_ids=payload.get("assertion_ids"),
                        status=payload.get("status"),
                        result=payload.get("result"),
                        performed_by=payload.get("performed_by"),
                        performed_at=payload.get("performed_at"),
                        actor=actor,
                        expected_version=expected,
                    )
                elif action == "set_conclusion":
                    result = service.set_conclusion(
                        project_id,
                        case_id,
                        outcome=str(payload.get("outcome") or ""),
                        summary=str(payload.get("summary") or ""),
                        basis_evidence_ids=payload.get("basis_evidence_ids"),
                        procedure_ids=payload.get("procedure_ids"),
                        unresolved_assertion_ids=payload.get(
                            "unresolved_assertion_ids"
                        ),
                        override_reason=str(payload.get("override_reason") or ""),
                        misstatement_amount=payload.get("misstatement_amount"),
                        currency=str(payload.get("currency") or ""),
                        actor=actor,
                        expected_version=expected,
                    )
                elif action in {"review", "signoff"}:
                    result = service.record_review(
                        project_id,
                        case_id,
                        action=action,
                        actor=actor,
                        note=str(payload.get("note") or ""),
                        expected_version=expected,
                    )
                elif action == "close":
                    result = service.update_case(
                        project_id,
                        case_id,
                        status="closed",
                        actor=actor,
                        note=str(payload.get("note") or ""),
                        expected_version=expected,
                    )
                else:
                    return {"error": f"不支持的审计事项操作：{action}"}
        except (KeyError, ValueError) as exc:
            return {"error": str(exc.args[0] if isinstance(exc, KeyError) else exc)}
        return attach_ui_actions(
            {"ok": True, "action": action, **result},
            _cases_nav_actions(project_id),
        )

    if name == "query_drilldown":
        selector = arguments.get("selector") or {}
        year = selector.get("year")
        if year is None:
            return {"error": "selector 缺少 year"}
        limit = min(int(arguments.get("limit") or 30), 50)
        work = store.get_analysis_work_df(project_id, int(year))
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
        work = store.get_analysis_work_df(project_id, int(year))
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
        return {
            "tools": caps,
            "analysis_modules": ANALYSIS_MODULES,
            "cross_year_detectors": CROSS_YEAR_DETECTORS,
            "evidence_boundary": {
                "available": ["序时账", "序时账派生财务画像", "规则命中", "疑点库", "抽样结果"],
                "not_connected": [
                    "ERP 科目主数据与系统配置",
                    "科目余额表",
                    "财务报表",
                    "银行流水",
                    "合同",
                    "发票",
                ],
                "rule": "未接入来源不能被描述为已取得或已核验的审计证据",
            },
            "execution_contract": {
                "truth_source": "tool_calls.result 与后台 job_id/status",
                "rule": "没有真实工具调用或任务编号时，不得声称任务正在执行或已完成",
            },
        }

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
        plan = {
            key: arguments[key]
            for key in (
                "plan_name",
                "population_scope",
                "strategy",
                "method",
                "size",
                "seed",
                "years",
                "coverage_constraints",
                "stratify_by",
                "stratify_mode",
            )
            if key in arguments and arguments[key] is not None
        }
        pipeline = AnalysisPipeline(store)
        try:
            out = pipeline.extract_samples(
                project_id,
                method=method,
                size=size_int,
                seed=int(arguments.get("seed") or 42),
                plan=plan,
            )
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
