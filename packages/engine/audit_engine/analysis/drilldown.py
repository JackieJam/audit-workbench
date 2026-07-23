"""图表钻取 — 从聚合视图回查序时账分录。"""

from __future__ import annotations

import pandas as pd
from audit_engine.account_classifier import operating_cost_mask, operating_revenue_mask
from audit_engine.analysis.adjustment import adjustment_voucher_entries
from audit_engine.analysis.balance_sheet import category_account_entries, category_month_entries
from audit_engine.analysis.entry_display import entry_display_columns
from audit_engine.analysis.expense import expense_category_entries
from audit_engine.analysis.other_pnl import other_pnl_entries
from audit_engine.analysis.statistical_profile import (
    benford_digit_entries,
    month_end_entries,
)
from audit_engine.analysis.working_capital import (
    ap_accrual_entries,
    other_payable_entries,
    other_receivable_entries,
)
from audit_engine.data_columns import ensure_analysis_columns


def monthly_income_cost_entries(
    work: pd.DataFrame,
    *,
    month: int,
    metric: str,
    category: str = "总计",
    top_n: int | None = None,
) -> pd.DataFrame:
    work = ensure_analysis_columns(work)
    source = work if category == "总计" else work[work["_pnl_category"] == category]

    if metric == "revenue":
        detail = source[operating_revenue_mask(source) & (source["_month"] == month)].copy()
        amount_label = "收入影响"
        detail[amount_label] = detail["_pnl_effect"]
    elif metric == "cost":
        detail = source[operating_cost_mask(source) & (source["_month"] == month)].copy()
        amount_label = "成本发生额"
        detail[amount_label] = detail["_amount_raw"]
    elif metric == "gross":
        detail = source[
            (operating_revenue_mask(source) | operating_cost_mask(source))
            & (source["_month"] == month)
        ].copy()
        amount_label = "毛利影响"
        detail[amount_label] = detail["_pnl_effect"]
    else:
        return pd.DataFrame()

    if detail.empty:
        return pd.DataFrame()

    detail = detail.sort_values("_amount_abs", ascending=False)
    if top_n:
        detail = detail.head(top_n)
    return entry_display_columns(detail, amount_label)


def customer_revenue_entries(
    work: pd.DataFrame,
    *,
    customer: str,
    category: str = "总计",
    top_n: int | None = None,
) -> pd.DataFrame:
    work = ensure_analysis_columns(work)
    source = work if category == "总计" else work[work["_pnl_category"] == category]
    detail = source[
        operating_revenue_mask(source) & (source["_customer_display"] == customer)
    ].copy()
    if detail.empty:
        return pd.DataFrame()

    detail["收入影响"] = detail["_pnl_effect"]
    detail = detail.sort_values("_amount_abs", ascending=False)
    if top_n:
        detail = detail.head(top_n)
    return entry_display_columns(detail, "收入影响")


def resolve_drilldown(work: pd.DataFrame, selector: dict, *, limit: int | None = None) -> pd.DataFrame:
    """按 selector 解析各模块钻取明细（供疑点入库复用）。"""
    kind = selector.get("kind")

    if kind == "monthly_income_cost":
        return monthly_income_cost_entries(
            work,
            month=int(selector["month"]),
            metric=str(selector["metric"]),
            category=str(selector.get("category") or "总计"),
            top_n=limit,
        )
    if kind == "customer_revenue":
        return customer_revenue_entries(
            work,
            customer=str(selector["customer"]),
            category=str(selector.get("category") or "总计"),
            top_n=limit,
        )
    if kind == "expense_category":
        return expense_category_entries(work, str(selector["expense_category"]), top_n=limit)
    if kind == "other_pnl_month":
        return other_pnl_entries(
            work,
            month=int(selector["month"]),
            metric=str(selector["metric"]),
            top_n=limit,
        )
    if kind == "profile_benford_digit":
        return benford_digit_entries(
            work,
            int(selector["digit"]),
            top_n=limit,
        )
    if kind == "profile_month_end":
        return month_end_entries(
            work,
            int(selector["month"]),
            top_n=limit,
        )
    if kind == "ap_accrual_month":
        return ap_accrual_entries(
            work,
            month=int(selector["month"]),
            direction=str(selector["direction"]),
            top_n=limit,
        )
    if kind == "ap_accrual_supplier":
        return ap_accrual_entries(
            work,
            month=int(selector["month"]),
            direction=str(selector["direction"]),
            supplier=str(selector.get("supplier") or ""),
            top_n=limit,
        )
    if kind == "other_receivable_month":
        return other_receivable_entries(
            work,
            month=int(selector["month"]),
            direction=str(selector["direction"]),
            top_n=limit,
        )
    if kind == "other_payable_month":
        return other_payable_entries(
            work,
            month=int(selector["month"]),
            direction=str(selector["direction"]),
            top_n=limit,
        )
    if kind == "bs_category_month":
        return category_month_entries(
            work,
            str(selector["category"]),
            int(selector["month"]),
            str(selector["direction"]),
            top_n=limit,
        )
    if kind == "bs_category_account":
        return category_account_entries(
            work,
            str(selector["category"]),
            str(selector["account"]),
            top_n=limit,
        )
    if kind == "adjustment_voucher":
        return adjustment_voucher_entries(
            work,
            str(selector["voucher_id"]),
            str(selector.get("date") or "") or None,
        )
    return pd.DataFrame()


# 兼容旧调用名
resolve_income_cost_drilldown = resolve_drilldown
