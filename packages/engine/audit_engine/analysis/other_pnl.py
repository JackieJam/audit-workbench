"""投资收益与营业外收支分析。"""

from __future__ import annotations

import pandas as pd
from audit_engine.account_classifier import (
    asset_disposal_mask,
    asset_impairment_mask,
    credit_impairment_mask,
    fair_value_change_mask,
    income_tax_expense_mask,
    investment_income_mask,
    non_operating_expense_mask,
    non_operating_income_mask,
    other_income_mask,
)
from audit_engine.analysis.entry_display import entry_display_columns
from audit_engine.data_columns import ensure_analysis_columns

METRIC_LABELS = {
    "investment_income": "投资收益",
    "fair_value_change": "公允价值变动损益",
    "other_income": "其他收益",
    "asset_disposal": "资产处置收益",
    "non_operating_income": "营业外收入",
    "non_operating_expense": "营业外支出",
    "credit_impairment": "信用减值损失",
    "asset_impairment": "资产减值损失",
    "income_tax": "所得税费用",
}


def _metric_mask(work: pd.DataFrame, metric: str) -> pd.Series:
    if metric == "investment_income":
        return investment_income_mask(work)
    if metric == "fair_value_change":
        return fair_value_change_mask(work)
    if metric == "other_income":
        return other_income_mask(work)
    if metric == "asset_disposal":
        return asset_disposal_mask(work)
    if metric == "non_operating_income":
        return non_operating_income_mask(work)
    if metric == "non_operating_expense":
        return non_operating_expense_mask(work)
    if metric == "credit_impairment":
        return credit_impairment_mask(work)
    if metric == "asset_impairment":
        return asset_impairment_mask(work)
    if metric == "income_tax":
        return income_tax_expense_mask(work)
    return pd.Series(False, index=work.index)


def monthly_other_pnl(work: pd.DataFrame) -> pd.DataFrame:
    work = ensure_analysis_columns(work)
    periods = list(range(1, 13))
    if work["_month"].eq(13).any():
        periods.append(13)

    rows: list[dict[str, float | int]] = []
    metric_masks = {metric: _metric_mask(work, metric) for metric in METRIC_LABELS}
    income_metrics = {
        "investment_income",
        "fair_value_change",
        "other_income",
        "asset_disposal",
        "non_operating_income",
    }
    for period in periods:
        current = work[work["_month"].eq(period)]
        row: dict[str, float | int] = {"月份": period}
        net_impact = 0.0
        for metric, label in METRIC_LABELS.items():
            mask = metric_masks[metric].reindex(current.index, fill_value=False)
            if metric in income_metrics:
                amount = float(current.loc[mask, "_pnl_effect"].sum())
                net_impact += amount
            else:
                amount = float(current.loc[mask, "_amount_raw"].sum())
                net_impact -= amount
            row[label] = amount
        row["净影响"] = net_impact
        rows.append(row)
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
    expense_metrics = {
        "non_operating_expense",
        "credit_impairment",
        "asset_impairment",
        "income_tax",
    }
    detail[label] = detail["_amount_raw"] if metric in expense_metrics else detail["_pnl_effect"]
    detail = detail.sort_values("_amount_abs", ascending=False)
    if top_n:
        detail = detail.head(top_n)
    return entry_display_columns(detail, label)
