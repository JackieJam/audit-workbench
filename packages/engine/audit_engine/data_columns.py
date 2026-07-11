"""Derived analysis columns for journal work DataFrames."""

from __future__ import annotations

import pandas as pd

from audit_engine.account_classifier import apply_prefix_category, auto_classify, classify_dataframe


def normalize_signed_amount(raw: pd.Series, dc: pd.Series) -> pd.Series:
    """统一金额符号：S（借）为正、H（贷）为负。"""
    values = pd.to_numeric(raw, errors="coerce").fillna(0.0)
    side = dc.astype(str).str.strip()
    abs_amt = values.abs()
    signed = abs_amt.where(side == "S", 0.0)
    signed = signed.where(side != "H", -abs_amt)
    unknown = ~side.isin(["S", "H"])
    return signed.where(~unknown, values)


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
    out["_amount_raw"] = normalize_signed_amount(out[amount_col], out["_dc"])
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
    out["_customer_display"] = [
        _display_party(code, name) for code, name in zip(customer_code, customer_name, strict=False)
    ]
    out["_vendor_display"] = [
        _display_party(code, name) for code, name in zip(vendor_code, vendor_name, strict=False)
    ]

    reversal_cols = [c for c in ("反记账", "反记帐", "冲销标识") if c in out.columns]
    if reversal_cols:
        out["_reversal_text"] = out[reversal_cols].fillna("").astype(str).agg(" ".join, axis=1).str.strip()
    else:
        out["_reversal_text"] = ""

    return out


REQUIRED_ANALYSIS_COLUMNS = {
    "_acct", "_acct4", "_acct_category", "_month", "_amount_raw", "_amount_abs", "_dc",
    "_debit_amount", "_credit_amount", "_account_name", "_pnl_category", "_customer_display",
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
