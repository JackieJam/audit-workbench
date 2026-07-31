"""标准成本与实际成本差异的结转分析。"""

from __future__ import annotations

import pandas as pd
from audit_engine.account_classifier import (
    CAT_INVENTORY,
    cost_variance_mask,
    manufacturing_cost_mask,
    operating_cost_mask,
)
from audit_engine.analysis.entry_display import entry_display_columns
from audit_engine.data_columns import VOUCHER_KEY_COLUMN, ensure_analysis_columns

METRIC_LABELS = {
    "variance": "差异科目发生额",
    "cogs": "结转营业成本影响",
    "inventory": "结转存货影响",
    "manufacturing": "制造归集影响",
}


def _counterpart_mask(work: pd.DataFrame, metric: str) -> pd.Series:
    if metric == "cogs":
        return operating_cost_mask(work)
    if metric == "inventory":
        return work["_acct_category"].eq(CAT_INVENTORY)
    if metric == "manufacturing":
        return manufacturing_cost_mask(work)
    return cost_variance_mask(work)


def _variance_vouchers(work: pd.DataFrame, month: int | None = None) -> set[str]:
    variance = cost_variance_mask(work)
    if month is not None:
        variance &= work["_month"].eq(int(month))
    return set(work.loc[variance, VOUCHER_KEY_COLUMN].dropna().astype(str))


def _period_end_mask(rows: pd.DataFrame) -> pd.Series:
    if "过账日期" not in rows.columns:
        return pd.Series(False, index=rows.index)
    dates = pd.to_datetime(rows["过账日期"], errors="coerce")
    return dates.notna() & ((dates.dt.days_in_month - dates.dt.day) < 5)


def monthly_cost_variance(work: pd.DataFrame) -> pd.DataFrame:
    """月度差异结转及去向。

    ``差异科目净额``用于勾稽差异账户是否清零；``结转营业成本``才是对当期
    毛利的直接影响。存货、制造归集分别展示，避免把正常标准成本调整误判为费用。
    """
    work = ensure_analysis_columns(work)
    periods = list(range(1, 13))
    if work["_month"].eq(13).any():
        periods.append(13)

    rows: list[dict[str, float | int | bool]] = []
    for month in periods:
        variance_rows = work[cost_variance_mask(work) & work["_month"].eq(month)]
        voucher_keys = set(variance_rows[VOUCHER_KEY_COLUMN].dropna().astype(str))
        counterpart = work[work[VOUCHER_KEY_COLUMN].astype(str).isin(voucher_keys)]
        absolute_amount = float(variance_rows["_amount_abs"].sum())
        end_amount = float(
            variance_rows.loc[_period_end_mask(variance_rows), "_amount_abs"].sum()
        )
        rows.append({
            "月份": month,
            "差异科目净额": float(variance_rows["_amount_raw"].sum()),
            "差异绝对发生额": absolute_amount,
            "结转营业成本": float(counterpart.loc[operating_cost_mask(counterpart), "_amount_raw"].sum()),
            "结转存货": float(
                counterpart.loc[counterpart["_acct_category"].eq(CAT_INVENTORY), "_amount_raw"].sum()
            ),
            "制造归集": float(
                counterpart.loc[manufacturing_cost_mask(counterpart), "_amount_raw"].sum()
            ),
            "期末五日占比": end_amount / absolute_amount if absolute_amount else 0.0,
            "异常波动": False,
        })

    result = pd.DataFrame(rows)
    active = result["差异绝对发生额"].gt(0)
    signal = result.loc[active, "结转营业成本"].abs()
    if len(signal) >= 4:
        median = float(signal.median())
        mad = float((signal - median).abs().median())
        if mad > 0:
            robust_z = 0.6745 * (signal - median).abs() / mad
            result.loc[signal.index, "异常波动"] = robust_z.gt(3.5)
        else:
            # 多数月份完全一致、少数月份突变时 MAD 为零；把偏离基线的少数月份标出。
            deviations = (signal - median).abs().gt(0)
            if 0 < int(deviations.sum()) <= max(1, len(signal) // 3):
                result.loc[signal.index, "异常波动"] = deviations
    return result


def cost_variance_summary(work: pd.DataFrame) -> dict[str, object]:
    work = ensure_analysis_columns(work)
    monthly = monthly_cost_variance(work)
    active = monthly[monthly["差异绝对发生额"].gt(0)]
    variance_rows = work[cost_variance_mask(work)]
    absolute_amount = float(variance_rows["_amount_abs"].sum())
    end_amount = float(
        variance_rows.loc[_period_end_mask(variance_rows), "_amount_abs"].sum()
    )
    largest = (
        active.loc[active["结转营业成本"].abs().idxmax()]
        if not active.empty
        else None
    )
    cogs_impact = float(monthly["结转营业成本"].sum())
    operating_cost_absolute = float(work.loc[operating_cost_mask(work), "_amount_abs"].sum())
    year_end_amount = float(
        active.loc[active["月份"].isin([12, 13]), "差异绝对发生额"].sum()
    )
    active_month_count = int(len(active))
    recurring_monthly = active_month_count >= 10
    interpretation = (
        f"成本差异覆盖 {active_month_count} 个期间，"
        + (
            "呈持续月度结转，月末集中通常是标准成本调整的正常流程；"
            if recurring_monthly
            else "未形成完整月度结转序列；"
        )
        + "系统不把差异科目本身直接并入毛利，重点观察对营业成本的实际影响、"
        "年末集中和相对自身历史的异常波动。"
    )
    return {
        "variance_absolute_amount": absolute_amount,
        "variance_net_amount": float(variance_rows["_amount_raw"].sum()),
        "cogs_impact": cogs_impact,
        "cogs_impact_ratio": abs(cogs_impact) / operating_cost_absolute if operating_cost_absolute else 0.0,
        "inventory_impact": float(monthly["结转存货"].sum()),
        "period_end_five_day_ratio": end_amount / absolute_amount if absolute_amount else 0.0,
        "year_end_amount_ratio": year_end_amount / absolute_amount if absolute_amount else 0.0,
        "active_month_count": active_month_count,
        "recurring_monthly": recurring_monthly,
        "largest_cogs_impact_month": int(largest["月份"]) if largest is not None else None,
        "largest_cogs_impact_amount": float(largest["结转营业成本"]) if largest is not None else 0.0,
        "outlier_months": [
            int(value)
            for value in monthly.loc[monthly["异常波动"], "月份"].tolist()
        ],
        "interpretation": interpretation,
    }


def cost_variance_entries(
    work: pd.DataFrame,
    *,
    month: int,
    metric: str,
    top_n: int | None = None,
) -> pd.DataFrame:
    work = ensure_analysis_columns(work)
    voucher_keys = _variance_vouchers(work, month)
    if metric == "variance":
        detail = work[cost_variance_mask(work) & work["_month"].eq(int(month))].copy()
    else:
        detail = work[
            work[VOUCHER_KEY_COLUMN].astype(str).isin(voucher_keys)
            & _counterpart_mask(work, metric)
        ].copy()
    if detail.empty:
        return pd.DataFrame()
    label = METRIC_LABELS.get(metric, metric)
    detail[label] = detail["_amount_raw"]
    detail = detail.sort_values("_amount_abs", ascending=False)
    if top_n:
        detail = detail.head(top_n)
    return entry_display_columns(detail, label)
