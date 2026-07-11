"""暂估往来分析 — 应付暂估 / 其他应收 / 其他应付。"""

from __future__ import annotations

import pandas as pd

from audit_engine.account_classifier import (
    CAT_AP,
    CAT_AP_ACCRUAL,
    CAT_OTHER_PAYABLE,
    CAT_OTHER_RECEIVABLE,
)
from audit_engine.analysis.entry_display import entry_display_columns
from audit_engine.data_columns import ensure_analysis_columns


def _ap_accrual_rows(work: pd.DataFrame) -> pd.DataFrame:
    work = ensure_analysis_columns(work)
    account_name = work.get("_account_name", pd.Series("", index=work.index)).astype(str)
    return work[
        work["_acct_category"].eq(CAT_AP_ACCRUAL)
        | (work["_acct_category"].eq(CAT_AP) & account_name.str.contains("暂估|GR/IR", na=False))
    ]


def ap_accrual_monthly(work: pd.DataFrame) -> pd.DataFrame:
    accrual = _ap_accrual_rows(work)
    rows: list[dict] = []
    for month in range(1, 13):
        m = accrual[accrual["_month"] == month]
        credit_increase = float(-m.loc[m["_dc"] == "H", "_credit_amount"].sum())
        debit_decrease = float(m.loc[m["_dc"] == "S", "_debit_amount"].sum())
        rows.append({
            "月份": month,
            "暂估贷方增加": credit_increase,
            "暂估借方减少": debit_decrease,
            "暂估净额": credit_increase - debit_decrease,
        })
    return pd.DataFrame(rows)


def ap_accrual_suppliers(work: pd.DataFrame, month: int, top_n: int = 10) -> pd.DataFrame:
    accrual = _ap_accrual_rows(work)
    rows = accrual[(accrual["_month"] == month) & (accrual["_vendor_display"] != "未维护")].copy()
    if rows.empty:
        return pd.DataFrame(
            columns=["供应商", "暂估贷方增加", "暂估借方减少", "暂估净额", "贷方占比", "借方占比", "净额占比"]
        )

    grouped = []
    for vendor, grp in rows.groupby("_vendor_display"):
        credit = float(-grp.loc[grp["_dc"] == "H", "_credit_amount"].sum())
        debit = float(grp.loc[grp["_dc"] == "S", "_debit_amount"].sum())
        grouped.append({"供应商": vendor, "暂估贷方增加": credit, "暂估借方减少": debit, "暂估净额": credit - debit})

    result = pd.DataFrame(grouped)
    credit_total = result["暂估贷方增加"].sum()
    debit_total = result["暂估借方减少"].sum()
    net_total = result["暂估净额"].sum()
    result["贷方占比"] = result["暂估贷方增加"] / credit_total if credit_total else 0
    result["借方占比"] = result["暂估借方减少"] / debit_total if debit_total else 0
    result["净额占比"] = result["暂估净额"] / net_total if net_total else 0
    result["_sort"] = result["暂估净额"].abs()
    return result.sort_values("_sort", ascending=False).drop(columns=["_sort"]).head(top_n).reset_index(drop=True)


def ap_accrual_entries(
    work: pd.DataFrame,
    month: int,
    direction: str,
    supplier: str | None = None,
    top_n: int | None = None,
) -> pd.DataFrame:
    detail = _ap_accrual_rows(work)
    detail = detail[detail["_month"] == month].copy()
    if supplier:
        detail = detail[detail["_vendor_display"] == supplier].copy()
    if direction == "credit":
        detail = detail[detail["_dc"] == "H"].copy()
        amount_label = "暂估贷方增加"
        detail[amount_label] = -detail["_credit_amount"]
    elif direction == "debit":
        detail = detail[detail["_dc"] == "S"].copy()
        amount_label = "暂估借方减少"
        detail[amount_label] = detail["_debit_amount"]
    elif direction == "net":
        amount_label = "暂估净额影响"
        detail[amount_label] = -detail["_amount_raw"]
    else:
        return pd.DataFrame()
    if detail.empty:
        return pd.DataFrame()
    detail = detail.sort_values("_amount_abs", ascending=False)
    if top_n:
        detail = detail.head(top_n)
    return entry_display_columns(detail, amount_label)


def _other_receivable_rows(work: pd.DataFrame) -> pd.DataFrame:
    work = ensure_analysis_columns(work)
    return work[work["_acct_category"].eq(CAT_OTHER_RECEIVABLE)]


def other_receivable_monthly(work: pd.DataFrame) -> pd.DataFrame:
    other_receivable = _other_receivable_rows(work)
    rows: list[dict] = []
    for month in range(1, 13):
        m = other_receivable[other_receivable["_month"] == month]
        debit_s = float(m.loc[m["_dc"] == "S", "_amount_raw"].sum())
        credit_h = float((-m.loc[m["_dc"] == "H", "_amount_raw"]).sum())
        rows.append({
            "月份": month,
            "其他应收S发生额": debit_s,
            "其他应收H发生额": credit_h,
            "其他应收净额": debit_s - credit_h,
        })
    return pd.DataFrame(rows)


def other_receivable_entries(
    work: pd.DataFrame,
    month: int,
    direction: str,
    top_n: int | None = None,
) -> pd.DataFrame:
    detail = _other_receivable_rows(work)
    detail = detail[detail["_month"] == month].copy()
    if direction == "debit":
        detail = detail[detail["_dc"] == "S"].copy()
        amount_label = "其他应收S发生额"
        detail[amount_label] = detail["_amount_raw"]
    elif direction == "credit":
        detail = detail[detail["_dc"] == "H"].copy()
        amount_label = "其他应收H发生额"
        detail[amount_label] = -detail["_amount_raw"]
    elif direction == "net":
        amount_label = "其他应收净额影响"
        detail[amount_label] = detail["_amount_raw"]
    else:
        return pd.DataFrame()
    if detail.empty:
        return pd.DataFrame()
    detail = detail.sort_values("_amount_abs", ascending=False)
    if top_n:
        detail = detail.head(top_n)
    return entry_display_columns(detail, amount_label)


def _other_payable_rows(work: pd.DataFrame) -> pd.DataFrame:
    work = ensure_analysis_columns(work)
    return work[work["_acct_category"].eq(CAT_OTHER_PAYABLE)]


def other_payable_monthly(work: pd.DataFrame) -> pd.DataFrame:
    other_payable = _other_payable_rows(work)
    rows: list[dict] = []
    for month in range(1, 13):
        m = other_payable[other_payable["_month"] == month]
        accrual_h = float((-m.loc[m["_dc"] == "H", "_amount_raw"]).sum())
        writeoff_s = float(m.loc[m["_dc"] == "S", "_amount_raw"].sum())
        rows.append({
            "月份": month,
            "其他应付预提H": accrual_h,
            "其他应付核销S": writeoff_s,
            "其他应付净值": accrual_h - writeoff_s,
        })
    return pd.DataFrame(rows)


def other_payable_entries(
    work: pd.DataFrame,
    month: int,
    direction: str,
    top_n: int | None = None,
) -> pd.DataFrame:
    detail = _other_payable_rows(work)
    detail = detail[detail["_month"] == month].copy()
    if direction == "accrual":
        detail = detail[detail["_dc"] == "H"].copy()
        amount_label = "其他应付预提H"
        detail[amount_label] = -detail["_amount_raw"]
    elif direction == "writeoff":
        detail = detail[detail["_dc"] == "S"].copy()
        amount_label = "其他应付核销S"
        detail[amount_label] = detail["_amount_raw"]
    elif direction == "net":
        amount_label = "其他应付净值影响"
        detail[amount_label] = -detail["_amount_raw"]
    else:
        return pd.DataFrame()
    if detail.empty:
        return pd.DataFrame()
    detail = detail.sort_values("_amount_abs", ascending=False)
    if top_n:
        detail = detail.head(top_n)
    return entry_display_columns(detail, amount_label)
