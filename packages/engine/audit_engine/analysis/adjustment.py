"""调账冲销分析 — 关键词匹配凭证摘要。"""

from __future__ import annotations

import re
from collections.abc import Iterable

import pandas as pd

from audit_engine.config.accounts import DEFAULT_ADJUSTMENT_KEYWORDS
from audit_engine.data_columns import ensure_analysis_columns
from audit_engine.analysis.entry_display import entry_display_columns


def _keyword_pattern(keywords: Iterable[str]) -> str:
    escaped = [re.escape(str(k).strip()) for k in keywords if str(k).strip()]
    return "|".join(escaped)


def adjustment_summary(
    work: pd.DataFrame,
    keywords: Iterable[str] | None = None,
    max_vouchers: int = 300,
) -> pd.DataFrame:
    work = ensure_analysis_columns(work)
    kws = list(keywords) if keywords else list(DEFAULT_ADJUSTMENT_KEYWORDS)
    pattern = _keyword_pattern(kws)
    if not pattern:
        return pd.DataFrame()

    text_hit = work["_combined_text"].str.contains(pattern, na=False, regex=True)
    reversal_hit = work["_reversal_text"].str.strip().astype(bool)
    hit_rows = work[text_hit | reversal_hit].copy()
    if hit_rows.empty:
        return pd.DataFrame()

    hit_keys = hit_rows[["凭证编号", "过账日期"]].drop_duplicates()
    detail = work.merge(hit_keys, on=["凭证编号", "过账日期"], how="inner")

    def _matched_words(s: pd.Series) -> str:
        found: list[str] = []
        for text in s.dropna().astype(str):
            for kw in kws:
                if kw and kw in text and kw not in found:
                    found.append(str(kw))
        return "、".join(found[:8])

    agg_kwargs: dict = {
        "行数": ("凭证编号", "size"),
        "凭证类型": ("凭证类型", lambda x: "、".join(sorted({str(v) for v in x.dropna()}))),
        "命中关键词": ("_combined_text", _matched_words),
        "反记账标识": ("_reversal_text", lambda x: "、".join(sorted({v for v in x.astype(str) if v.strip() and v != "nan"}))),
        "借方金额": ("_debit_abs", "sum"),
        "贷方金额": ("_credit_abs", "sum"),
        "最大行金额": ("_amount_abs", "max"),
        "凭证抬头摘要": ("_header_text", "first"),
        "摘要": ("_line_text", lambda x: " | ".join(dict.fromkeys([str(v) for v in x.dropna() if str(v).strip()]))[:240]),
    }
    if "用户名" in detail.columns:
        agg_kwargs["用户名"] = ("用户名", lambda x: "、".join(sorted({str(v) for v in x.dropna()}))[:120])

    summary = detail.groupby(["凭证编号", "过账日期"]).agg(**agg_kwargs).reset_index()
    summary["过账日期"] = pd.to_datetime(summary["过账日期"], errors="coerce").dt.strftime("%Y-%m-%d")
    return summary.sort_values(["过账日期", "最大行金额"], ascending=[False, False]).head(max_vouchers)


def adjustment_voucher_entries(
    work: pd.DataFrame,
    voucher_id: str,
    voucher_date: str | None = None,
) -> pd.DataFrame:
    work = ensure_analysis_columns(work)
    detail = work[work["凭证编号"].astype(str) == str(voucher_id)].copy()
    if voucher_date and "过账日期" in detail.columns:
        detail = detail[
            pd.to_datetime(detail["过账日期"], errors="coerce").dt.strftime("%Y-%m-%d") == str(voucher_date)[:10]
        ].copy()
    if detail.empty:
        return pd.DataFrame()
    detail["发生额"] = detail["_amount_raw"]
    detail = detail.sort_values("_amount_abs", ascending=False)
    return entry_display_columns(detail, "发生额")
