"""资产负债分析 — 科目类别月度变动与集中度。"""

from __future__ import annotations

import pandas as pd
from audit_engine.account_classifier import BALANCE_SHEET_CATEGORIES, BALANCE_SHEET_SIDE
from audit_engine.analysis.entry_display import entry_display_columns
from audit_engine.data_columns import ensure_analysis_columns


def balance_sheet_categories(work: pd.DataFrame) -> list[str]:
    work = ensure_analysis_columns(work)
    present = set(work["_acct_category"].dropna().astype(str).unique())
    return [c for c in BALANCE_SHEET_CATEGORIES if c in present]


def _category_rows(work: pd.DataFrame, category: str) -> pd.DataFrame:
    work = ensure_analysis_columns(work)
    return work[work["_acct_category"].eq(category)]


def _net_change(debit: float, credit: float, category: str) -> float:
    if BALANCE_SHEET_SIDE.get(category) == "资产":
        return debit - credit
    return credit - debit


def category_monthly_movement(work: pd.DataFrame, category: str) -> pd.DataFrame:
    rows_src = _category_rows(work, category)
    rows: list[dict] = []
    periods = list(range(1, 13))
    if rows_src["_month"].eq(13).any():
        periods.append(13)
    for month in periods:
        m = rows_src[rows_src["_month"] == month]
        debit = float(m.loc[m["_dc"].eq("S"), "_amount_raw"].sum())
        credit = float(-m.loc[m["_dc"].eq("H"), "_amount_raw"].sum())
        rows.append({"月份": month, "借方发生额": debit, "贷方发生额": credit, "净变动": _net_change(debit, credit, category)})
    return pd.DataFrame(rows)


def category_account_breakdown(work: pd.DataFrame, category: str, month: int | None = None, top_n: int | None = 15) -> pd.DataFrame:
    rows_src = _category_rows(work, category)
    if month is not None:
        rows_src = rows_src[rows_src["_month"] == int(month)]
    if rows_src.empty:
        return pd.DataFrame(columns=["科目编号", "科目名称", "借方发生额", "贷方发生额", "净变动", "占比"])

    grouped_rows: list[dict] = []
    for code, grp in rows_src.groupby("_acct"):
        debit = float(grp.loc[grp["_dc"].eq("S"), "_amount_raw"].sum())
        credit = float(-grp.loc[grp["_dc"].eq("H"), "_amount_raw"].sum())
        name = next((n for n in grp["_account_name"].astype(str) if n and n != "nan"), "")
        grouped_rows.append({
            "科目编号": str(code), "科目名称": name,
            "借方发生额": debit, "贷方发生额": credit,
            "净变动": _net_change(debit, credit, category),
        })
    result = pd.DataFrame(grouped_rows)
    total_abs = result["净变动"].abs().sum()
    result["占比"] = result["净变动"].abs() / total_abs if total_abs else 0.0
    result = result.reindex(result["净变动"].abs().sort_values(ascending=False).index).reset_index(drop=True)
    return result.head(top_n) if top_n else result


def category_month_entries(work: pd.DataFrame, category: str, month: int, direction: str, top_n: int | None = None) -> pd.DataFrame:
    detail = _category_rows(work, category)
    detail = detail[detail["_month"] == int(month)].copy()
    if direction == "debit":
        detail = detail[detail["_dc"] == "S"].copy()
        amount_label = "借方发生额"
        detail[amount_label] = detail["_amount_raw"]
    elif direction == "credit":
        detail = detail[detail["_dc"] == "H"].copy()
        amount_label = "贷方发生额"
        detail[amount_label] = -detail["_amount_raw"]
    elif direction == "net":
        amount_label = "净变动影响"
        sign = 1.0 if BALANCE_SHEET_SIDE.get(category) == "资产" else -1.0
        detail[amount_label] = sign * detail["_amount_raw"]
    else:
        return pd.DataFrame()
    if detail.empty:
        return pd.DataFrame()
    detail = detail.sort_values("_amount_abs", ascending=False)
    if top_n:
        detail = detail.head(top_n)
    return entry_display_columns(detail, amount_label)


def category_account_entries(work: pd.DataFrame, category: str, account_code: str, top_n: int | None = None) -> pd.DataFrame:
    detail = _category_rows(work, category)
    detail = detail[detail["_acct"] == str(account_code).strip()].copy()
    if detail.empty:
        return pd.DataFrame()
    detail["发生额"] = detail["_amount_raw"]
    detail = detail.sort_values("_amount_abs", ascending=False)
    if top_n:
        detail = detail.head(top_n)
    return entry_display_columns(detail, "发生额")
