"""投资收益与营业外收支分析。"""

from __future__ import annotations

import pandas as pd
from audit_engine.account_classifier import (
    investment_income_mask,
    non_operating_expense_mask,
    non_operating_income_mask,
)
from audit_engine.analysis.entry_display import entry_display_columns
from audit_engine.data_columns import ensure_analysis_columns

METRIC_LABELS = {
    "investment_income": "投资收益",
    "non_operating_income": "营业外收入",
    "non_operating_expense": "营业外支出",
}


def _metric_mask(work: pd.DataFrame, metric: str) -> pd.Series:
    if metric == "investment_income":
        return investment_income_mask(work)
    if metric == "non_operating_income":
        return non_operating_income_mask(work)
    if metric == "non_operating_expense":
        return non_operating_expense_mask(work)
    return pd.Series(False, index=work.index)


def monthly_other_pnl(work: pd.DataFrame) -> pd.DataFrame:
    work = ensure_analysis_columns(work)
    periods = list(range(1, 13))
    if work["_month"].eq(13).any():
        periods.append(13)

    rows: list[dict[str, float | int]] = []
    inv_mask = investment_income_mask(work)
    income_mask = non_operating_income_mask(work)
    expense_mask = non_operating_expense_mask(work)
    for period in periods:
        current = work[work["_month"].eq(period)]
        investment = float(current.loc[inv_mask.reindex(current.index, fill_value=False), "_pnl_effect"].sum())
        non_operating_income = float(
            current.loc[income_mask.reindex(current.index, fill_value=False), "_pnl_effect"].sum()
        )
        non_operating_expense = float(
            current.loc[expense_mask.reindex(current.index, fill_value=False), "_amount_raw"].sum()
        )
        rows.append(
            {
                "月份": period,
                "投资收益": investment,
                "营业外收入": non_operating_income,
                "营业外支出": non_operating_expense,
                "净影响": investment + non_operating_income - non_operating_expense,
            }
        )
    return pd.DataFrame(rows)


def other_pnl_entries(
    work: pd.DataFrame,
    *,
    month: int,
    metric: str,
    top_n: int | None = None,
) -> pd.DataFrame:
    work = ensure_analysis_columns(work)
    detail = work[_metric_mask(work, metric) & work["_month"].eq(int(month))].copy()
    if detail.empty:
        return pd.DataFrame()
    label = METRIC_LABELS.get(metric, metric)
    detail[label] = detail["_amount_raw"] if metric == "non_operating_expense" else detail["_pnl_effect"]
    detail = detail.sort_values("_amount_abs", ascending=False)
    if top_n:
        detail = detail.head(top_n)
    return entry_display_columns(detail, label)
