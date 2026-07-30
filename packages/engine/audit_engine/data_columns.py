"""Derived analysis columns for journal work DataFrames."""

from __future__ import annotations

from typing import Any

import pandas as pd

from audit_engine.account_classifier import (
    apply_prefix_category,
    auto_classify,
    classify_dataframe,
    uncategorized_reason,
)

AMOUNT_MODE_SIGNED = "signed_raw"
AMOUNT_MODE_DC_MULTIPLIER = "dc_multiplier"


def infer_amount_sign_mode(
    raw: pd.Series,
    dc: pd.Series,
    voucher_ids: pd.Series | None = None,
) -> tuple[str, float]:
    """判断金额列是自身带方向，还是需结合借贷标识解释。

    两种常见导出格式：
    - signed_raw：借方通常为正、贷方通常为负，反向发生额保留相反符号；
    - dc_multiplier：借贷列决定正常方向，原始正负表示正常/冲回。

    优先选择能让凭证借贷残差更小的模式；没有凭证号时按贷方金额符号多数决。
    """
    values = pd.to_numeric(raw, errors="coerce").fillna(0.0)
    side = dc.astype(str).str.strip()
    recognized = side.isin(["S", "H"]) & values.ne(0)
    if not recognized.any():
        return AMOUNT_MODE_SIGNED, 0.0

    signed_candidate = values
    dc_candidate = values.where(side != "H", -values)

    if voucher_ids is not None:
        voucher = voucher_ids.fillna("").astype(str).str.strip()
        usable = recognized & voucher.ne("")
        if usable.any():
            denominator = float(values.loc[usable].abs().sum()) or 1.0
            signed_residual = float(signed_candidate.loc[usable].groupby(voucher.loc[usable]).sum().abs().sum()) / denominator
            dc_residual = float(dc_candidate.loc[usable].groupby(voucher.loc[usable]).sum().abs().sum()) / denominator
            if abs(signed_residual - dc_residual) > 1e-9:
                mode = AMOUNT_MODE_SIGNED if signed_residual < dc_residual else AMOUNT_MODE_DC_MULTIPLIER
                confidence = abs(signed_residual - dc_residual) / max(signed_residual, dc_residual, 1e-9)
                return mode, round(min(confidence, 1.0), 4)

    credit_values = values.loc[recognized & side.eq("H")]
    if not credit_values.empty:
        positive_ratio = float(credit_values.gt(0).mean())
        mode = AMOUNT_MODE_DC_MULTIPLIER if positive_ratio >= 0.5 else AMOUNT_MODE_SIGNED
        return mode, round(abs(positive_ratio - 0.5) * 2, 4)
    return AMOUNT_MODE_SIGNED, 0.0


def normalize_signed_amount(
    raw: pd.Series,
    dc: pd.Series,
    voucher_ids: pd.Series | None = None,
    *,
    mode: str | None = None,
) -> pd.Series:
    """统一金额方向，同时保留负借方/正贷方等反向发生额。"""
    values = pd.to_numeric(raw, errors="coerce").fillna(0.0)
    side = dc.astype(str).str.strip()
    resolved_mode = mode or infer_amount_sign_mode(values, side, voucher_ids)[0]
    if resolved_mode == AMOUNT_MODE_DC_MULTIPLIER:
        signed = values.where(side != "H", -values)
    else:
        signed = values.copy()
    return signed.where(side.isin(["S", "H"]), values)


def _account_text_col(df: pd.DataFrame) -> str | None:
    for col in ("总账科目：长文本", "总账科目：短文本"):
        if col in df.columns:
            return col
    return None


def _safe_text(df: pd.DataFrame, col: str) -> pd.Series:
    if col in df.columns:
        return df[col].fillna("").astype(str)
    return pd.Series("", index=df.index)


def _display_party(code: object, name: object) -> str:
    code_s = "" if pd.isna(code) else str(code).replace(".0", "").strip()
    name_s = "" if pd.isna(name) else str(name).strip()
    if code_s and name_s:
        return f"{code_s} - {name_s}"
    return code_s or name_s or "未维护"


def _propagate_unique_party(
    df: pd.DataFrame,
    display: pd.Series,
) -> tuple[pd.Series, pd.Series]:
    """同凭证只有一个明确主体时，把主体安全地补到收入/暂估等对应行。"""
    original = display.fillna("未维护").astype(str)
    if "凭证编号" not in df.columns:
        return original, pd.Series("line", index=df.index)

    group_cols = ["凭证编号"]
    for col in ("公司代码", "会计年度"):
        if col in df.columns and df[col].astype(str).str.strip().ne("").any():
            group_cols.insert(0, col)

    valid = original.ne("未维护") & original.str.strip().ne("")
    unique_by_group = (
        df.loc[valid, group_cols]
        .assign(_party=original.loc[valid])
        .groupby(group_cols, dropna=False)["_party"]
        .agg(lambda values: next(iter(set(values))) if len(set(values)) == 1 else "")
    )
    keys = pd.MultiIndex.from_frame(df[group_cols]) if len(group_cols) > 1 else df[group_cols[0]]
    inherited = pd.Series(keys.map(unique_by_group), index=df.index).fillna("")
    result = original.where(valid, inherited.where(inherited.ne(""), "未维护"))
    source = pd.Series("missing", index=df.index)
    source.loc[valid] = "line"
    source.loc[~valid & inherited.ne("")] = "voucher"
    return result, source


def _related_party_category(account_name: object, default: str = "") -> str:
    text = "" if pd.isna(account_name) else str(account_name)
    if "内部关联" in text:
        return "内部关联方"
    if "外部关联" in text:
        return "外部关联方"
    if "第三方" in text:
        return "第三方"
    if "其他" in text and default != "第三方":
        return "其他"
    return default


def _pnl_category(acct4: object, account_name: object) -> str:
    acct4_s = "" if pd.isna(acct4) else str(acct4)
    if acct4_s in ("6001", "6401"):
        return f"主营业务-{_related_party_category(account_name, default='第三方')}"
    if acct4_s in ("6051", "6402"):
        related = _related_party_category(account_name)
        return f"其他业务-{related}" if related else "其他业务"
    return ""


def add_analysis_columns(
    df: pd.DataFrame,
    *,
    category_overrides: dict[str, str] | None = None,
) -> pd.DataFrame:
    out = df.copy()
    out["_acct"] = out["总账科目"].astype(str).str.strip()
    out["_acct4"] = out["_acct"].str[:4]
    out["_month"] = out["过账日期"].dt.month
    if "过账期间" in out.columns:
        p13 = pd.to_numeric(out["过账期间"], errors="coerce").eq(13)
        out["_is_period13"] = p13
        out.loc[p13, "_month"] = 13
    amount_col = "公司代码货币价值" if "公司代码货币价值" in out.columns else "凭证货币价值"
    out["_dc"] = out["借/贷标识"].astype(str).str.strip()
    voucher_ids = out["凭证编号"] if "凭证编号" in out.columns else None
    amount_mode, amount_confidence = infer_amount_sign_mode(out[amount_col], out["_dc"], voucher_ids)
    out["_amount_raw"] = normalize_signed_amount(
        out[amount_col],
        out["_dc"],
        voucher_ids,
        mode=amount_mode,
    )
    out["_amount_sign_mode"] = amount_mode
    out["_amount_sign_confidence"] = amount_confidence
    out["_amount_source"] = amount_col
    if amount_col == "公司代码货币价值":
        out["_currency_basis"] = "company"
        out["_amount_currency"] = _safe_text(out, "公司代码货币代码").replace("", "未维护")
    else:
        out["_currency_basis"] = "document"
        out["_amount_currency"] = _safe_text(out, "凭证货币代码").replace("", "未维护")
    out["_amount_abs"] = out["_amount_raw"].abs()
    out["_debit_amount"] = out["_amount_raw"].where(out["_dc"] == "S", 0)
    out["_credit_amount"] = out["_amount_raw"].where(out["_dc"] == "H", 0)
    out["_debit_abs"] = out["_amount_abs"].where(out["_dc"] == "S", 0)
    out["_credit_abs"] = out["_amount_abs"].where(out["_dc"] == "H", 0)
    out["_pnl_effect"] = -out["_amount_raw"]

    account_text = _account_text_col(out)
    out["_account_name"] = _safe_text(out, account_text) if account_text else ""
    out["_pnl_category"] = [
        _pnl_category(acct4, account_name)
        for acct4, account_name in zip(out["_acct4"], out["_account_name"], strict=False)
    ]

    overrides = category_overrides or {}
    auto_cats = pd.Series(
        [
            apply_prefix_category(code, auto_classify(name))
            for code, name in zip(out["_acct"], out["_account_name"], strict=False)
        ],
        index=out.index,
    )
    if overrides:
        manual = out["_acct"].map(overrides)
        out["_acct_category"] = manual.where(manual.notna() & manual.astype(bool), auto_cats)
    else:
        out["_acct_category"] = auto_cats
    # 生产成本等前缀强制不进损益，覆盖手工分类
    out["_acct_category"] = [
        apply_prefix_category(code, cat)
        for code, cat in zip(out["_acct"], out["_acct_category"], strict=False)
    ]

    out["_header_text"] = _safe_text(out, "凭证抬头摘要")
    out["_line_text"] = _safe_text(out, "文本") if "文本" in out.columns else _safe_text(out, "摘要")
    out["_combined_text"] = (out["_header_text"] + " " + out["_line_text"]).str.strip()

    customer_code = _safe_text(out, "客户")
    customer_name = _safe_text(out, "客户科目：姓名 1")
    vendor_code = _safe_text(out, "供应商编号") if "供应商编号" in out.columns else _safe_text(out, "供应商")
    vendor_name = _safe_text(out, "供应商科目：名称 1")
    customer_display = pd.Series([
        _display_party(code, name) for code, name in zip(customer_code, customer_name, strict=False)
    ], index=out.index)
    vendor_display = pd.Series([
        _display_party(code, name) for code, name in zip(vendor_code, vendor_name, strict=False)
    ], index=out.index)
    out["_customer_display"], out["_customer_source"] = _propagate_unique_party(out, customer_display)
    out["_vendor_display"], out["_vendor_source"] = _propagate_unique_party(out, vendor_display)

    reversal_cols = [c for c in ("反记账", "反记帐", "冲销标识") if c in out.columns]
    if reversal_cols:
        out["_reversal_text"] = out[reversal_cols].fillna("").astype(str).agg(" ".join, axis=1).str.strip()
    else:
        out["_reversal_text"] = ""

    return out


REQUIRED_ANALYSIS_COLUMNS = {
    "_acct", "_acct4", "_acct_category", "_month", "_amount_raw", "_amount_abs", "_dc",
    "_debit_amount", "_credit_amount", "_account_name", "_pnl_category", "_customer_display",
    "_amount_source", "_amount_sign_mode", "_currency_basis", "_amount_currency", "_customer_source", "_vendor_source",
}


def ensure_category(
    df: pd.DataFrame,
    *,
    category_overrides: dict[str, str] | None = None,
) -> pd.DataFrame:
    """给 raw DataFrame 补 _acct_category 列（已存在则原样返回）。"""
    if "_acct_category" in df.columns:
        return df
    return classify_dataframe(df, overrides=category_overrides or {})


# rule_engine 依赖的 PnL 分类 helper（与 add_analysis_columns 内逻辑一致）
pnl_category = _pnl_category


def ensure_analysis_columns(
    work: pd.DataFrame,
    *,
    category_overrides: dict[str, str] | None = None,
) -> pd.DataFrame:
    if REQUIRED_ANALYSIS_COLUMNS.issubset(work.columns):
        return work
    return add_analysis_columns(work, category_overrides=category_overrides)


QUALITY_REASON_LABELS = {
    "needs_mapping": "待补充分类",
    "missing_identity": "缺少科目身份",
    "intentional_exclusion": "系统口径排除",
    "confirmed_exclusion": "用户确认排除",
    "deferred": "暂缓决策",
}


def analysis_quality_summary(
    work: pd.DataFrame,
    *,
    classification_decisions: dict[str, dict[str, Any]] | None = None,
) -> dict[str, object]:
    """返回会影响图表可信度的核心数据质量指标。"""
    work = ensure_analysis_columns(work)
    total_amount = float(work["_amount_abs"].sum())
    decisions = classification_decisions or {}
    unclassified = work.loc[work["_acct_category"].eq("未分类")].copy()
    review_accounts: list[dict[str, object]] = []
    reason_totals: dict[str, dict[str, float | int | str]] = {
        key: {"label": label, "amount": 0.0, "row_count": 0, "account_count": 0}
        for key, label in QUALITY_REASON_LABELS.items()
    }
    if not unclassified.empty:
        grouped = (
            unclassified.groupby(["_acct", "_account_name"], dropna=False)
            .agg(amount=("_amount_abs", "sum"), row_count=("_acct", "size"))
            .reset_index()
            .sort_values("amount", ascending=False)
        )
        for _, row in grouped.iterrows():
            code = str(row["_acct"]).strip()
            name = str(row["_account_name"]).strip()
            decision = decisions.get(code) if isinstance(decisions.get(code), dict) else {}
            decision_kind = str((decision or {}).get("decision") or "")
            if decision_kind == "exclude":
                reason = "confirmed_exclusion"
            elif decision_kind == "defer":
                reason = "deferred"
            else:
                reason = uncategorized_reason(code, name)
            amount = float(row["amount"])
            row_count = int(row["row_count"])
            reason_totals[reason]["amount"] = float(reason_totals[reason]["amount"]) + amount
            reason_totals[reason]["row_count"] = int(reason_totals[reason]["row_count"]) + row_count
            reason_totals[reason]["account_count"] = int(reason_totals[reason]["account_count"]) + 1
            review_accounts.append({
                "account_code": code,
                "account_name": name,
                "amount": amount,
                "amount_ratio": amount / total_amount if total_amount else 0.0,
                "row_count": row_count,
                "reason": reason,
                "reason_label": QUALITY_REASON_LABELS[reason],
                "mapping_allowed": reason not in {"intentional_exclusion", "confirmed_exclusion"},
                "decision": decision_kind or None,
                "decision_category": (decision or {}).get("category"),
                "rationale": str((decision or {}).get("rationale") or ""),
            })
    for summary in reason_totals.values():
        summary["amount_ratio"] = (
            float(summary["amount"]) / total_amount if total_amount else 0.0
        )

    unclassified_amount = float(unclassified["_amount_abs"].sum()) if not unclassified.empty else 0.0
    review_required_amount = sum(
        float(reason_totals[key]["amount"])
        for key in ("needs_mapping", "missing_identity", "deferred")
    )
    excluded_amount = sum(
        float(reason_totals[key]["amount"])
        for key in ("intentional_exclusion", "confirmed_exclusion")
    )
    currencies = sorted({
        str(value).strip().upper()
        for value in work["_amount_currency"].dropna().astype(str)
        if str(value).strip() and str(value) != "未维护"
    })
    currency_basis = str(work["_currency_basis"].iat[0]) if not work.empty else ""
    currency_distribution: list[dict[str, object]] = []
    if not work.empty and "_amount_currency" in work.columns:
        voucher_col = (
            work["凭证编号"].fillna("").astype(str)
            if "凭证编号" in work.columns
            else pd.Series("", index=work.index)
        )
        distribution = (
            work.assign(
                _quality_currency=(
                    work["_amount_currency"].fillna("未维护").astype(str).str.strip().str.upper()
                ),
                _quality_voucher=voucher_col,
            )
            .groupby("_quality_currency", dropna=False)
            .agg(
                row_count=("_amount_abs", "size"),
                voucher_count=("_quality_voucher", "nunique"),
                absolute_entry_amount=("_amount_abs", "sum"),
            )
            .reset_index()
            .sort_values("absolute_entry_amount", ascending=False)
        )
        currency_distribution = [
            {
                "currency": str(row["_quality_currency"]),
                "row_count": int(row["row_count"]),
                "voucher_count": int(row["voucher_count"]),
                "absolute_entry_amount": float(row["absolute_entry_amount"]),
            }
            for _, row in distribution.iterrows()
        ]
    mixed_document_currency = currency_basis == "document" and len(currencies) > 1
    return {
        "amount_source": str(work["_amount_source"].iat[0]) if not work.empty else "",
        "amount_sign_mode": str(work["_amount_sign_mode"].iat[0]) if not work.empty else "",
        "amount_sign_confidence": float(work["_amount_sign_confidence"].iat[0]) if not work.empty else 0.0,
        "currency_basis": currency_basis,
        "currencies": currencies,
        "mixed_document_currency": mixed_document_currency,
        "amounts_comparable": not mixed_document_currency,
        "currency_distribution": currency_distribution,
        "total_absolute_entry_amount": total_amount,
        "unclassified_amount": unclassified_amount,
        "unclassified_amount_ratio": unclassified_amount / total_amount if total_amount else 0.0,
        "review_required_amount": review_required_amount,
        "review_required_amount_ratio": review_required_amount / total_amount if total_amount else 0.0,
        "excluded_amount": excluded_amount,
        "excluded_amount_ratio": excluded_amount / total_amount if total_amount else 0.0,
        "reason_breakdown": reason_totals,
        "review_accounts": review_accounts,
    }
