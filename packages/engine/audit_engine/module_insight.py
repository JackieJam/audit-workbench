"""模块级 LLM 风险分析 — 基于聚合数据 + 风险关注点生成报告与抽样建议。"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

import pandas as pd

from audit_engine.analysis.adjustment import adjustment_summary
from audit_engine.analysis.balance_sheet import (
    balance_sheet_categories,
    category_account_breakdown,
    category_monthly_movement,
)
from audit_engine.analysis.drilldown import resolve_drilldown
from audit_engine.analysis.expense import cross_year_expense_table
from audit_engine.analysis.income_cost import customer_revenue_top, monthly_revenue_cost
from audit_engine.analysis.other_pnl import monthly_other_pnl
from audit_engine.analysis.working_capital import (
    ap_accrual_monthly,
    other_payable_monthly,
    other_receivable_monthly,
)
from audit_engine.candidate_pool import add_candidate_group, build_candidate_group
from audit_engine.json_utils import parse_json_dict
from audit_engine.llm_client import make_openai_client
from audit_engine.module_insight_jobs import (
    fail_job,
    finish_job,
    make_progress_reporter,
    set_job_stage,
)
from audit_engine.profiler import (
    build_financial_summary,
    build_profile,
    financials_to_summary_text,
    profiles_to_summary_text,
)
from audit_engine.store import ProjectStore

SYSTEM_PROMPT = (
    "你是一名企业内部审计经理，基于序时账聚合指标做初步风险分析。"
    "结论必须基于输入数据；抽样建议 condition 字段值须使用输入原文。仅输出 JSON。"
)

OUTPUT_SCHEMA = {
    "executive_summary": "3-5句话模块风险概述",
    "findings": [
        {
            "severity": "高/中/低",
            "observation": "现象",
            "audit_meaning": "审计含义",
            "suggested_procedure": "建议核查动作",
        }
    ],
    "recommendations": [
        {
            "title": "建议标题",
            "risk_level": "高/中/低",
            "reason": "纳入疑点库理由",
            "audit_procedure": "核查程序",
            "condition": {"kind": "expense_category", "year": 2024, "expense_category": "人工"},
        }
    ],
}

MODULE_GUIDES = {
    "收入成本": "kind: monthly_income_cost/customer_revenue；需 year、month、metric、category 或 customer",
    "费用": "kind=expense_category；需 year、expense_category",
    "营业外与投资收益": "kind=other_pnl_month；需 year、month、metric",
    "暂估往来": "kind: ap_accrual_month/other_receivable_month/other_payable_month；需 year、month、direction",
    "资产负债": "kind: bs_category_month/bs_category_account",
    "调账冲销": "kind=adjustment_voucher；需 year、voucher_id",
}


def resolve_api_key(header_key: str | None = None) -> str:
    for src in (
        header_key,
        os.environ.get("DEEPSEEK_API_KEY"),
        os.environ.get("OPENAI_API_KEY"),
        os.environ.get("LLM_API_KEY"),
    ):
        if src and str(src).strip():
            return str(src).strip()
    return ""


def _records(df: pd.DataFrame, limit: int = 40) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    out = df.head(limit).copy()
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            out[col] = out[col].dt.strftime("%Y-%m-%d")
    return json.loads(out.to_json(orient="records", force_ascii=False))


def build_module_payload(
    store: ProjectStore,
    project_id: str,
    module_key: str,
    *,
    risk_questions: list[dict[str, str]],
) -> dict[str, Any]:
    manifest = store.load_manifest(project_id)
    work_by_year = {y: store.get_analysis_work_df(project_id, y) for y in manifest.years}
    financials = {y: build_financial_summary(df, y) for y, df in work_by_year.items() if not df.empty}
    profiles = {y: build_profile(df, y) for y, df in work_by_year.items() if not df.empty}

    payload: dict[str, Any] = {
        "module": module_key,
        "years": manifest.years,
        "analysis_currency": store.current_analysis_currency(project_id),
        "risk_focus": [q.get("text", "") for q in risk_questions if q.get("text")],
        "financial_summary": financials_to_summary_text(financials),
        "profile_summary": profiles_to_summary_text(profiles),
    }

    if module_key == "费用":
        payload["cross_year_expense"] = _records(cross_year_expense_table(financials), 80)
    elif module_key == "营业外与投资收益":
        payload["yearly_other_pnl"] = [
            {"year": year, "monthly": _records(monthly_other_pnl(work), 13)}
            for year, work in work_by_year.items()
            if not work.empty
        ]
    elif module_key == "收入成本":
        payload["yearly_income_cost"] = [
            {
                "year": year,
                "monthly": _records(monthly_revenue_cost(work, category="总计"), 12),
                "customer_top": _records(customer_revenue_top(work, category="总计", top_n=10), 10),
            }
            for year, work in work_by_year.items()
            if not work.empty
        ]
    elif module_key == "暂估往来":
        payload["working_capital"] = [
            {
                "year": year,
                "ap_accrual": _records(ap_accrual_monthly(work), 12),
                "other_receivable": _records(other_receivable_monthly(work), 12),
                "other_payable": _records(other_payable_monthly(work), 12),
            }
            for year, work in work_by_year.items()
            if not work.empty
        ]
    elif module_key == "资产负债":
        payload["balance_sheet"] = [
            {
                "year": year,
                "categories": [
                    {
                        "category": category,
                        "monthly": _records(category_monthly_movement(work, category), 13),
                        "top_accounts": _records(
                            category_account_breakdown(work, category, top_n=10),
                            10,
                        ),
                    }
                    for category in balance_sheet_categories(work)
                ],
            }
            for year, work in work_by_year.items()
            if not work.empty
        ]
    elif module_key == "调账冲销":
        payload["adjustment_summaries"] = {
            str(y): _records(adjustment_summary(work), 20)
            for y, work in work_by_year.items()
            if not work.empty
        }

    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    payload["signature"] = hashlib.sha1(raw.encode()).hexdigest()[:16]
    return payload


def generate_module_insight(
    module_key: str,
    payload: dict[str, Any],
    *,
    api_key: str,
    model: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    if not api_key:
        raise ValueError("缺少 LLM API Key")

    model = model or os.environ.get("LLM_MODEL", "deepseek-chat")
    base_url = base_url or os.environ.get("LLM_BASE_URL", "https://api.deepseek.com")

    user_prompt = (
        f"## 模块\n{module_key}\n\n"
        f"## 风险关注点\n{json.dumps(payload.get('risk_focus', []), ensure_ascii=False)}\n\n"
        f"## 聚合数据\n{json.dumps({k: v for k, v in payload.items() if k != 'signature'}, ensure_ascii=False, indent=2, default=str)}\n\n"
        f"## 输出格式\n{json.dumps(OUTPUT_SCHEMA, ensure_ascii=False, indent=2)}\n\n"
        f"## condition 约束\n{MODULE_GUIDES.get(module_key, '')}\n"
    )

    client = make_openai_client(api_key=api_key, base_url=base_url, max_retries=2, timeout=90.0)
    resp = client.chat.completions.create(
        model=model,
        max_tokens=2400,
        temperature=0.1,
        timeout=90.0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    )
    parsed = parse_json_dict(resp.choices[0].message.content or "{}")
    parsed["module"] = module_key
    parsed["input_signature"] = payload.get("signature", "")
    return parsed


def condition_to_selector(condition: dict[str, Any]) -> dict[str, Any]:
    c = dict(condition or {})
    kind = str(c.get("kind", "")).strip()
    year = int(c["year"])

    mapping = {
        "expense_category": lambda: {
            "kind": "expense_category",
            "year": year,
            "expense_category": str(c.get("expense_category", "")),
        },
        "monthly_income_cost": lambda: {
            "kind": "monthly_income_cost",
            "year": year,
            "month": int(c.get("month", 1)),
            "metric": str(c.get("metric", "revenue")),
            "category": str(c.get("category", "总计")),
        },
        "customer_revenue": lambda: {
            "kind": "customer_revenue",
            "year": year,
            "customer": str(c.get("customer", "")),
            "category": str(c.get("category", "总计")),
        },
        "ap_accrual_month": lambda: {
            "kind": "ap_accrual_month",
            "year": year,
            "month": int(c.get("month", 1)),
            "direction": str(c.get("direction", "net")),
        },
        "other_receivable_month": lambda: {
            "kind": "other_receivable_month",
            "year": year,
            "month": int(c.get("month", 1)),
            "direction": str(c.get("direction", "net")),
        },
        "other_payable_month": lambda: {
            "kind": "other_payable_month",
            "year": year,
            "month": int(c.get("month", 1)),
            "direction": str(c.get("direction", "net")),
        },
        "adjustment_voucher": lambda: {
            "kind": "adjustment_voucher",
            "year": year,
            "voucher_id": str(c.get("voucher_id", "")),
            "date": str(c.get("date", "")),
        },
        "other_pnl_month": lambda: {
            "kind": "other_pnl_month",
            "year": year,
            "month": int(c.get("month", 1)),
            "metric": str(c.get("metric", "investment_income")),
        },
        "bs_category_month": lambda: {
            "kind": "bs_category_month",
            "year": year,
            "category": str(c.get("category", "")),
            "month": int(c.get("month", 1)),
            "direction": str(c.get("direction", "net")),
        },
        "bs_category_account": lambda: {
            "kind": "bs_category_account",
            "year": year,
            "category": str(c.get("category", "")),
            "account": str(c.get("account", "")),
        },
    }
    if kind not in mapping:
        raise ValueError(f"不支持的 condition.kind: {kind}")
    return mapping[kind]()


def apply_recommendations(
    store: ProjectStore,
    project_id: str,
    recommendations: list[dict[str, Any]],
    *,
    module_key: str,
    indices: list[int] | None = None,
) -> dict[str, Any]:
    from audit_engine.routine_filter import prepare_candidate_detail
    from audit_engine.rules_config import default_rules_config, merge_rules_config

    pool = store.load_candidate_pool(project_id)
    rules_cfg = default_rules_config()
    state = store.load_state(project_id)
    if isinstance(state.get("rules_config"), dict):
        rules_cfg = merge_rules_config(rules_cfg, state.get("rules_config"))

    added = skipped = 0
    errors: list[str] = []
    selected = indices if indices is not None else list(range(len(recommendations)))

    for idx in selected:
        if idx < 0 or idx >= len(recommendations):
            continue
        rec = recommendations[idx]
        if not isinstance(rec, dict):
            skipped += 1
            continue
        try:
            selector = condition_to_selector(rec.get("condition") or {})
            work = store.get_analysis_work_df(project_id, int(selector["year"]))
            detail = resolve_drilldown(work, selector)
            if detail.empty:
                skipped += 1
                errors.append(f"{rec.get('title', idx)}: 无匹配分录")
                continue
            detail = prepare_candidate_detail(detail, rules_cfg, selector=selector)
            if detail.empty:
                skipped += 1
                errors.append(f"{rec.get('title', idx)}: 过滤常规分录后无剩余样本")
                continue
            group = build_candidate_group(
                title=str(rec.get("title") or f"{module_key} 抽样建议"),
                source_module=str(rec.get("source_module") or module_key),
                source_view=str(rec.get("source_view") or "AI抽样建议"),
                detail=detail,
                tags=[str(t) for t in (rec.get("tags") or []) if str(t).strip()] or ["AI建议"],
                reason=str(rec.get("reason") or rec.get("audit_procedure") or ""),
                selector=selector,
                created_by="llm_insight",
            )
            pool = add_candidate_group(pool, group)
            added += 1
        except Exception as exc:
            skipped += 1
            errors.append(f"{rec.get('title', idx)}: {exc}")

    store.save_candidate_pool(project_id, pool)
    return {"added": added, "skipped": skipped, "errors": errors}


def run_module_insight_pipeline(
    store: ProjectStore,
    project_id: str,
    module_key: str,
    *,
    risk_questions: list[dict[str, str]],
    api_key: str,
    model: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    """带阶段上报的完整模块分析流程。"""
    report = make_progress_reporter(store, project_id, module_key)
    try:
        set_job_stage(store, project_id, module_key, "load_data", status="running")
        report("aggregate")
        payload = build_module_payload(store, project_id, module_key, risk_questions=risk_questions)
        report("prompt")
        report("llm")
        insight = generate_module_insight(
            module_key,
            payload,
            api_key=api_key,
            model=model,
            base_url=base_url,
        )
        report("parse")
        report("save")

        def save_insight(state: dict[str, Any]) -> None:
            module_insights = dict(state.get("module_insights") or {})
            module_insights[module_key] = insight
            state["module_insights"] = module_insights

        store.update_state(project_id, save_insight)
        finish_job(store, project_id, module_key)
        return insight
    except Exception as exc:
        fail_job(store, project_id, module_key, str(exc))
        raise
