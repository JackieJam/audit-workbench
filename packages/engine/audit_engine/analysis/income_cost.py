"""收入成本分析聚合视图。"""

from __future__ import annotations

import pandas as pd
from audit_engine.account_classifier import (
    CAT_EXPENSE,
    CAT_FINANCIAL_EXPENSE,
    CAT_RD_EXPENSE,
    CAT_TAX_SURCHARGE,
    operating_cost_mask,
    operating_revenue_mask,
)
from audit_engine.data_columns import ensure_analysis_columns


def income_cost_categories(work: pd.DataFrame) -> list[str]:
    work = ensure_analysis_columns(work)
    pnl_mask = operating_revenue_mask(work) | operating_cost_mask(work)
    categories = [
        c
        for c in work.loc[pnl_mask, "_pnl_category"].dropna().astype(str).unique().tolist()
        if c
    ]
    order = {
        "主营业务-第三方": 10,
        "主营业务-内部关联方": 20,
        "主营业务-外部关联方": 30,
        "主营业务-其他": 40,
        "其他业务": 50,
    }
    return ["总计"] + sorted(categories, key=lambda x: (order.get(x, 999), x))


def monthly_revenue_cost(work: pd.DataFrame, category: str = "总计") -> pd.DataFrame:
    work = ensure_analysis_columns(work)
    source = work if category == "总计" else work[work["_pnl_category"] == category]

    rows: list[dict] = []
    revenue_mask = operating_revenue_mask(source)
    cost_mask = operating_cost_mask(source)
    expense_mask = source["_acct_category"].isin(
        [CAT_EXPENSE, CAT_RD_EXPENSE, CAT_FINANCIAL_EXPENSE, CAT_TAX_SURCHARGE]
    )

    periods = list(range(1, 13))
    if source["_month"].eq(13).any():
        periods.append(13)
    for month in periods:
        m = source[source["_month"] == month]
        revenue = m[revenue_mask.reindex(m.index, fill_value=False)]
        cost = m[cost_mask.reindex(m.index, fill_value=False)]
        expense = m[expense_mask.reindex(m.index, fill_value=False)]

        income_h = float(revenue.loc[revenue["_dc"] == "H", "_amount_raw"].sum())
        income_s = float(revenue.loc[revenue["_dc"] == "S", "_amount_raw"].sum())
        cost_s = float(cost.loc[cost["_dc"] == "S", "_amount_raw"].sum())
        cost_h = float(cost.loc[cost["_dc"] == "H", "_amount_raw"].sum())
        expense_s = float(expense.loc[expense["_dc"] == "S", "_amount_raw"].sum())
        expense_h = float(expense.loc[expense["_dc"] == "H", "_amount_raw"].sum())
        net_revenue = -(income_h + income_s)
        net_cost_effect = -(cost_s + cost_h)
        net_cost = cost_s + cost_h  # 正数=成本净发生额
        net_expense = expense_s + expense_h

        rows.append({
            "月份": month,
            "净收入": net_revenue,
            "净成本": net_cost,
            "净成本影响": net_cost_effect,
            "净费用": net_expense,
            "毛利": net_revenue + net_cost_effect,
            "营业利润": net_revenue + net_cost_effect - net_expense,
        })
    return pd.DataFrame(rows)


def customer_revenue_top(work: pd.DataFrame, category: str = "总计", top_n: int = 10) -> pd.DataFrame:
    work = ensure_analysis_columns(work)
    source = work if category == "总计" else work[work["_pnl_category"] == category]
    revenue = source[operating_revenue_mask(source) & (source["_customer_display"] != "未维护")]
    if revenue.empty:
        return pd.DataFrame(columns=["客户", "净收入", "占比"])

    rev_rows = []
    for customer, grp in revenue.groupby("_customer_display"):
        income_h = float(grp.loc[grp["_dc"] == "H", "_amount_raw"].sum())
        income_s = float(grp.loc[grp["_dc"] == "S", "_amount_raw"].sum())
        rev_rows.append({"客户": customer, "净收入": -(income_h + income_s)})
    result = pd.DataFrame(rev_rows)
    total = result["净收入"].sum()
    result["占比"] = result["净收入"] / total if total else 0
    return result.sort_values("净收入", ascending=False).head(top_n).reset_index(drop=True)
