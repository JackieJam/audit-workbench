"""Analysis API — 财务画像聚合数据。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
from audit_engine.analysis.drilldown import customer_revenue_entries, monthly_income_cost_entries
from audit_engine.analysis.income_cost import (
    customer_revenue_top,
    income_cost_categories,
    monthly_revenue_cost,
)
from audit_engine.store import ProjectStore
from fastapi import APIRouter, Depends, HTTPException, Query

from audit_api.deps import get_store

router = APIRouter(prefix="/projects", tags=["analysis"])

_REPO_ROOT = Path(__file__).resolve().parents[4]
_AUDIT_QUESTIONS_PATH = _REPO_ROOT / "config" / "audit_questions.json"


def _df_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df.empty:
        return []
    out = df.copy()
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            out[col] = out[col].dt.strftime("%Y-%m-%d")
        elif pd.api.types.is_float_dtype(out[col]):
            out[col] = out[col].round(2)
    return json.loads(out.to_json(orient="records", force_ascii=False))


def _load_questions(module: str) -> list[dict[str, str]]:
    if not _AUDIT_QUESTIONS_PATH.exists():
        return []
    data = json.loads(_AUDIT_QUESTIONS_PATH.read_text(encoding="utf-8"))
    block = data.get(module, {})
    return block.get("questions", [])


def _require_year(manifest_years: list[int], year: int) -> None:
    if year not in manifest_years:
        raise HTTPException(status_code=404, detail=f"项目中无 {year} 年序时账数据")


@router.get("/{project_id}/analysis/modules/{module_key}/questions")
def module_audit_questions(project_id: str, module_key: str, store: ProjectStore = Depends(get_store)) -> dict:
    try:
        store.load_manifest(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    questions = _load_questions(module_key)
    return {"module": module_key, "questions": questions}


@router.get("/{project_id}/analysis/income-cost/categories")
def income_cost_category_list(
    project_id: str,
    year: int = Query(...),
    store: ProjectStore = Depends(get_store),
) -> dict:
    manifest = _manifest_or_404(store, project_id)
    _require_year(manifest.years, year)
    work = store.get_work_df(project_id, year)
    return {"year": year, "categories": income_cost_categories(work)}


@router.get("/{project_id}/analysis/income-cost/monthly")
def income_cost_monthly(
    project_id: str,
    year: int = Query(...),
    category: str = Query("总计"),
    store: ProjectStore = Depends(get_store),
) -> dict:
    manifest = _manifest_or_404(store, project_id)
    _require_year(manifest.years, year)
    work = store.get_work_df(project_id, year)
    df = monthly_revenue_cost(work, category=category)
    return {"year": year, "category": category, "rows": _df_records(df)}


@router.get("/{project_id}/analysis/income-cost/drilldown/monthly")
def income_cost_drilldown_monthly(
    project_id: str,
    year: int = Query(...),
    month: int = Query(..., ge=1, le=12),
    metric: str = Query(..., pattern="^(revenue|cost|gross)$"),
    category: str = Query("总计"),
    limit: int = Query(200, ge=1, le=2000),
    store: ProjectStore = Depends(get_store),
) -> dict:
    manifest = _manifest_or_404(store, project_id)
    _require_year(manifest.years, year)
    work = store.get_work_df(project_id, year)
    df = monthly_income_cost_entries(work, month=month, metric=metric, category=category, top_n=limit)
    metric_labels = {"revenue": "净收入", "cost": "净成本", "gross": "毛利"}
    return {
        "year": year,
        "month": month,
        "metric": metric,
        "metric_label": metric_labels.get(metric, metric),
        "category": category,
        "row_count": len(df),
        "rows": _df_records(df),
    }


@router.get("/{project_id}/analysis/income-cost/drilldown/customer")
def income_cost_drilldown_customer(
    project_id: str,
    year: int = Query(...),
    customer: str = Query(...),
    category: str = Query("总计"),
    limit: int = Query(200, ge=1, le=2000),
    store: ProjectStore = Depends(get_store),
) -> dict:
    manifest = _manifest_or_404(store, project_id)
    _require_year(manifest.years, year)
    work = store.get_work_df(project_id, year)
    df = customer_revenue_entries(work, customer=customer, category=category, top_n=limit)
    return {
        "year": year,
        "customer": customer,
        "category": category,
        "row_count": len(df),
        "rows": _df_records(df),
    }


@router.get("/{project_id}/analysis/income-cost/customers")
def income_cost_customers(
    project_id: str,
    year: int = Query(...),
    category: str = Query("总计"),
    top_n: int = Query(10, ge=1, le=50),
    store: ProjectStore = Depends(get_store),
) -> dict:
    manifest = _manifest_or_404(store, project_id)
    _require_year(manifest.years, year)
    work = store.get_work_df(project_id, year)
    df = customer_revenue_top(work, category=category, top_n=top_n)
    return {"year": year, "category": category, "rows": _df_records(df)}


def _manifest_or_404(store: ProjectStore, project_id: str):
    try:
        return store.load_manifest(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
