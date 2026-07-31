"""Analysis API — 费用 / 暂估 / 资产负债 / 调账。"""

from __future__ import annotations

from typing import Any

import pandas as pd
from audit_engine.analysis.adjustment import adjustment_summary, adjustment_voucher_entries
from audit_engine.analysis.balance_sheet import (
    balance_sheet_categories,
    category_account_breakdown,
    category_monthly_movement,
)
from audit_engine.analysis.cost_variance import cost_variance_summary, monthly_cost_variance
from audit_engine.analysis.drilldown import resolve_drilldown
from audit_engine.analysis.expense import build_expense_financials, cross_year_expense_table, expense_category_entries
from audit_engine.analysis.other_pnl import monthly_other_pnl
from audit_engine.analysis.working_capital import (
    ap_accrual_monthly,
    ap_accrual_suppliers,
    other_payable_monthly,
    other_receivable_monthly,
)
from audit_engine.store import ProjectStore
from fastapi import APIRouter, Depends, HTTPException, Query

from audit_api.deps import get_store
from audit_api.routers.analysis import (
    _analysis_work_or_409,
    _df_records,
    _manifest_or_404,
    _require_year,
)

router = APIRouter(prefix="/projects", tags=["analysis-modules"])


def _work(store: ProjectStore, project_id: str, year: int) -> pd.DataFrame:
    return _analysis_work_or_409(store, project_id, year)


# ── 费用 ──

@router.get("/{project_id}/analysis/expense/cross-year")
def expense_cross_year(project_id: str, store: ProjectStore = Depends(get_store)) -> dict:
    manifest = _manifest_or_404(store, project_id)
    if not manifest.years:
        raise HTTPException(status_code=400, detail="项目无序时账数据")
    work_map = {y: _work(store, project_id, y) for y in manifest.years}
    financials = build_expense_financials(work_map)
    df = cross_year_expense_table(financials)
    return {"years": manifest.years, "rows": _df_records(df)}


@router.get("/{project_id}/analysis/expense/drilldown")
def expense_drilldown(
    project_id: str,
    year: int = Query(...),
    category: str = Query(...),
    limit: int = Query(200, ge=1, le=2000),
    store: ProjectStore = Depends(get_store),
) -> dict:
    manifest = _manifest_or_404(store, project_id)
    _require_year(manifest.years, year)
    work = _work(store, project_id, year)
    df = expense_category_entries(work, category, top_n=limit)
    return {"year": year, "category": category, "row_count": len(df), "rows": _df_records(df)}


# ── 暂估往来 ──


@router.get("/{project_id}/analysis/other-pnl/monthly")
def other_pnl_monthly(
    project_id: str,
    year: int = Query(...),
    store: ProjectStore = Depends(get_store),
) -> dict:
    manifest = _manifest_or_404(store, project_id)
    _require_year(manifest.years, year)
    df = monthly_other_pnl(_work(store, project_id, year))
    return {"year": year, "rows": _df_records(df)}


@router.get("/{project_id}/analysis/cost-variance/monthly")
def cost_variance_monthly(
    project_id: str,
    year: int = Query(...),
    store: ProjectStore = Depends(get_store),
) -> dict:
    manifest = _manifest_or_404(store, project_id)
    _require_year(manifest.years, year)
    work = _work(store, project_id, year)
    return {
        "year": year,
        "summary": cost_variance_summary(work),
        "rows": _df_records(monthly_cost_variance(work)),
    }


# ── 暂估往来 ──

@router.get("/{project_id}/analysis/working-capital/ap-accrual/monthly")
def wc_ap_monthly(project_id: str, year: int = Query(...), store: ProjectStore = Depends(get_store)) -> dict:
    manifest = _manifest_or_404(store, project_id)
    _require_year(manifest.years, year)
    df = ap_accrual_monthly(_work(store, project_id, year))
    return {"year": year, "rows": _df_records(df)}


@router.get("/{project_id}/analysis/working-capital/ap-accrual/suppliers")
def wc_ap_suppliers(
    project_id: str,
    year: int = Query(...),
    month: int = Query(..., ge=1, le=13),
    top_n: int = Query(10, ge=1, le=50),
    store: ProjectStore = Depends(get_store),
) -> dict:
    manifest = _manifest_or_404(store, project_id)
    _require_year(manifest.years, year)
    df = ap_accrual_suppliers(_work(store, project_id, year), month, top_n=top_n)
    return {"year": year, "month": month, "rows": _df_records(df)}


@router.get("/{project_id}/analysis/working-capital/other-receivable/monthly")
def wc_or_monthly(project_id: str, year: int = Query(...), store: ProjectStore = Depends(get_store)) -> dict:
    manifest = _manifest_or_404(store, project_id)
    _require_year(manifest.years, year)
    df = other_receivable_monthly(_work(store, project_id, year))
    return {"year": year, "rows": _df_records(df)}


@router.get("/{project_id}/analysis/working-capital/other-payable/monthly")
def wc_op_monthly(project_id: str, year: int = Query(...), store: ProjectStore = Depends(get_store)) -> dict:
    manifest = _manifest_or_404(store, project_id)
    _require_year(manifest.years, year)
    df = other_payable_monthly(_work(store, project_id, year))
    return {"year": year, "rows": _df_records(df)}


@router.post("/{project_id}/analysis/drilldown")
def generic_drilldown(
    project_id: str,
    body: dict[str, Any],
    limit: int = Query(200, ge=1, le=2000),
    store: ProjectStore = Depends(get_store),
) -> dict:
    """通用钻取 — body 为 selector（含 kind/year 等）。"""
    manifest = _manifest_or_404(store, project_id)
    selector = body.get("selector") or body
    year = selector.get("year")
    if year is None:
        raise HTTPException(status_code=400, detail="selector 缺少 year")
    _require_year(manifest.years, int(year))
    work = _work(store, project_id, int(year))
    df = resolve_drilldown(work, selector, limit=limit)
    if df.empty:
        return {"row_count": 0, "rows": [], "selector": selector}
    return {"row_count": len(df), "rows": _df_records(df), "selector": selector}


# ── 资产负债 ──

@router.get("/{project_id}/analysis/balance-sheet/categories")
def bs_categories(project_id: str, year: int = Query(...), store: ProjectStore = Depends(get_store)) -> dict:
    manifest = _manifest_or_404(store, project_id)
    _require_year(manifest.years, year)
    cats = balance_sheet_categories(_work(store, project_id, year))
    return {"year": year, "categories": cats}


@router.get("/{project_id}/analysis/balance-sheet/monthly")
def bs_monthly(
    project_id: str,
    year: int = Query(...),
    category: str = Query(...),
    store: ProjectStore = Depends(get_store),
) -> dict:
    manifest = _manifest_or_404(store, project_id)
    _require_year(manifest.years, year)
    df = category_monthly_movement(_work(store, project_id, year), category)
    return {"year": year, "category": category, "rows": _df_records(df)}


@router.get("/{project_id}/analysis/balance-sheet/accounts")
def bs_accounts(
    project_id: str,
    year: int = Query(...),
    category: str = Query(...),
    month: int | None = Query(None, ge=1, le=13),
    top_n: int = Query(15, ge=1, le=50),
    store: ProjectStore = Depends(get_store),
) -> dict:
    manifest = _manifest_or_404(store, project_id)
    _require_year(manifest.years, year)
    df = category_account_breakdown(_work(store, project_id, year), category, month=month, top_n=top_n)
    return {"year": year, "category": category, "month": month, "rows": _df_records(df)}


# ── 调账冲销 ──

@router.get("/{project_id}/analysis/adjustment/summary")
def adjustment_list(
    project_id: str,
    year: int = Query(...),
    keywords: str | None = Query(None, description="逗号分隔关键词，默认内置词表"),
    store: ProjectStore = Depends(get_store),
) -> dict:
    manifest = _manifest_or_404(store, project_id)
    _require_year(manifest.years, year)
    kws = [k.strip() for k in keywords.split(",") if k.strip()] if keywords else None
    df = adjustment_summary(_work(store, project_id, year), keywords=kws)
    return {"year": year, "row_count": len(df), "rows": _df_records(df)}


@router.get("/{project_id}/analysis/adjustment/drilldown")
def adjustment_drilldown(
    project_id: str,
    year: int = Query(...),
    voucher_id: str = Query(...),
    date: str | None = Query(None),
    store: ProjectStore = Depends(get_store),
) -> dict:
    manifest = _manifest_or_404(store, project_id)
    _require_year(manifest.years, year)
    df = adjustment_voucher_entries(_work(store, project_id, year), voucher_id, date)
    return {"year": year, "voucher_id": voucher_id, "row_count": len(df), "rows": _df_records(df)}
