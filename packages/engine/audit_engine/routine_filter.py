"""常规机械分录过滤 — 排除低抽样价值的法定/系统/结转类噪声。

问题类（不是单个科目）：
- 法定代扣代缴与福利（社保/公积金/个税等）
- 系统自动凭证与批量结转（ML/AF/差异结转）
- 常规财务机械分录（折旧摊销、银行手续费利息、汇兑调汇）

配置在 default_rules.json 的 routine_exclusion；
兼容旧版 whitelist_keywords / whitelist_voucher_types。
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

from audit_engine.config.accounts import AUTO_VOUCHER_TYPES
from audit_engine.data_columns import VOUCHER_KEY_COLUMN, ensure_voucher_identity

# selector.expense_category → 受保护的 routine category（钻取该费用类时不过滤）
_SELECTOR_PROTECTS: dict[str, set[str]] = {
    "人工": {"wage_salary"},
    "折旧摊销": {"depreciation_amortization"},
    "财务费用": {"fx_revaluation", "bank_interest_fees"},
    "财务费用(汇兑)": {"fx_revaluation", "bank_interest_fees"},
}

_ACCOUNT_NAME_COLS = (
    "总账科目：长文本",
    "总账科目：短文本",
    "总账科目:长文本",
    "总账科目:短文本",
    "科目名称",
    "_account_name",
)


def _as_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    return bool(value)


def routine_exclusion_cfg(cfg: dict | None) -> dict[str, Any]:
    """合并 routine_exclusion 与旧 whitelist_* 字段。"""
    root = dict(cfg or {})
    block = dict(root.get("routine_exclusion") or {})
    if not block and not root.get("whitelist_keywords") and not root.get("whitelist_voucher_types"):
        return {"enabled": False}

    categories = dict(block.get("categories") or {})
    legacy_kw = [str(k) for k in (root.get("whitelist_keywords") or []) if str(k).strip()]
    if legacy_kw and "legacy_whitelist" not in categories:
        categories["legacy_whitelist"] = {
            "account_patterns": list(legacy_kw),
            "text_patterns": list(legacy_kw),
            "exclude_from_rules": True,
            "exclude_from_candidates": True,
            "exclude_from_export_lines": True,
        }

    voucher_types = [
        str(v).strip()
        for v in (block.get("always_exclude_voucher_types") or root.get("whitelist_voucher_types") or [])
        if str(v).strip()
    ]
    if not voucher_types:
        voucher_types = sorted(AUTO_VOUCHER_TYPES)

    return {
        "enabled": _as_bool(block.get("enabled"), True),
        "categories": categories,
        "always_exclude_voucher_types": voucher_types,
        "always_exclude_account_patterns": list(block.get("always_exclude_account_patterns") or []),
        "max_vouchers_per_candidate_group": int(block.get("max_vouchers_per_candidate_group") or 50),
        "max_lines_per_voucher_in_export": int(block.get("max_lines_per_voucher_in_export") or 40),
        "export_skip_routine_lines": _as_bool(block.get("export_skip_routine_lines"), True),
        "drop_pure_routine_vouchers": _as_bool(block.get("drop_pure_routine_vouchers"), True),
        "circular_max_vouchers_per_finding": int(block.get("circular_max_vouchers_per_finding") or 15),
    }


def _account_name_series(df: pd.DataFrame) -> pd.Series:
    parts: list[pd.Series] = []
    for col in _ACCOUNT_NAME_COLS:
        if col in df.columns:
            parts.append(df[col].astype("string").fillna(""))
    if "总账科目" in df.columns:
        parts.append(df["总账科目"].astype("string").fillna(""))
    if not parts:
        return pd.Series([""] * len(df), index=df.index, dtype="string")
    out = parts[0]
    for part in parts[1:]:
        out = out + " " + part
    return out


def _text_series(df: pd.DataFrame) -> pd.Series:
    if "文本" not in df.columns:
        return pd.Series([""] * len(df), index=df.index, dtype="string")
    return df["文本"].astype("string").fillna("")


def _compile_patterns(patterns: list[str]) -> re.Pattern[str] | None:
    cleaned = [re.escape(str(p).strip()) for p in patterns if str(p).strip()]
    if not cleaned:
        return None
    return re.compile("|".join(cleaned))


def _protected_categories(selector: dict | None) -> set[str]:
    if not selector:
        return set()
    protected: set[str] = set()
    expense_cat = str(selector.get("expense_category") or "").strip()
    if expense_cat:
        protected |= set(_SELECTOR_PROTECTS.get(expense_cat, set()))
    # 显式保护列表
    for name in selector.get("protect_routine_categories") or []:
        if str(name).strip():
            protected.add(str(name).strip())
    return protected


def routine_line_mask(
    df: pd.DataFrame,
    cfg: dict | None,
    *,
    purpose: str = "rules",
    selector: dict | None = None,
) -> pd.Series:
    """返回 True = 常规噪声行，应排除。

    purpose: rules | candidates | export
    """
    re_cfg = routine_exclusion_cfg(cfg)
    if df.empty or not re_cfg.get("enabled", True):
        return pd.Series(False, index=df.index)

    mask = pd.Series(False, index=df.index)
    account = _account_name_series(df)
    text = _text_series(df)
    haystack = account + " " + text

    # 自动/结转类凭证类型
    vtypes = set(re_cfg.get("always_exclude_voucher_types") or [])
    if vtypes and "凭证类型" in df.columns:
        mask |= df["凭证类型"].astype(str).isin(vtypes)

    # 永远排除的科目模式（差异结转等）
    always_acct = _compile_patterns(list(re_cfg.get("always_exclude_account_patterns") or []))
    if always_acct is not None:
        mask |= account.str.contains(always_acct, na=False, regex=True)

    # 金额为 0
    for amount_col in ("_amount_raw", "公司代码货币价值", "凭证货币价值"):
        if amount_col in df.columns:
            mask |= pd.to_numeric(df[amount_col], errors="coerce").fillna(0).abs() == 0
            break

    protected = _protected_categories(selector)
    flag_key = {
        "rules": "exclude_from_rules",
        "candidates": "exclude_from_candidates",
        "export": "exclude_from_export_lines",
    }.get(purpose, "exclude_from_rules")

    for cat_name, cat_cfg in (re_cfg.get("categories") or {}).items():
        if not isinstance(cat_cfg, dict):
            continue
        if cat_name in protected:
            continue
        if not _as_bool(cat_cfg.get(flag_key), True):
            continue
        patterns = list(cat_cfg.get("account_patterns") or []) + list(cat_cfg.get("text_patterns") or [])
        compiled = _compile_patterns(patterns)
        if compiled is None:
            continue
        mask |= haystack.str.contains(compiled, na=False, regex=True)

    return mask.fillna(False)


def apply_routine_exclusion(
    df: pd.DataFrame,
    cfg: dict | None,
    *,
    purpose: str = "rules",
    selector: dict | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """返回 (保留, 排除)。

    purpose=rules 时**不按行删除**，避免破坏凭证完整性：
    仅剔除「纯常规凭证」（噪声行占比达阈值），混合凭证保留全部行。
    candidates/export 仍可按行排除常规分录。
    """
    if df.empty:
        return df.copy(), df.iloc[0:0].copy()

    if purpose == "rules":
        work = ensure_voucher_identity(df)
        pure_ids = pure_routine_voucher_ids(
            work, cfg, purpose="rules", selector=selector, stable=True,
        )
        if not pure_ids:
            return work.copy(), work.iloc[0:0].copy()
        keep = ~work[VOUCHER_KEY_COLUMN].astype(str).isin(pure_ids)
        return work.loc[keep].copy(), work.loc[~keep].copy()

    mask = routine_line_mask(df, cfg, purpose=purpose, selector=selector)
    return df[~mask].copy(), df[mask].copy()


def pure_routine_voucher_ids(
    df: pd.DataFrame,
    cfg: dict | None,
    *,
    purpose: str = "candidates",
    selector: dict | None = None,
    threshold: float = 0.8,
    stable: bool = False,
) -> set[str]:
    """噪声行占比 ≥ threshold 的凭证；stable=True 时返回稳定凭证键。"""
    if df.empty or "凭证编号" not in df.columns:
        return set()
    re_cfg = routine_exclusion_cfg(cfg)
    if not re_cfg.get("drop_pure_routine_vouchers", True):
        return set()

    work = ensure_voucher_identity(df) if stable else df
    mask = routine_line_mask(work, cfg, purpose=purpose, selector=selector)
    voucher_column = VOUCHER_KEY_COLUMN if stable else "凭证编号"
    tmp = pd.DataFrame({
        "凭证": work[voucher_column].astype(str),
        "noise": mask.astype(int),
    })
    stats = tmp.groupby("凭证", sort=False)["noise"].agg(["sum", "count"])
    ratio = stats["sum"] / stats["count"].clip(lower=1)
    return set(ratio[ratio >= threshold].index.astype(str))


def prepare_candidate_detail(
    detail: pd.DataFrame,
    cfg: dict | None,
    *,
    selector: dict | None = None,
) -> pd.DataFrame:
    """疑点入库前：去掉常规噪声行、纯噪声凭证，并按金额截断凭证数。"""
    if detail.empty:
        return detail

    re_cfg = routine_exclusion_cfg(cfg)
    if not re_cfg.get("enabled", True):
        return detail

    work = ensure_voucher_identity(detail)
    kept, _ = apply_routine_exclusion(work, cfg, purpose="candidates", selector=selector)
    if kept.empty:
        return kept

    pure_ids = pure_routine_voucher_ids(
        work,
        cfg,
        purpose="candidates",
        selector=selector,
        stable=True,
    )
    if pure_ids:
        kept = kept[~kept[VOUCHER_KEY_COLUMN].astype(str).isin(pure_ids)].copy()
    if kept.empty:
        return kept

    max_vouchers = int(re_cfg.get("max_vouchers_per_candidate_group") or 50)
    if max_vouchers > 0:
        amount_col = next(
            (c for c in ("_amount_abs", "费用发生额", "收入影响", "金额", "凭证货币价值", "公司代码货币价值")
             if c in kept.columns),
            None,
        )
        if amount_col:
            ranked = (
                kept.assign(
                    _vid=kept[VOUCHER_KEY_COLUMN].astype(str),
                    _amt=pd.to_numeric(
                        kept[amount_col], errors="coerce"
                    ).abs().fillna(0),
                )
                .groupby("_vid", sort=False)["_amt"]
                .sum()
                .sort_values(ascending=False)
            )
        else:
            ranked = (
                kept.assign(_vid=kept[VOUCHER_KEY_COLUMN].astype(str))
                .groupby("_vid", sort=False)
                .size()
                .sort_values(ascending=False)
            )
        keep_vids = set(ranked.head(max_vouchers).index.astype(str))
        kept = kept[kept[VOUCHER_KEY_COLUMN].astype(str).isin(keep_vids)].copy()

    return kept


def filter_export_voucher_rows(
    voucher_rows: pd.DataFrame,
    cfg: dict | None,
    *,
    has_rule_hit: bool = False,
) -> pd.DataFrame:
    """导出时压缩凭证分录：去掉常规噪声行，并限制行数。"""
    if voucher_rows.empty:
        return voucher_rows

    re_cfg = routine_exclusion_cfg(cfg)
    rows = voucher_rows
    if re_cfg.get("enabled", True) and re_cfg.get("export_skip_routine_lines", True):
        kept, excluded = apply_routine_exclusion(rows, cfg, purpose="export")
        # 若过滤后为空但仍有规则命中，保留金额最大的若干非空行以免样本空洞
        if kept.empty and has_rule_hit:
            amount_col = next(
                (c for c in ("_amount_abs", "_amount_raw", "凭证货币价值", "公司代码货币价值") if c in rows.columns),
                None,
            )
            if amount_col:
                rows = rows.assign(_sort=pd.to_numeric(rows[amount_col], errors="coerce").abs().fillna(0))
                rows = rows.sort_values("_sort", ascending=False).head(5)
            else:
                rows = rows.head(5)
        else:
            rows = kept

    max_lines = int(re_cfg.get("max_lines_per_voucher_in_export") or 40)
    if max_lines > 0 and len(rows) > max_lines:
        amount_col = next(
            (c for c in ("_amount_abs", "_amount_raw", "凭证货币价值", "公司代码货币价值") if c in rows.columns),
            None,
        )
        if amount_col:
            rows = rows.assign(_sort=pd.to_numeric(rows[amount_col], errors="coerce").abs().fillna(0))
            rows = rows.sort_values("_sort", ascending=False).head(max_lines)
        else:
            rows = rows.head(max_lines)
    return rows


def prioritize_voucher_ids_for_export(
    voucher_ids: list[str],
    *,
    hit_voucher_ids: set[str] | None = None,
    max_size: int | None = None,
) -> list[str]:
    """导出凭证排序：规则命中优先，再截断 max_size。"""
    ordered = list(dict.fromkeys(str(v).strip() for v in voucher_ids if str(v).strip()))
    if not ordered:
        return []
    hits = {str(v) for v in (hit_voucher_ids or set())}
    if hits:
        primary = [v for v in ordered if v in hits]
        secondary = [v for v in ordered if v not in hits]
        ordered = primary + secondary
    if max_size and max_size > 0:
        return ordered[:max_size]
    return ordered
