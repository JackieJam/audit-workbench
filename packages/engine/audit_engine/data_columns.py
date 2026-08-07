"""Derived analysis columns for journal work DataFrames."""

from __future__ import annotations

from typing import Any

import pandas as pd

from audit_engine.account_classifier import (
    apply_prefix_category,
    auto_classify,
    classify_dataframe,
    is_system_protected_category,
    uncategorized_reason,
)

AMOUNT_MODE_SIGNED = "signed_raw"
AMOUNT_MODE_DC_MULTIPLIER = "dc_multiplier"
VOUCHER_KEY_COLUMN = "_voucher_key"
LINE_KEY_COLUMN = "_line_key"

_MISSING_COMPANY = "__NO_COMPANY__"
_MISSING_YEAR = "__NO_YEAR__"


def _identity_text(values: pd.Series) -> pd.Series:
    """Normalize identifier text without turning missing values into the string ``nan``."""
    text = values.astype("string").fillna("").str.strip()
    text = text.mask(text.str.lower().isin({"nan", "none", "<na>", "nat"}), "")
    return text.str.replace(r"\.0$", "", regex=True)


def _identity_year(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Return (year, source), preferring fiscal year over derived/posting year."""
    year = pd.Series("", index=df.index, dtype="string")
    source = pd.Series("missing", index=df.index, dtype="string")

    for column, label in (("会计年度", "fiscal_year"), ("_year", "derived_year")):
        if column not in df.columns:
            continue
        numeric = pd.to_numeric(df[column], errors="coerce").round().astype("Int64")
        candidate = numeric.astype("string").fillna("")
        fill = year.eq("") & candidate.ne("")
        year.loc[fill] = candidate.loc[fill]
        source.loc[fill] = label

    if "过账日期" in df.columns:
        posting_year = pd.to_datetime(df["过账日期"], errors="coerce").dt.year.astype("Int64")
        candidate = posting_year.astype("string").fillna("")
        fill = year.eq("") & candidate.ne("")
        year.loc[fill] = candidate.loc[fill]
        source.loc[fill] = "posting_date"
    return year, source


def ensure_voucher_identity(df: pd.DataFrame) -> pd.DataFrame:
    """Attach stable voucher and line identities.

    Voucher numbers are only unique inside a company and fiscal year.  Missing
    company code is an explicit degradation.  Missing year is more severe: the
    key falls back to a row-scoped token so rows can never silently merge across
    years.
    """
    if df.empty:
        out = df.copy()
        for column in (
            VOUCHER_KEY_COLUMN,
            LINE_KEY_COLUMN,
            "_voucher_key_basis",
            "_voucher_year_source",
            "_voucher_identity_quality",
        ):
            if column not in out.columns:
                out[column] = pd.Series(dtype="string")
        return out

    out = df.copy()
    row_number = pd.Series(range(len(out)), index=out.index, dtype="int64")
    voucher = (
        _identity_text(out["凭证编号"])
        if "凭证编号" in out.columns
        else pd.Series("", index=out.index, dtype="string")
    )
    company = (
        _identity_text(out["公司代码"])
        if "公司代码" in out.columns
        else pd.Series("", index=out.index, dtype="string")
    )
    year, year_source = _identity_year(out)

    missing_voucher = voucher.eq("")
    missing_year = year.eq("")
    safe_voucher = voucher.mask(missing_voucher, "__ROW_" + row_number.astype(str))
    safe_company = company.mask(company.eq(""), _MISSING_COMPANY)
    # A row-scoped missing-year token deliberately prevents accidental grouping.
    safe_year = year.mask(missing_year, _MISSING_YEAR + "_ROW_" + row_number.astype(str))

    generated_key = (
        "VK|"
        + safe_company.str.replace("|", r"\|", regex=False)
        + "|"
        + safe_year.str.replace("|", r"\|", regex=False)
        + "|"
        + safe_voucher.str.replace("|", r"\|", regex=False)
    )
    if VOUCHER_KEY_COLUMN in out.columns:
        existing = _identity_text(out[VOUCHER_KEY_COLUMN])
        out[VOUCHER_KEY_COLUMN] = existing.where(existing.ne(""), generated_key)
    else:
        out[VOUCHER_KEY_COLUMN] = generated_key

    basis = pd.Series("company_fiscal_year_voucher", index=out.index, dtype="string")
    basis.loc[company.eq("")] = "fiscal_year_voucher"
    basis.loc[year_source.eq("posting_date") & company.ne("")] = "company_posting_year_voucher"
    basis.loc[year_source.eq("posting_date") & company.eq("")] = "posting_year_voucher"
    basis.loc[missing_year] = "row_fallback_missing_year"
    basis.loc[missing_voucher] = "row_fallback_missing_voucher"
    out["_voucher_key_basis"] = basis
    out["_voucher_year_source"] = year_source

    quality = pd.Series("complete", index=out.index, dtype="string")
    quality.loc[company.eq("")] = "missing_company"
    quality.loc[missing_year] = "missing_year"
    quality.loc[missing_voucher] = "missing_voucher"
    out["_voucher_identity_quality"] = quality

    signature_columns = [
        column
        for column in (
            VOUCHER_KEY_COLUMN,
            "_source_asset_id",
            "_source_file_hash",
            "_source_file",
            "_source_sheet",
            "_source_row",
            "过账日期",
            "凭证日期",
            "总账科目",
            "借/贷标识",
            "公司代码货币价值",
            "凭证货币价值",
            "文本",
            "页数",
        )
        if column in out.columns
    ]
    signature = out[signature_columns].copy()
    for column in signature.columns:
        signature[column] = signature[column].astype("string").fillna("")
    row_hash = pd.util.hash_pandas_object(signature, index=False).astype("uint64")
    hash_text = row_hash.map(lambda value: f"{int(value):016x}")
    occurrence = (
        pd.DataFrame({"voucher": out[VOUCHER_KEY_COLUMN], "hash": hash_text}, index=out.index)
        .groupby(["voucher", "hash"], sort=False, dropna=False)
        .cumcount()
        .astype(str)
    )
    generated_line_key = out[VOUCHER_KEY_COLUMN] + "|L|" + hash_text + "|" + occurrence
    if LINE_KEY_COLUMN in out.columns:
        existing_line = _identity_text(out[LINE_KEY_COLUMN])
        out[LINE_KEY_COLUMN] = existing_line.where(existing_line.ne(""), generated_line_key)
    else:
        out[LINE_KEY_COLUMN] = generated_line_key
    return out


def _usable_amount_column(df: pd.DataFrame) -> str | None:
    """Choose one amount basis; never select an entirely empty placeholder column."""
    for column in ("公司代码货币价值", "凭证货币价值"):
        if column not in df.columns:
            continue
        if pd.to_numeric(df[column], errors="coerce").notna().any():
            return column
    return None


def collect_analysis_currencies(frames: Any) -> set[str]:
    """汇总多份分析帧中的有效 `_amount_currency`（排除空/未维护）。"""
    currencies: set[str] = set()
    for df in frames:
        if df is None or getattr(df, "empty", True):
            continue
        if "_amount_currency" not in df.columns:
            continue
        for value in df["_amount_currency"].dropna():
            text = str(value).strip().upper()
            if text and text not in {"未维护", "NAN", "NONE", "(空)"}:
                currencies.add(text)
    return currencies


def audit_input_quality(df: pd.DataFrame) -> dict[str, Any]:
    """Describe the minimum input gate for formal journal rule analysis."""
    total = int(len(df))
    issues: list[dict[str, Any]] = []
    if total == 0:
        return {"status": "blocked", "row_count": 0, "usable_rows": 0, "issues": [
            {"code": "empty_population", "severity": "blocking", "message": "审计总体为空"},
        ]}

    work = ensure_voucher_identity(df)
    voucher_key = (
        _identity_text(work[VOUCHER_KEY_COLUMN])
        if VOUCHER_KEY_COLUMN in work.columns
        else _identity_text(work["凭证编号"]) if "凭证编号" in work.columns
        else pd.Series("", index=work.index, dtype="string")
    )
    voucher = (
        _identity_text(work["凭证编号"])
        if "凭证编号" in work.columns
        else pd.Series("", index=work.index, dtype="string")
    )
    year, _ = _identity_year(work)
    posting_date = (
        pd.to_datetime(work["过账日期"], errors="coerce")
        if "过账日期" in work.columns
        else pd.Series(pd.NaT, index=work.index)
    )
    dc = (
        work["借/贷标识"].astype("string").fillna("").str.strip()
        if "借/贷标识" in work.columns
        else pd.Series("", index=work.index, dtype="string")
    )
    amount_col = _usable_amount_column(work)
    amount_valid = (
        pd.to_numeric(work[amount_col], errors="coerce").notna()
        if amount_col
        else pd.Series(False, index=work.index)
    )
    masks = {
        "voucher": voucher.ne(""),
        "year": year.ne(""),
        "posting_date": posting_date.notna(),
        "dc": dc.isin(["S", "H"]),
        "amount": amount_valid,
        "account": (
            _identity_text(work["总账科目"]).ne("")
            if "总账科目" in work.columns
            else pd.Series(False, index=work.index)
        ),
    }
    labels = {
        "voucher": ("missing_voucher_id", "凭证编号"),
        "year": ("missing_year_source", "会计年度或可解析过账日期"),
        "posting_date": ("missing_posting_date", "可解析过账日期"),
        "dc": ("missing_debit_credit", "借/贷标识（S/H）"),
        "amount": ("missing_usable_amount", "可用金额"),
        "account": ("missing_account", "总账科目"),
    }
    for key, mask in masks.items():
        missing_count = int((~mask).sum())
        code, label = labels[key]
        if int(mask.sum()) == 0:
            issues.append({
                "code": code,
                "severity": "blocking",
                "message": f"缺少{label}，正式规则分析已阻断",
                "row_count": missing_count,
            })
        elif missing_count:
            issues.append({
                "code": code,
                "severity": "warning",
                "message": f"{missing_count} 行缺少{label}，已从正式规则总体排除",
                "row_count": missing_count,
            })

    if "公司代码" not in work.columns or _identity_text(
        work.get("公司代码", pd.Series("", index=work.index))
    ).eq("").any():
        issues.append({
            "code": "missing_company_code",
            "severity": "warning",
            "message": "公司代码缺失的行使用显式占位符降级，无法区分同年度多主体同号凭证",
        })

    currency_dist: dict[str, int] = {}
    # 币种不变量：无论 document / company basis，分析金额币种 > 1 即阻断合并
    currency_series = None
    if amount_col == "公司代码货币价值" and "公司代码货币代码" in work.columns:
        currency_series = work.loc[amount_valid, "公司代码货币代码"]
    elif amount_col == "凭证货币价值" and "凭证货币代码" in work.columns:
        currency_series = work.loc[amount_valid, "凭证货币代码"]
    elif "_amount_currency" in work.columns:
        currency_series = work.loc[amount_valid, "_amount_currency"]

    if currency_series is not None:
        currencies = {
            str(value).strip().upper()
            for value in currency_series.dropna()
            if str(value).strip() and str(value).strip().lower() not in {"nan", "none", "未维护"}
        }
        currency_dist = (
            currency_series.astype("string").fillna("").str.strip().str.upper()
            .replace({"": "(空)", "NAN": "(空)", "NONE": "(空)", "未维护": "(空)"})
            .value_counts()
            .astype(int)
            .to_dict()
        )
        if len(currencies) > 1:
            basis_label = "本位币" if amount_col == "公司代码货币价值" else "凭证币"
            issues.append({
                "code": "mixed_analysis_currencies",
                "severity": "blocking",
                "message": (
                    f"检测到多种{basis_label} {sorted(currencies)}，"
                    "禁止直接合并金额分析（需选择单一报告币种或提供汇率折算）"
                ),
            })
            if amount_col == "凭证货币价值":
                # 兼容旧调用方对 mixed_document_currencies 的检查
                issues.append({
                    "code": "mixed_document_currencies",
                    "severity": "blocking",
                    "message": (
                        f"检测到多种凭证币 {sorted(currencies)} 且未使用本位币金额，"
                        "禁止合并规则分析"
                    ),
                })

    company_dist: dict[str, int] = {}
    if "公司代码" in work.columns:
        company_dist = (
            _identity_text(work["公司代码"])
            .replace({"": "(空)"})
            .value_counts()
            .astype(int)
            .head(20)
            .to_dict()
        )
        if len(company_dist) > 1 and "(空)" in company_dist:
            issues.append({
                "code": "multi_company_incomplete_codes",
                "severity": "warning",
                "message": "多公司代码并存且存在空公司代码，金额/凭证唯一性可能失真",
            })

    # 借贷平衡粗检：按 voucher_key 汇总有符号金额
    unbalanced_vouchers = 0
    voucher_count = int(voucher_key[voucher_key.ne("")].nunique()) if voucher_key.ne("").any() else 0
    if amount_col and "借/贷标识" in work.columns and voucher_count > 0:
        try:
            signed = normalize_signed_amount(work[amount_col], work["借/贷标识"], voucher_key)
            balance = pd.DataFrame({"v": voucher_key, "amt": signed}).groupby("v", sort=False)["amt"].sum()
            unbalanced_vouchers = int((balance.abs() > 0.01).sum())
            unbalanced_rate = unbalanced_vouchers / max(len(balance), 1)
            if unbalanced_rate > 0.05:
                issues.append({
                    "code": "voucher_unbalanced",
                    "severity": "warning",
                    "message": (
                        f"{unbalanced_vouchers} 张凭证借贷不平衡"
                        f"（{unbalanced_rate:.1%}），请核对金额符号/借贷标识"
                    ),
                    "row_count": unbalanced_vouchers,
                })
        except Exception:
            pass

    # 来源文件字段完整率（多文件异构映射风险）
    source_file_coverage: dict[str, Any] = {}
    if "_source_file" in work.columns and amount_col:
        for src_file, grp in work.groupby("_source_file", dropna=False):
            amt_ok = int(pd.to_numeric(grp[amount_col], errors="coerce").notna().sum())
            source_file_coverage[str(src_file or "(unknown)")] = {
                "rows": int(len(grp)),
                "amount_non_null": amt_ok,
                "amount_fill_rate": round(amt_ok / max(len(grp), 1), 4),
            }
            if len(grp) > 0 and amt_ok / len(grp) < 0.5:
                issues.append({
                    "code": "source_file_amount_gap",
                    "severity": "blocking",
                    "message": (
                        f"来源文件 {src_file} 金额可用率仅 {amt_ok / len(grp):.0%}，"
                        f"疑似列映射静默漏数"
                    ),
                    "row_count": int(len(grp) - amt_ok),
                })

    usable = masks["voucher"] & masks["year"] & masks["dc"] & masks["amount"]
    usable &= masks["posting_date"] & masks["account"]
    blocked = any(issue["severity"] == "blocking" for issue in issues)
    status = "blocked" if blocked else ("degraded" if issues else "ready")
    return {
        "status": status,
        "row_count": total,
        "usable_rows": int(usable.sum()),
        "voucher_count": voucher_count,
        "amount_column": amount_col,
        "currency_distribution": currency_dist,
        "company_distribution": company_dist,
        "unbalanced_vouchers": unbalanced_vouchers,
        "source_file_coverage": source_file_coverage,
        "issues": issues,
    }


def prepare_rule_dataframe(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Apply the formal input gate and return only auditable rows."""
    quality = audit_input_quality(df)
    if quality["status"] == "blocked":
        messages = "；".join(str(item["message"]) for item in quality["issues"] if item["severity"] == "blocking")
        raise ValueError(messages or "规则分析输入不满足最低字段门禁")
    normalized = df.copy()
    normalized["过账日期"] = pd.to_datetime(
        normalized["过账日期"], errors="coerce"
    )
    work = ensure_analysis_columns(normalized)
    usable = (
        _identity_text(work["凭证编号"]).ne("")
        & _identity_year(work)[0].ne("")
        & pd.to_datetime(work["过账日期"], errors="coerce").notna()
        & work["借/贷标识"].astype("string").fillna("").str.strip().isin(["S", "H"])
        & work["_amount_available"].fillna(False).astype(bool)
        & _identity_text(work["总账科目"]).ne("")
    )
    return work.loc[usable].copy(), quality


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
    if VOUCHER_KEY_COLUMN not in df.columns and "凭证编号" not in df.columns:
        return original, pd.Series("line", index=df.index)

    group_cols = [VOUCHER_KEY_COLUMN] if VOUCHER_KEY_COLUMN in df.columns else ["凭证编号"]

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
    out = ensure_voucher_identity(df)
    out["_acct"] = out["总账科目"].astype(str).str.strip()
    out["_acct4"] = out["_acct"].str[:4]
    out["_month"] = out["过账日期"].dt.month
    if "过账期间" in out.columns:
        p13 = pd.to_numeric(out["过账期间"], errors="coerce").eq(13)
        out["_is_period13"] = p13
        out.loc[p13, "_month"] = 13
    amount_col = _usable_amount_column(out)
    if amount_col is None:
        raise ValueError("缺少可用金额：公司代码货币价值与凭证货币价值均无有效数值")
    out["_dc"] = out["借/贷标识"].astype(str).str.strip()
    voucher_ids = out[VOUCHER_KEY_COLUMN]
    numeric_amount = pd.to_numeric(out[amount_col], errors="coerce")
    out["_amount_available"] = numeric_amount.notna()
    amount_mode, amount_confidence = infer_amount_sign_mode(numeric_amount, out["_dc"], voucher_ids)
    normalized_amount = normalize_signed_amount(
        out[amount_col],
        out["_dc"],
        voucher_ids,
        mode=amount_mode,
    )
    out["_amount_raw"] = normalized_amount.where(out["_amount_available"])
    out["_amount_sign_mode"] = amount_mode
    out["_amount_sign_confidence"] = amount_confidence
    out["_amount_source"] = amount_col
    if amount_col == "公司代码货币价值":
        out["_currency_basis"] = "company"
        out["_amount_basis"] = "company_currency"
        out["_amount_currency"] = _safe_text(out, "公司代码货币代码").replace("", "未维护")
    else:
        out["_currency_basis"] = "document"
        out["_amount_basis"] = "document_currency"
        out["_amount_currency"] = _safe_text(out, "凭证货币代码").replace("", "未维护")
    out["_amount_abs"] = out["_amount_raw"].abs()
    out["_debit_amount"] = out["_amount_raw"].where(out["_dc"] == "S", 0)
    out["_credit_amount"] = out["_amount_raw"].where(out["_dc"] == "H", 0)
    out["_debit_abs"] = out["_amount_abs"].where(out["_dc"] == "S", 0)
    out["_credit_abs"] = out["_amount_abs"].where(out["_dc"] == "H", 0)
    for column in ("_debit_amount", "_credit_amount", "_debit_abs", "_credit_abs"):
        out[column] = out[column].where(out["_amount_available"])
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
    # 明确会计语义由系统保护；错误的历史人工覆盖只保留审计轨迹，不再改变有效分类。
    out["_acct_category"] = pd.Series([
        apply_prefix_category(code, cat)
        for code, cat in zip(out["_acct"], out["_acct_category"], strict=False)
    ], index=out.index)
    protected = auto_cats.map(is_system_protected_category)
    out["_acct_category"] = out["_acct_category"].where(~protected, auto_cats)

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
    "_amount_basis", "_amount_available", VOUCHER_KEY_COLUMN, LINE_KEY_COLUMN,
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
    "system_corrected": "系统已纠正",
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
    if not work.empty:
        grouped = (
            work.groupby(["_acct", "_account_name", "_acct_category"], dropna=False)
            .agg(amount=("_amount_abs", "sum"), row_count=("_acct", "size"))
            .reset_index()
            .sort_values("amount", ascending=False)
        )
        for _, row in grouped.iterrows():
            code = str(row["_acct"]).strip()
            name = str(row["_account_name"]).strip()
            effective_category = str(row["_acct_category"]).strip()
            decision = decisions.get(code) if isinstance(decisions.get(code), dict) else {}
            decision_kind = str((decision or {}).get("decision") or "")
            recommended_category = apply_prefix_category(code, auto_classify(name))
            corrected = (
                decision_kind in {"map", "exclude", "defer"}
                and is_system_protected_category(recommended_category)
                and (
                    decision_kind != "map"
                    or str((decision or {}).get("category") or "") != recommended_category
                )
            )
            if corrected:
                reason = "system_corrected"
            elif effective_category != "未分类":
                continue
            elif decision_kind == "exclude":
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
                "mapping_allowed": reason not in {
                    "intentional_exclusion",
                    "confirmed_exclusion",
                    "system_corrected",
                },
                "recommended_category": recommended_category,
                "effective_category": effective_category,
                "system_corrected": corrected,
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
            work[VOUCHER_KEY_COLUMN].fillna("").astype(str)
            if VOUCHER_KEY_COLUMN in work.columns
            else (
                work["凭证编号"].fillna("").astype(str)
                if "凭证编号" in work.columns
                else pd.Series("", index=work.index)
            )
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
    mixed_analysis_currency = len(currencies) > 1
    return {
        "amount_source": str(work["_amount_source"].iat[0]) if not work.empty else "",
        "amount_sign_mode": str(work["_amount_sign_mode"].iat[0]) if not work.empty else "",
        "amount_sign_confidence": float(work["_amount_sign_confidence"].iat[0]) if not work.empty else 0.0,
        "currency_basis": currency_basis,
        "currencies": currencies,
        "mixed_document_currency": mixed_document_currency,
        "mixed_analysis_currency": mixed_analysis_currency,
        "amounts_comparable": not mixed_analysis_currency,
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
