"""Agent 安全序时账查询：白名单过滤、有限聚合、可追溯口径。"""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

from audit_engine.store import ProjectStore

GROUP_FIELDS = {
    "year": ("年度", "_query_year"),
    "month": ("月份", "_query_period"),
    "account": ("科目", "_query_account"),
    "category": ("财务分类", "_acct_category"),
    "customer": ("客户", "_customer_display"),
    "supplier": ("供应商", "_vendor_display"),
    "debit_credit": ("借贷方向", "_dc"),
    "user": ("制单用户", "用户名"),
}


def _contains(series: pd.Series, value: object) -> pd.Series:
    text = str(value or "").strip()
    if not text:
        return pd.Series(True, index=series.index)
    return series.fillna("").astype(str).str.contains(text, case=False, regex=False)


def _records(frame: pd.DataFrame, limit: int) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    out = frame.head(limit).copy()
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            out[col] = out[col].dt.strftime("%Y-%m-%d")
        elif pd.api.types.is_float_dtype(out[col]):
            out[col] = out[col].round(2)
    return json.loads(out.to_json(orient="records", force_ascii=False))


def run_journal_query(
    store: ProjectStore,
    project_id: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """对项目序时账执行受限查询，不接受 SQL 或任意列名。"""
    manifest = store.load_manifest(project_id)
    requested_years = arguments.get("years") or manifest.years
    try:
        years = sorted({int(year) for year in requested_years})
    except (TypeError, ValueError):
        return {"error": "years 必须为年度整数数组"}
    invalid_years = [year for year in years if year not in manifest.years]
    if invalid_years:
        return {"error": f"项目中不存在年度：{invalid_years}"}

    frames: list[pd.DataFrame] = []
    amount_semantics: list[dict[str, Any]] = []
    for year in years:
        work = store.get_work_df(project_id, year)
        if work.empty:
            continue
        work = work.copy()
        work["_query_year"] = year
        work["_query_period"] = work["_month"].map(
            lambda month: f"{year}-{int(month):02d}" if pd.notna(month) else f"{year}-未知"
        )
        work["_query_account"] = (
            work["_acct"].fillna("").astype(str)
            + " "
            + work["_account_name"].fillna("").astype(str)
        ).str.strip()
        amount_semantics.append({
            "year": year,
            "amount_source": str(work["_amount_source"].iat[0]),
            "sign_mode": str(work["_amount_sign_mode"].iat[0]),
            "sign_confidence": float(work["_amount_sign_confidence"].iat[0]),
        })
        frames.append(work)
    if not frames:
        return {"error": "所选年度没有可查询分录"}
    source = pd.concat(frames, ignore_index=True)

    months = {int(month) for month in arguments.get("months") or []}
    if months:
        source = source[source["_month"].isin(months)]
    account_codes = {str(code).strip() for code in arguments.get("account_codes") or [] if str(code).strip()}
    if account_codes:
        source = source[source["_acct"].isin(account_codes)]
    voucher_ids = {str(value).strip() for value in arguments.get("voucher_ids") or [] if str(value).strip()}
    if voucher_ids:
        source = source[source.get("凭证编号", "").astype(str).isin(voucher_ids)]

    source = source[_contains(source["_account_name"], arguments.get("account_name_contains"))]
    source = source[_contains(source["_combined_text"], arguments.get("text_contains"))]
    source = source[_contains(source["_customer_display"], arguments.get("customer_contains"))]
    source = source[_contains(source["_vendor_display"], arguments.get("supplier_contains"))]

    category = str(arguments.get("category") or "").strip()
    if category:
        source = source[source["_acct_category"].eq(category)]
    debit_credit = str(arguments.get("debit_credit") or "").strip()
    if debit_credit:
        source = source[source["_dc"].eq(debit_credit)]

    dates = pd.to_datetime(source.get("过账日期"), errors="coerce")
    date_from = pd.to_datetime(arguments.get("date_from"), errors="coerce")
    date_to = pd.to_datetime(arguments.get("date_to"), errors="coerce")
    if pd.notna(date_from):
        source = source[dates >= date_from]
        dates = dates.loc[source.index]
    if pd.notna(date_to):
        source = source[dates <= date_to]

    min_amount = pd.to_numeric(arguments.get("min_absolute_amount"), errors="coerce")
    max_amount = pd.to_numeric(arguments.get("max_absolute_amount"), errors="coerce")
    if pd.notna(min_amount):
        source = source[source["_amount_abs"] >= float(min_amount)]
    if pd.notna(max_amount):
        source = source[source["_amount_abs"] <= float(max_amount)]

    matched_rows = int(len(source))
    voucher_series = source.get("凭证编号", pd.Series("", index=source.index)).fillna("").astype(str)
    metrics = {
        "row_count": matched_rows,
        "voucher_count": int(voucher_series[voucher_series.str.strip().ne("")].nunique()),
        "absolute_entry_amount": float(source["_amount_abs"].sum()),
        "debit_absolute_amount": float(source["_debit_abs"].sum()),
        "credit_absolute_amount": float(source["_credit_abs"].sum()),
        "normalized_signed_amount": float(source["_amount_raw"].sum()),
    }

    group_by = str(arguments.get("group_by") or "").strip()
    group_rows: list[dict[str, Any]] = []
    if group_by:
        if group_by not in GROUP_FIELDS:
            return {"error": f"不支持的 group_by：{group_by}", "allowed": sorted(GROUP_FIELDS)}
        label, field = GROUP_FIELDS[group_by]
        if field not in source.columns:
            return {"error": f"当前数据不包含分组维度：{label}"}
        grouped = (
            source.assign(_group=source[field].fillna("未维护").astype(str))
            .groupby("_group", dropna=False)
            .agg(
                row_count=("_amount_abs", "size"),
                voucher_count=("凭证编号", "nunique"),
                absolute_entry_amount=("_amount_abs", "sum"),
                normalized_signed_amount=("_amount_raw", "sum"),
                debit_absolute_amount=("_debit_abs", "sum"),
                credit_absolute_amount=("_credit_abs", "sum"),
            )
            .reset_index()
            .rename(columns={"_group": "group_value"})
            .sort_values("absolute_entry_amount", ascending=False)
        )
        limit = max(1, min(int(arguments.get("limit") or 20), 50))
        group_rows = _records(grouped, limit)

    sample_limit = max(0, min(int(arguments.get("sample_limit") or 10), 20))
    sample_columns = [
        "_query_year",
        "凭证编号",
        "过账日期",
        "行项目",
        "总账科目",
        "_account_name",
        "_acct_category",
        "借/贷标识",
        "_amount_raw",
        "_amount_abs",
        "_customer_display",
        "_vendor_display",
        "_combined_text",
    ]
    sample_columns = [column for column in sample_columns if column in source.columns]
    sample = source.sort_values("_amount_abs", ascending=False)[sample_columns].rename(columns={
        "_query_year": "年度",
        "_account_name": "科目名称",
        "_acct_category": "财务分类",
        "_amount_raw": "统一方向金额",
        "_amount_abs": "绝对发生额",
        "_customer_display": "客户",
        "_vendor_display": "供应商",
        "_combined_text": "摘要",
    })

    query_spec = {
        key: value
        for key, value in arguments.items()
        if value not in (None, "", [], {})
    }
    return {
        "metrics": metrics,
        "group_by": group_by or None,
        "groups": group_rows,
        "sample_rows": _records(sample, sample_limit),
        "provenance": {
            "project_id": project_id,
            "data_version": store.current_data_version(project_id),
            "classification_revision": store.current_classification_revision(project_id),
            "years": years,
            "query_spec": query_spec,
            "matched_row_count": matched_rows,
            "amount_semantics": amount_semantics,
            "metric_definitions": {
                "absolute_entry_amount": "逐行统一金额绝对值之和；同一凭证借贷两侧均计入",
                "normalized_signed_amount": "结合金额正负与借贷标识识别后的方向金额之和",
                "voucher_count": "匹配范围内非空凭证编号去重数",
            },
        },
    }
