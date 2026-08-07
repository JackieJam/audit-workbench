"""
跨年交叉稽核模块：对多年数据执行七类跨年异常检测。
每类返回 CrossYearFinding 列表，供 LLM 规则校准和可视化使用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from audit_engine.account_classifier import (
    CAT_AR,
    CAT_EXPENSE,
    CAT_FINANCIAL_EXPENSE,
    CAT_OTHER_RECEIVABLE,
    CAT_RD_EXPENSE,
    CAT_REVENUE,
    CAT_TAX_SURCHARGE,
)
from audit_engine.config.accounts import AUTO_VOUCHER_TYPES
from audit_engine.data_columns import (
    VOUCHER_KEY_COLUMN,
    ensure_category,
    ensure_voucher_identity,
    normalize_signed_amount,
)


@dataclass
class CrossYearFinding:
    category: str          # 异常类型
    description: str       # 具体描述
    years_involved: list[int]
    voucher_ids: list[str]
    amount: float
    severity: str          # "高" | "中" | "低"
    evidence: dict[str, Any] = field(default_factory=dict)
    voucher_keys: list[str] = field(default_factory=list)


def _amount_abs(df: pd.DataFrame) -> pd.Series:
    if "_amount_abs" in df.columns:
        return pd.to_numeric(df["_amount_abs"], errors="coerce")
    return pd.to_numeric(df["凭证货币价值"], errors="coerce").abs()


def _amount_raw(df: pd.DataFrame) -> pd.Series:
    if "_amount_raw" in df.columns:
        return pd.to_numeric(df["_amount_raw"], errors="coerce")
    return pd.to_numeric(df["凭证货币价值"], errors="coerce")



# 跨年检测阈值的默认值——与 config/default_rules.json 保持一致。
# 此处仅作兜底；正常路径下由 rules_config 传入，实现"调阈值真生效"。
# coverage_threshold / dec_multiplier 由 UI 规则卡（cross_year_accrual/revenue）暴露；
# 其余为高级检测阈值，集中在 rules_config["cross_year_detection"]，消除全部散落硬编码。
_DEFAULT_COVERAGE_THRESHOLD = 0.80
_DEFAULT_MATCH_WINDOW_DAYS = 90
_DEFAULT_DEC_MULTIPLIER = 1.8

_CROSS_YEAR_DETECTION_DEFAULTS: dict[str, float] = {
    "accrual_min_amount": 10_000.0,               # 预提金额下限，低于则忽略
    "accrual_mismatch_tolerance": 0.05,           # 冲回金额不符容差（|coverage-1|）
    "accrual_high_severity_amount": 1_000_000.0,  # 悬空金额高危分级线
    "balance_buildup_growth_ratio": 1.5,          # 应收累计净发生逐年累积的增幅倍数
    "circular_large_amount": 500_000.0,           # 对手方资金循环单笔大额线
    "circular_match_ratio": 0.7,                  # 进出金额匹配度
    "circular_max_vouchers": 15.0,                # 单对手方最多保留凭证数
    "expense_spike_multiplier": 2.5,              # 费用科目年度突变倍数
    "manual_entry_delta_threshold": 0.15,         # 手工凭证占比上升幅度（ppt）
    "new_pair_count_threshold": 20.0,             # 新科目组合数量阈值
}


def _cross_year_thresholds(cfg: dict | None) -> dict[str, float]:
    """从 rules_config 提取**全部**跨年检测阈值；缺省回落默认值。

    - coverage_threshold / dec_multiplier 来自 UI 规则卡（cross_year_accrual/revenue）。
    - 其余高级阈值来自 cfg["cross_year_detection"]，集中管理、消除散落硬编码
      （符合 CLAUDE.md「不在代码里硬编码任何阈值」）。
    返回的完整 dict 既驱动检测，又用于构造缓存键（任一变更即失效重算）。
    """
    cfg = cfg or {}
    accrual = cfg.get("cross_year_accrual") or {}
    revenue = cfg.get("cross_year_revenue") or {}
    detection = cfg.get("cross_year_detection") or {}
    out = {
        "coverage_threshold": float(accrual.get("coverage_threshold", _DEFAULT_COVERAGE_THRESHOLD)),
        "match_window_days": float(accrual.get("match_window_days", _DEFAULT_MATCH_WINDOW_DAYS)),
        "dec_multiplier": float(revenue.get("dec_multiplier", _DEFAULT_DEC_MULTIPLIER)),
    }
    for key, default in _CROSS_YEAR_DETECTION_DEFAULTS.items():
        out[key] = float(detection.get(key, default))
    return out


def _thresholds_signature(thresholds: dict[str, float]) -> tuple[tuple[str, float], ...]:
    """把阈值快照成可哈希的稳定签名，作为缓存键的一部分（只含标量）。"""
    return tuple(sorted(thresholds.items()))


def _run_cross_year_impl(
    year_map: dict[int, pd.DataFrame],
    overrides: dict[str, str],
    thresholds: dict[str, float],
) -> list[CrossYearFinding]:
    """执行七类跨年检测。"""
    prepared = {
        year: ensure_voucher_identity(ensure_category(df.copy(), category_overrides=overrides))
        for year, df in year_map.items()
    }
    t = thresholds

    findings: list[CrossYearFinding] = []
    findings.extend(_accrual_reversal_pairs(
        prepared,
        coverage_threshold=t["coverage_threshold"],
        match_window_days=int(t["match_window_days"]),
        min_amount=t["accrual_min_amount"],
        mismatch_tolerance=t["accrual_mismatch_tolerance"],
        high_severity_amount=t["accrual_high_severity_amount"],
    ))
    findings.extend(_revenue_timing_drift(prepared, dec_multiplier=t["dec_multiplier"]))
    findings.extend(_yearend_balance_buildup(
        prepared, growth_ratio=t["balance_buildup_growth_ratio"]))
    findings.extend(_counterparty_circular_flow(
        prepared,
        large_amount=t["circular_large_amount"],
        match_ratio=t["circular_match_ratio"],
        max_vouchers=int(t.get("circular_max_vouchers", 15)),
    ))
    findings.extend(_expense_category_spike(
        prepared, spike_multiplier=t["expense_spike_multiplier"]))
    findings.extend(_manual_entry_trend(
        prepared, delta_threshold=t["manual_entry_delta_threshold"]))
    findings.extend(_account_relationship_drift(
        prepared, new_pair_count_threshold=int(t["new_pair_count_threshold"])))
    return findings


def run_cross_year_analysis(
    year_map: dict[int, pd.DataFrame],
    cfg: dict | None = None,
    *,
    category_overrides: dict[str, str] | None = None,
) -> list[CrossYearFinding]:
    """执行全部跨年稽核，返回所有发现。

    结果按 (年度数据, 分类覆盖签名, 阈值签名) 缓存，避免每次 Streamlit 交互
    都重算七类跨年勾稽。数据、分类覆盖或检测阈值变化时缓存自动失效。

    Args:
        year_map: 按年分片的序时账。
        cfg: rules_config，用于读取跨年检测阈值；为 None 时全部回落默认值。
    """
    if len(year_map) < 2:
        return []
    thresholds = _cross_year_thresholds(cfg)
    return _run_cross_year_impl(year_map, category_overrides or {}, thresholds)


# ─────────────────────────────────────────────
# 1. 预提-冲回跨年配对（逐笔匹配，非总额覆盖）
# ─────────────────────────────────────────────

def _party_key(row: pd.Series) -> str:
    for col in ("供应商编号", "客户", "成本中心"):
        if col not in row.index:
            continue
        value = row.get(col)
        if value is None or (isinstance(value, float) and pd.isna(value)):
            continue
        text = str(value).strip()
        if text and text.lower() not in {"nan", "none", "未维护", "0"}:
            return f"{col}:{text}"
    return ""


def _collapse_to_accrual_entities(lines: pd.DataFrame) -> pd.DataFrame:
    """把含「预提/冲回」文本的 journal line 折叠为经济预提实体（每凭证一条）。

    一张平衡凭证借贷双方摘要都写「预提」时，经济金额是单边（通常取暂估负债腿），
    绝不能把借贷两行金额相加翻倍。
    """
    if lines.empty:
        return lines.copy()

    work = ensure_voucher_identity(lines)
    group_col = VOUCHER_KEY_COLUMN if VOUCHER_KEY_COLUMN in work.columns else "凭证编号"
    entities: list[pd.Series] = []

    for _, grp in work.groupby(group_col, sort=False):
        dc = (
            grp["借/贷标识"].astype(str).str.strip()
            if "借/贷标识" in grp.columns
            else pd.Series("", index=grp.index)
        )
        acct = (
            grp["总账科目"].astype(str)
            if "总账科目" in grp.columns
            else pd.Series("", index=grp.index)
        )
        credit = grp.loc[dc.eq("H")]
        debit = grp.loc[dc.eq("S")]
        liability = credit.loc[acct.str.startswith("22")] if not credit.empty else credit

        if not liability.empty:
            selected = liability
            leg = "liability_credit"
        elif not credit.empty:
            selected = credit
            leg = "credit"
        elif not debit.empty:
            selected = debit
            leg = "debit"
        else:
            selected = grp
            leg = "all_lines"

        selected_amt = float(_amount_abs(selected).fillna(0).sum())
        # 若借贷两侧都进入候选，经济金额取较大单边（平衡凭证 = 单边金额）
        debit_amt = float(_amount_abs(debit).fillna(0).sum()) if not debit.empty else 0.0
        credit_amt = float(_amount_abs(credit).fillna(0).sum()) if not credit.empty else 0.0
        if debit_amt > 0 and credit_amt > 0:
            economic_amt = max(debit_amt, credit_amt)
            if credit_amt >= debit_amt and not credit.empty:
                selected = liability if not liability.empty else credit
                leg = "balanced_credit_leg"
            elif not debit.empty:
                selected = debit
                leg = "balanced_debit_leg"
            selected_amt = economic_amt

        row = selected.iloc[0].copy()
        # 把实体金额写回标准金额列，供后续配对使用
        for col in ("凭证货币价值", "公司代码货币价值", "_amount_abs", "_amount_raw"):
            if col in row.index or col in work.columns:
                row[col] = selected_amt
        row["_accrual_entity_leg"] = leg
        row["_accrual_entity_amount"] = selected_amt
        row["_accrual_line_count"] = int(len(grp))
        entities.append(row)

    if not entities:
        return lines.iloc[0:0].copy()
    return pd.DataFrame(entities)


def _match_accrual_reversals(
    accruals: pd.DataFrame,
    reversals: pd.DataFrame,
    *,
    amount_tolerance: float,
) -> tuple[list[dict[str, Any]], pd.DataFrame]:
    """一对一贪心配对：科目前缀 + 对手方 + 相反借贷 + 金额容差。

    输入先折叠为经济预提实体（每凭证一条），再配对。
    返回 (pairs, unmatched_accruals)。无关冲回不会“覆盖”未匹配预提。
    """
    if accruals.empty:
        return [], accruals.copy()

    accruals = _collapse_to_accrual_entities(accruals)
    reversals = _collapse_to_accrual_entities(reversals)
    work_rev = reversals.copy()
    if work_rev.empty:
        return [], accruals.copy()

    def _acct4(frame: pd.DataFrame) -> pd.Series:
        if "总账科目" in frame.columns:
            return frame["总账科目"].astype(str).str[:4]
        return pd.Series("", index=frame.index, dtype="string")

    def _dc(frame: pd.DataFrame) -> pd.Series:
        if "借/贷标识" in frame.columns:
            return frame["借/贷标识"].astype(str).str.strip()
        return pd.Series("", index=frame.index, dtype="string")

    work_rev["_match_amt"] = _amount_abs(work_rev).fillna(0)
    work_rev["_match_date"] = pd.to_datetime(work_rev["过账日期"], errors="coerce")
    work_rev["_acct4"] = _acct4(work_rev)
    work_rev["_party"] = work_rev.apply(_party_key, axis=1)
    work_rev["_dc"] = _dc(work_rev)
    used: set[Any] = set()
    pairs: list[dict[str, Any]] = []
    unmatched_idx: list[Any] = []

    ordered = accruals.assign(
        _match_amt=_amount_abs(accruals).fillna(0),
        _match_date=pd.to_datetime(accruals["过账日期"], errors="coerce"),
        _acct4=_acct4(accruals),
        _party=accruals.apply(_party_key, axis=1),
        _dc=_dc(accruals),
    ).sort_values("_match_amt", ascending=False)

    for idx, accrual in ordered.iterrows():
        amt = float(accrual["_match_amt"])
        if amt <= 0:
            unmatched_idx.append(idx)
            continue
        accrual_dc = str(accrual["_dc"])
        opposite = "H" if accrual_dc == "S" else "S"
        available = work_rev.loc[~work_rev.index.isin(used)].copy()
        candidates = available[available["_acct4"].eq(str(accrual["_acct4"]))].copy()
        if accrual_dc in {"S", "H"}:
            candidates = candidates[
                candidates["_dc"].eq(opposite) | candidates["_dc"].eq("")
            ]
        if candidates.empty:
            unmatched_idx.append(idx)
            continue
        # 先按金额容差过滤，再优先同对手方——避免「同对手方错误金额」挡住正确配对，
        # 也避免「不同对手方同金额」把无关冲回当成覆盖。
        candidates = candidates.assign(
            _tol=(candidates["_match_amt"] - amt).abs() / amt
        )
        amount_ok = candidates[candidates["_tol"].le(amount_tolerance)]
        if amount_ok.empty:
            unmatched_idx.append(idx)
            continue

        accrual_party = str(accrual["_party"])
        if accrual_party:
            same_party = amount_ok[amount_ok["_party"].eq(accrual_party)]
            if same_party.empty:
                # 预提有明确对手方时，不允许用其他对手方冲回“顶替”
                unmatched_idx.append(idx)
                continue
            pool = same_party
        else:
            pool = amount_ok

        best = pool.sort_values(["_tol", "_match_date"]).iloc[0]
        used.add(best.name)
        pairs.append({
            "accrual_voucher": str(accrual.get("凭证编号", "")),
            "accrual_key": str(accrual.get(VOUCHER_KEY_COLUMN, accrual.get("凭证编号", ""))),
            "reversal_voucher": str(best.get("凭证编号", "")),
            "reversal_key": str(best.get(VOUCHER_KEY_COLUMN, best.get("凭证编号", ""))),
            "accrual_amount": round(amt, 2),
            "reversal_amount": round(float(best["_match_amt"]), 2),
            "tolerance": round(float(best["_tol"]), 4),
            "account_prefix": str(accrual["_acct4"]),
            "party": accrual_party,
        })

    unmatched = accruals.loc[[i for i in unmatched_idx if i in accruals.index]].copy()
    return pairs, unmatched


def _accrual_reversal_pairs(
    year_map: dict[int, pd.DataFrame],
    coverage_threshold: float = _DEFAULT_COVERAGE_THRESHOLD,
    match_window_days: int = _DEFAULT_MATCH_WINDOW_DAYS,
    min_amount: float = _CROSS_YEAR_DETECTION_DEFAULTS["accrual_min_amount"],
    mismatch_tolerance: float = _CROSS_YEAR_DETECTION_DEFAULTS["accrual_mismatch_tolerance"],
    high_severity_amount: float = _CROSS_YEAR_DETECTION_DEFAULTS["accrual_high_severity_amount"],
) -> list[CrossYearFinding]:
    year_map = {year: ensure_voucher_identity(df) for year, df in year_map.items()}
    findings = []
    years = sorted(year_map.keys())
    # 金额容差：沿用 mismatch_tolerance（默认 5%），避免无关冲回“总额冲销”掩盖悬空预提
    amount_tolerance = max(float(mismatch_tolerance), 0.01)

    for i in range(len(years) - 1):
        yr_n, yr_n1 = years[i], years[i + 1]
        df_n = year_map[yr_n]
        df_n1 = year_map[yr_n1]

        # 年末预提行：12月，文本含"预提"，非冲销 —— 随后折叠为经济预提实体
        text_n = df_n["文本"].astype(str) if "文本" in df_n.columns else pd.Series("", index=df_n.index)
        dec_accrual_lines = df_n[
            (df_n["过账日期"].dt.month == 12)
            & text_n.str.contains("预提", na=False, regex=False)
            & ~text_n.str.contains("冲销|冲回|红字", na=False, regex=True)
        ].copy()
        dec_accruals = _collapse_to_accrual_entities(dec_accrual_lines)
        if not dec_accruals.empty:
            dec_accruals = dec_accruals[_amount_abs(dec_accruals).fillna(0).ge(min_amount)]

        if dec_accruals.empty:
            continue

        window_days = max(int(match_window_days), 1)
        window_start = pd.Timestamp(f"{yr_n1}-01-01")
        window_end = window_start + pd.Timedelta(days=window_days - 1)
        window_label = f"{yr_n1}年1月1日起{window_days}天内"

        text_n1 = df_n1["文本"].astype(str) if "文本" in df_n1.columns else pd.Series("", index=df_n1.index)
        reversal_lines = df_n1[
            (df_n1["过账日期"] >= window_start)
            & (df_n1["过账日期"] <= window_end)
            & text_n1.str.contains("冲销|冲回|红字", na=False, regex=True)
        ].copy()
        reversals = _collapse_to_accrual_entities(reversal_lines)

        pairs, unmatched = _match_accrual_reversals(
            dec_accruals, reversals, amount_tolerance=amount_tolerance,
        )
        total_accrual = float(_amount_abs(dec_accruals).sum())
        matched_amount = float(sum(p["accrual_amount"] for p in pairs))
        unmatched_amount = float(_amount_abs(unmatched).sum()) if not unmatched.empty else 0.0
        coverage = matched_amount / total_accrual if total_accrual > 0 else 0.0

        if total_accrual < min_amount:
            continue

        pair_sample = pairs[:8]
        unmatched_vids = (
            unmatched["凭证编号"].astype(str).unique().tolist()
            if not unmatched.empty and "凭证编号" in unmatched.columns
            else []
        )
        unmatched_keys = (
            unmatched[VOUCHER_KEY_COLUMN].astype(str).unique().tolist()
            if not unmatched.empty and VOUCHER_KEY_COLUMN in unmatched.columns
            else []
        )
        entity_count = int(len(dec_accruals))

        # 悬空预提：逐笔未匹配金额占比过高
        if coverage < coverage_threshold:
            findings.append(CrossYearFinding(
                category="预提冲回配对",
                description=(
                    f"{yr_n}年末预提{total_accrual:,.0f}（{entity_count}笔经济实体），"
                    f"{window_label}逐笔配对冲回{matched_amount:,.0f}（{len(pairs)}笔，覆盖{coverage:.0%}），"
                    f"未匹配悬空{unmatched_amount:,.0f}（{len(unmatched)}笔）"
                ),
                years_involved=[yr_n, yr_n1],
                voucher_ids=unmatched_vids or dec_accruals["凭证编号"].astype(str).unique().tolist(),
                voucher_keys=unmatched_keys or dec_accruals[VOUCHER_KEY_COLUMN].astype(str).unique().tolist(),
                amount=unmatched_amount,
                severity="高" if unmatched_amount > high_severity_amount else "中",
                evidence={
                    "accrual_amount": round(total_accrual, 2),
                    "matched_amount": round(matched_amount, 2),
                    "unmatched_amount": round(unmatched_amount, 2),
                    "coverage_ratio": round(coverage, 4),
                    "matched_pairs": len(pairs),
                    "unmatched_accruals": len(unmatched),
                    "pair_sample": pair_sample,
                    "threshold_used": coverage_threshold,
                    "match_window_days": window_days,
                    "amount_tolerance": amount_tolerance,
                    "pairing_mode": "economic_accrual_entity",
                    "source_line_count": int(len(dec_accrual_lines)),
                },
            ))
        elif abs(coverage - 1.0) > mismatch_tolerance and unmatched_amount > 0:
            findings.append(CrossYearFinding(
                category="预提冲回金额不符",
                description=(
                    f"{yr_n}年末预提与{window_label}逐笔配对后覆盖率{coverage:.0%}，"
                    f"未匹配{unmatched_amount:,.0f}，疑似调节跨年损益"
                ),
                years_involved=[yr_n, yr_n1],
                voucher_ids=unmatched_vids,
                voucher_keys=unmatched_keys,
                amount=unmatched_amount,
                severity="中",
                evidence={
                    "coverage_ratio": round(coverage, 4),
                    "match_window_days": window_days,
                    "pair_sample": pair_sample,
                    "pairing_mode": "economic_accrual_entity",
                },
            ))

    return findings


# ─────────────────────────────────────────────
# 2. 收入确认时点漂移
# ─────────────────────────────────────────────

def _revenue_timing_drift(
    year_map: dict[int, pd.DataFrame],
    dec_multiplier: float = _DEFAULT_DEC_MULTIPLIER,
) -> list[CrossYearFinding]:
    year_map = {year: ensure_voucher_identity(df) for year, df in year_map.items()}
    findings = []
    years = sorted(year_map.keys())

    year_dec_ratio: dict[int, float] = {}
    for yr, df in year_map.items():
        df = ensure_category(df)
        rev = df[df["_acct_category"].eq(CAT_REVENUE)]
        if rev.empty:
            continue
        rev = rev.copy()
        rev["_month"] = rev["过账日期"].dt.month
        rev["_rule_amount_abs"] = _amount_abs(rev)
        monthly = rev.groupby("_month")["_rule_amount_abs"].sum()
        if len(monthly) < 2:
            continue
        dec = float(monthly.get(12, 0))
        avg_other = float(monthly[monthly.index != 12].mean())
        year_dec_ratio[yr] = dec / avg_other if avg_other > 0 else 0

    for i in range(len(years) - 1):
        yr_n, yr_n1 = years[i], years[i + 1]
        ratio_n = year_dec_ratio.get(yr_n, 0)

        # 某年12月收入异常高，且次年1月出现大额红字
        if ratio_n > dec_multiplier:
            df_n1 = ensure_category(year_map[yr_n1])
            jan_amount = _amount_raw(df_n1)
            jan_red = df_n1[
                (df_n1["过账日期"].dt.month == 1)
                & df_n1["_acct_category"].eq(CAT_REVENUE)
                & jan_amount.lt(0)
            ]
            if not jan_red.empty:
                red_amt = _amount_abs(jan_red).sum()
                findings.append(CrossYearFinding(
                    category="收入跨年确认",
                    description=f"{yr_n}年12月收入是前11月均值的{ratio_n:.1f}倍，且{yr_n1}年1月出现红字冲回{red_amt:,.0f}，疑似提前确认收入",
                    years_involved=[yr_n, yr_n1],
                    voucher_ids=jan_red["凭证编号"].unique().tolist(),
                    voucher_keys=jan_red[VOUCHER_KEY_COLUMN].astype(str).unique().tolist(),
                    amount=red_amt,
                    severity="高",
                    evidence={
                        "dec_ratio": round(ratio_n, 2),
                        "jan_reversal": round(red_amt, 2),
                        "threshold_used": dec_multiplier,  # 审计留痕：实际生效阈值
                    },
                ))

    return findings


# ─────────────────────────────────────────────
# 3. 应收累计净发生持续累积（无期初时的近似期末余额）
# ─────────────────────────────────────────────

def _yearend_balance_buildup(
    year_map: dict[int, pd.DataFrame],
    growth_ratio: float = _CROSS_YEAR_DETECTION_DEFAULTS["balance_buildup_growth_ratio"],
) -> list[CrossYearFinding]:
    findings = []
    # 序时账无期初余额：用「截至该年末的累计净发生额」近似期末余额。
    # 严禁把 12 月绝对发生额当成余额——借贷对冲后净额可能接近 0。
    watch_categories = {
        "应收账款": CAT_AR,
        "其他应收": CAT_OTHER_RECEIVABLE,
    }

    for acct_name, category in watch_categories.items():
        year_end_balances: dict[int, float] = {}
        active_years: list[int] = []
        cumulative = 0.0

        for yr in sorted(year_map.keys()):
            raw = year_map[yr]
            if raw is None or raw.empty:
                year_end_balances[yr] = cumulative
                continue
            df = ensure_category(raw)
            acct_rows = df[df["_acct_category"].eq(category)]
            if acct_rows.empty:
                year_end_balances[yr] = cumulative
                continue

            # 优先用已标准化的有符号金额，避免对 _amount_raw 再次做借贷归一化导致双重取反
            if "_amount_raw" in acct_rows.columns and pd.to_numeric(
                acct_rows["_amount_raw"], errors="coerce"
            ).notna().any():
                year_net = float(
                    pd.to_numeric(acct_rows["_amount_raw"], errors="coerce").fillna(0).sum()
                )
            else:
                if "公司代码货币价值" in acct_rows.columns and pd.to_numeric(
                    acct_rows["公司代码货币价值"], errors="coerce"
                ).notna().any():
                    amount_col = "公司代码货币价值"
                elif "凭证货币价值" in acct_rows.columns:
                    amount_col = "凭证货币价值"
                else:
                    year_end_balances[yr] = cumulative
                    continue

                if "借/贷标识" in acct_rows.columns:
                    year_net = float(
                        normalize_signed_amount(
                            acct_rows[amount_col],
                            acct_rows["借/贷标识"],
                            acct_rows["凭证编号"] if "凭证编号" in acct_rows.columns else None,
                        ).sum()
                    )
                else:
                    year_net = float(
                        pd.to_numeric(acct_rows[amount_col], errors="coerce").fillna(0).sum()
                    )
            cumulative += year_net
            year_end_balances[yr] = cumulative
            active_years.append(yr)

        if len(active_years) < 2:
            continue

        bal_list = [(yr, year_end_balances[yr]) for yr in sorted(year_end_balances.keys())]
        if all(bal_list[i][1] < bal_list[i + 1][1] for i in range(len(bal_list) - 1)):
            last_yr, last_bal = bal_list[-1]
            first_yr, first_bal = bal_list[0]
            if first_bal > 0 and last_bal / first_bal > growth_ratio:
                findings.append(CrossYearFinding(
                    category="应收累计净发生持续累积",
                    description=(
                        f"{acct_name}累计净发生额（无科目余额表期初时的近似期末余额）"
                        f"从{first_yr}到{last_yr}持续增长，累计增幅{last_bal / first_bal:.1f}x，"
                        f"疑似虚增资产或收入造假积累。"
                        f"注意：此指标不是12月发生额，也不是真正期末余额。"
                    ),
                    years_involved=list(year_end_balances.keys()),
                    voucher_ids=[],
                    amount=last_bal - first_bal,
                    severity="中",
                    evidence={
                        **{yr: round(b, 2) for yr, b in bal_list},
                        "metric": "cumulative_net_occurrence",
                        "opening_balance_available": False,
                    },
                ))

    return findings


# ─────────────────────────────────────────────
# 4. 对手方跨年资金循环
# ─────────────────────────────────────────────

def _counterparty_circular_flow(
    year_map: dict[int, pd.DataFrame],
    large_amount: float = _CROSS_YEAR_DETECTION_DEFAULTS["circular_large_amount"],
    match_ratio: float = _CROSS_YEAR_DETECTION_DEFAULTS["circular_match_ratio"],
    max_vouchers: int = 15,
) -> list[CrossYearFinding]:
    year_map = {year: ensure_voucher_identity(df) for year, df in year_map.items()}
    findings = []
    years = sorted(year_map.keys())
    max_vouchers = max(1, int(max_vouchers or 15))

    for i in range(len(years) - 1):
        yr_n, yr_n1 = years[i], years[i + 1]
        df_n = year_map[yr_n]
        df_n1 = year_map[yr_n1]

        # Year N 年末大额支出
        amount_n = _amount_raw(df_n)
        dec_large_out = df_n[
            (df_n["过账日期"].dt.month == 12)
            & amount_n.lt(-large_amount)
            & df_n["供应商编号"].notna()
        ]

        if dec_large_out.empty:
            continue

        # Year N+1 年初同供应商大额收入（反向）
        amount_n1 = _amount_raw(df_n1)
        q1_large_in = df_n1[
            (df_n1["过账日期"].dt.month <= 3)
            & amount_n1.gt(large_amount)
            & df_n1["供应商编号"].notna()
        ]

        if q1_large_in.empty:
            continue

        overlap_vendors = set(dec_large_out["供应商编号"]) & set(q1_large_in["供应商编号"])
        for vendor in overlap_vendors:
            # 空供应商会把无关大额流水汇总成一条“伪循环”，直接丢弃
            vendor_key = "" if vendor is None or (isinstance(vendor, float) and pd.isna(vendor)) else str(vendor).strip()
            if not vendor_key or vendor_key.lower() in {"nan", "none", "未维护", "0"}:
                continue

            out_rows = dec_large_out[dec_large_out["供应商编号"] == vendor]
            in_rows = q1_large_in[q1_large_in["供应商编号"] == vendor]
            out_amt = _amount_abs(out_rows).sum()
            in_amt = _amount_abs(in_rows).sum()
            ratio = min(out_amt, in_amt) / max(out_amt, in_amt) if max(out_amt, in_amt) > 0 else 0

            if ratio > match_ratio:
                # 按单行金额保留头部凭证，避免一个对手方拖入数百张无关凭证
                ranked = (
                    pd.concat([out_rows, in_rows], ignore_index=True)
                    .assign(
                        _vid=lambda d: d["凭证编号"].astype(str),
                        _vkey=lambda d: d[VOUCHER_KEY_COLUMN].astype(str),
                        _amt=lambda d: _amount_abs(d).fillna(0),
                    )
                    .groupby(["_vkey", "_vid"], sort=False)["_amt"].sum()
                    .sort_values(ascending=False)
                )
                ranked_head = ranked.head(max_vouchers)
                keys = [str(key) for key, _vid in ranked_head.index]
                vids = [str(vid) for _key, vid in ranked_head.index]
                vendor_name = (
                    out_rows["供应商科目：名称 1"].iloc[0]
                    if "供应商科目：名称 1" in out_rows.columns and len(out_rows)
                    else vendor_key
                )
                findings.append(CrossYearFinding(
                    category="对手方跨年资金循环",
                    description=f"供应商{vendor_name}：{yr_n}年末付出{out_amt:,.0f}，{yr_n1}年Q1收回{in_amt:,.0f}（匹配度{ratio:.0%}），疑似资金空转",
                    years_involved=[yr_n, yr_n1],
                    voucher_ids=vids,
                    voucher_keys=keys,
                    amount=(out_amt + in_amt) / 2,
                    severity="高",
                    evidence={
                        "vendor": vendor_key,
                        "out_amount": round(float(out_amt), 2),
                        "in_amount": round(float(in_amt), 2),
                        "voucher_cap": max_vouchers,
                        "voucher_total": int(len(ranked)),
                    },
                ))

    return findings


# ─────────────────────────────────────────────
# 5. 费用科目年度突变
# ─────────────────────────────────────────────

def _expense_category_spike(
    year_map: dict[int, pd.DataFrame],
    spike_multiplier: float = _CROSS_YEAR_DETECTION_DEFAULTS["expense_spike_multiplier"],
) -> list[CrossYearFinding]:
    findings = []
    years = sorted(year_map.keys())
    if len(years) < 2:
        return findings

    # 关注费用类（含费用 / 研发 / 财务费用 / 税金及附加）的年度突变。
    # 这里直接按"自动分类后的类别"分桶，跨年规模差异更直观。
    watch_categories = [
        ("费用", CAT_EXPENSE),
        ("研发费用", CAT_RD_EXPENSE),
        ("财务费用", CAT_FINANCIAL_EXPENSE),
        ("税金及附加", CAT_TAX_SURCHARGE),
    ]

    for label, category in watch_categories:
        year_totals: dict[int, float] = {}
        for yr, df in year_map.items():
            df = ensure_category(df)
            rows = df[df["_acct_category"].eq(category)]
            year_totals[yr] = float(_amount_abs(rows).sum())

        if len(year_totals) < 2 or all(v == 0 for v in year_totals.values()):
            continue

        totals = [(yr, year_totals[yr]) for yr in sorted(year_totals.keys())]
        for i in range(1, len(totals)):
            prev_yr, prev_amt = totals[i - 1]
            curr_yr, curr_amt = totals[i]
            if prev_amt > 0 and curr_amt / prev_amt > spike_multiplier:
                findings.append(CrossYearFinding(
                    category="费用科目年度突变",
                    description=f"{label}类：{curr_yr}年发生额{curr_amt:,.0f}，是{prev_yr}年{prev_amt:,.0f}的{curr_amt/prev_amt:.1f}倍，异常放量",
                    years_involved=[prev_yr, curr_yr],
                    voucher_ids=[],
                    amount=curr_amt - prev_amt,
                    severity="中",
                    evidence={"category": label, "prev_amount": round(prev_amt, 2), "curr_amount": round(curr_amt, 2)},
                ))

    return findings


# ─────────────────────────────────────────────
# 6. 手工凭证占比趋势
# ─────────────────────────────────────────────

def _manual_entry_trend(
    year_map: dict[int, pd.DataFrame],
    delta_threshold: float = _CROSS_YEAR_DETECTION_DEFAULTS["manual_entry_delta_threshold"],
) -> list[CrossYearFinding]:
    findings = []

    year_ratios: dict[int, float] = {}
    year_row_ratios: dict[int, float] = {}
    for yr, df in year_map.items():
        if "凭证类型" not in df.columns or df.empty:
            continue
        # 口径：按凭证数占比（不是行数）。一行多行分录不能当成多笔交易。
        if "凭证编号" in df.columns:
            voucher_types = (
                df.groupby("凭证编号", sort=False)["凭证类型"]
                .agg(lambda s: str(s.iloc[0]))
            )
            if voucher_types.empty:
                continue
            manual_vouchers = (~voucher_types.isin(AUTO_VOUCHER_TYPES)).sum()
            year_ratios[yr] = round(float(manual_vouchers / len(voucher_types)), 4)
        else:
            manual = (~df["凭证类型"].isin(AUTO_VOUCHER_TYPES)).sum()
            year_ratios[yr] = round(manual / len(df), 4)
        manual_rows = (~df["凭证类型"].isin(AUTO_VOUCHER_TYPES)).sum()
        year_row_ratios[yr] = round(float(manual_rows / len(df)), 4)

    if len(year_ratios) < 2:
        return findings

    ratios = [(yr, year_ratios[yr]) for yr in sorted(year_ratios.keys())]
    # 逐年上升且末年比首年高 15ppt
    if all(ratios[i][1] <= ratios[i+1][1] for i in range(len(ratios)-1)):
        delta = ratios[-1][1] - ratios[0][1]
        if delta > delta_threshold:
            findings.append(CrossYearFinding(
                category="手工凭证占比持续上升",
                description=(
                    f"手工凭证数占比从{ratios[0][0]}年的{ratios[0][1]:.1%}"
                    f"逐年上升至{ratios[-1][0]}年的{ratios[-1][1]:.1%}，内控可能在弱化"
                ),
                years_involved=[r[0] for r in ratios],
                voucher_ids=[],
                amount=0,
                severity="中",
                evidence={
                    **{str(yr): round(r, 4) for yr, r in ratios},
                    "basis": "voucher_count",
                    "row_ratios": {str(yr): year_row_ratios.get(yr) for yr, _ in ratios},
                },
            ))

    return findings


# ─────────────────────────────────────────────
# 7. 科目组合稳定性（借贷科目对）
# ─────────────────────────────────────────────

def _account_relationship_drift(
    year_map: dict[int, pd.DataFrame],
    new_pair_count_threshold: int = int(_CROSS_YEAR_DETECTION_DEFAULTS["new_pair_count_threshold"]),
) -> list[CrossYearFinding]:
    """检测某年出现大量历史从未出现过的新科目组合。"""
    findings = []
    years = sorted(year_map.keys())
    if len(years) < 2:
        return findings

    def _get_acct_pairs(df: pd.DataFrame) -> set[tuple[str, str]]:
        pairs = set()
        for _vid, grp in df.groupby("凭证编号"):
            accounts = grp["总账科目"].astype(str).str[:4].unique().tolist()
            accounts.sort()
            for i in range(len(accounts)):
                for j in range(i + 1, len(accounts)):
                    pairs.add((accounts[i], accounts[j]))
        return pairs

    historical_pairs: set[tuple[str, str]] = set()
    for i, yr in enumerate(years):
        current_pairs = _get_acct_pairs(year_map[yr])
        if i > 0:
            new_pairs = current_pairs - historical_pairs
            if len(new_pairs) > new_pair_count_threshold:
                findings.append(CrossYearFinding(
                    category="新科目组合涌现",
                    description=f"{yr}年出现{len(new_pairs)}个历史从未有过的科目借贷组合，可能是新业务通道或绕过内控的新做账方式",
                    years_involved=[years[i-1], yr],
                    voucher_ids=[],
                    amount=0,
                    severity="低",
                    evidence={"new_pair_count": len(new_pairs), "sample_pairs": list(new_pairs)[:5]},
                ))
        historical_pairs |= current_pairs

    return findings


def findings_to_summary_text(findings: list[CrossYearFinding]) -> str:
    """转为 LLM prompt 用的文本摘要。"""
    if not findings:
        return "未发现跨年异常。"

    lines = [f"发现 {len(findings)} 条跨年异常：\n"]
    for i, f in enumerate(findings, 1):
        lines.append(f"{i}. [{f.severity}] {f.category}（涉及年份：{f.years_involved}，金额：{f.amount:,.0f}）")
        lines.append(f"   {f.description}")
    return "\n".join(lines)
