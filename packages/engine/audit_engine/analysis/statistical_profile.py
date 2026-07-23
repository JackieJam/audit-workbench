"""统计画像的可回查样本口径。"""

from __future__ import annotations

import numpy as np
import pandas as pd
from audit_engine.analysis.entry_display import entry_display_columns
from audit_engine.data_columns import ensure_analysis_columns


def benford_eligible_rows(work: pd.DataFrame) -> pd.DataFrame:
    """本福特样本：借方分录、分析金额绝对值不低于 10，避免借贷重复计数和微小金额噪声。"""
    ready = ensure_analysis_columns(work)
    finite = np.isfinite(ready["_amount_abs"])
    return ready[
        ready["_dc"].eq("S")
        & finite
        & ready["_amount_abs"].ge(10)
    ].copy()


def first_digit(amounts: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(amounts, errors="coerce").abs()
    return np.floor(numeric / (10 ** np.floor(np.log10(numeric)))).astype("Int64")


def benford_digit_entries(
    work: pd.DataFrame,
    digit: int,
    *,
    top_n: int | None = None,
) -> pd.DataFrame:
    if digit < 1 or digit > 9:
        return pd.DataFrame()
    detail = benford_eligible_rows(work)
    detail["首位数字"] = first_digit(detail["_amount_abs"])
    detail = detail[detail["首位数字"].eq(digit)].copy()
    if detail.empty:
        return pd.DataFrame()
    detail["分析金额"] = detail["_amount_abs"]
    detail = detail.sort_values("_amount_abs", ascending=False)
    if top_n:
        detail = detail.head(top_n)
    return entry_display_columns(detail, "首位数字").join(
        detail[["分析金额"]]
    )


def month_end_entries(
    work: pd.DataFrame,
    month: int,
    *,
    top_n: int | None = None,
) -> pd.DataFrame:
    if month < 1 or month > 12:
        return pd.DataFrame()
    ready = ensure_analysis_columns(work)
    detail = ready[ready["_month"].eq(month)].copy()
    if detail.empty:
        return pd.DataFrame()
    is_month_end = detail["过账日期"].dt.day > (detail["过账日期"].dt.days_in_month - 5)
    detail = detail[is_month_end].copy()
    if detail.empty:
        return pd.DataFrame()
    detail["月末发生额"] = detail["_amount_raw"]
    detail = detail.sort_values("_amount_abs", ascending=False)
    if top_n:
        detail = detail.head(top_n)
    return entry_display_columns(detail, "月末发生额")
