"""费用分析 — 跨年结构对比 + 分类钻取。"""

from __future__ import annotations

from typing import Any

import pandas as pd
from audit_engine.account_classifier import (
    CAT_EXPENSE,
    CAT_FINANCIAL_EXPENSE,
    CAT_RD_EXPENSE,
    CAT_TAX_SURCHARGE,
)
from audit_engine.analysis.entry_display import entry_display_columns
from audit_engine.config.accounts import classify_expense_subcategory
from audit_engine.data_columns import ensure_analysis_columns
from audit_engine.profiler import build_financial_summary


def expense_summary_table(financial: dict[str, Any]) -> pd.DataFrame:
    expenses = dict(financial.get("expenses", {}))
    if financial.get("rd_expense", 0) != 0:
        expenses["研发费用"] = financial["rd_expense"]
    if financial.get("financial_expense", 0) != 0:
        expenses["财务费用(汇兑)"] = financial["financial_expense"]
    if financial.get("tax_surcharge", 0) != 0:
        expenses["税金及附加"] = financial["tax_surcharge"]

    items = sorted(expenses.items(), key=lambda x: x[1], reverse=True)
    total = sum(v for _, v in items)
    return pd.DataFrame([
        {"费用类别": cat, "金额": val, "占比": val / total if total else 0}
        for cat, val in items
    ])


def cross_year_expense_table(financials: dict[int, dict]) -> pd.DataFrame:
    rows: list[dict] = []
    for year, financial in sorted(financials.items()):
        summary = expense_summary_table(financial)
        for row in summary.to_dict("records"):
            rows.append({
                "年份": int(year),
                "费用类别": str(row.get("费用类别", "")),
                "金额": float(row.get("金额", 0) or 0),
                "占比": float(row.get("占比", 0) or 0),
            })
    return pd.DataFrame(rows)


def build_expense_financials(work_by_year: dict[int, pd.DataFrame]) -> dict[int, dict]:
    return {year: build_financial_summary(df, year) for year, df in work_by_year.items()}


def expense_category_entries(
    work: pd.DataFrame,
    category: str,
    top_n: int | None = None,
) -> pd.DataFrame:
    work = ensure_analysis_columns(work)
    category = str(category).strip()
    if not category:
        return pd.DataFrame()

    if category == "研发费用":
        detail = work[work["_acct_category"].eq(CAT_RD_EXPENSE)].copy()
    elif category in ("财务费用", "财务费用(汇兑)"):
        detail = work[work["_acct_category"].eq(CAT_FINANCIAL_EXPENSE)].copy()
    elif category == "税金及附加":
        detail = work[work["_acct_category"].eq(CAT_TAX_SURCHARGE)].copy()
    else:
        expense_base = work[work["_acct_category"].eq(CAT_EXPENSE)].copy()
        expense_base["_expense_subcategory"] = expense_base["_account_name"].map(classify_expense_subcategory)
        detail = expense_base[expense_base["_expense_subcategory"].eq(category)].copy()

    if detail.empty:
        return pd.DataFrame()

    detail["费用发生额"] = detail["_amount_raw"]
    detail = detail.sort_values("_amount_abs", ascending=False)
    if top_n:
        detail = detail.head(top_n)
    return entry_display_columns(detail, "费用发生额")
