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
from audit_engine.data_columns import ensure_category


@dataclass
class CrossYearFinding:
    category: str          # 异常类型
    description: str       # 具体描述
    years_involved: list[int]
    voucher_ids: list[str]
    amount: float
    severity: str          # "高" | "中" | "低"
    evidence: dict[str, Any] = field(default_factory=dict)



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
    "balance_buildup_growth_ratio": 1.5,          # 期末余额逐年累积的增幅倍数
    "circular_large_amount": 500_000.0,           # 对手方资金循环单笔大额线
    "circular_match_ratio": 0.7,                  # 进出金额匹配度
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
        year: ensure_category(df.copy(), category_overrides=overrides)
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
# 1. 预提-冲回跨年配对
# ─────────────────────────────────────────────

def _accrual_reversal_pairs(
    year_map: dict[int, pd.DataFrame],
    coverage_threshold: float = _DEFAULT_COVERAGE_THRESHOLD,
    match_window_days: int = _DEFAULT_MATCH_WINDOW_DAYS,
    min_amount: float = _CROSS_YEAR_DETECTION_DEFAULTS["accrual_min_amount"],
    mismatch_tolerance: float = _CROSS_YEAR_DETECTION_DEFAULTS["accrual_mismatch_tolerance"],
    high_severity_amount: float = _CROSS_YEAR_DETECTION_DEFAULTS["accrual_high_severity_amount"],
) -> list[CrossYearFinding]:
    findings = []
    years = sorted(year_map.keys())

    for i in range(len(years) - 1):
        yr_n, yr_n1 = years[i], years[i + 1]
        df_n = year_map[yr_n]
        df_n1 = year_map[yr_n1]

        # 年末预提：12月，文本含"预提"，非冲销
        dec_accruals = df_n[
            (df_n["过账日期"].dt.month == 12)
            & df_n["文本"].str.contains("预提", na=False)
            & ~df_n["文本"].str.contains("冲销|冲回|红字", na=False)
        ].copy()

        if dec_accruals.empty:
            continue

        window_days = max(int(match_window_days), 1)
        window_start = pd.Timestamp(f"{yr_n1}-01-01")
        window_end = window_start + pd.Timedelta(days=window_days - 1)
        window_label = f"{yr_n1}年1月1日起{window_days}天内"

        # 次年指定窗口内冲回：文本含"冲销"或"冲回"
        reversals = df_n1[
            (df_n1["过账日期"] >= window_start)
            & (df_n1["过账日期"] <= window_end)
            & df_n1["文本"].str.contains("冲销|冲回|红字", na=False)
        ].copy()

        total_accrual = dec_accruals["凭证货币价值"].abs().sum()
        total_reversal = reversals["凭证货币价值"].abs().sum() if not reversals.empty else 0.0

        if total_accrual < min_amount:
            continue

        coverage = total_reversal / total_accrual if total_accrual > 0 else 0
        unmatched = total_accrual - total_reversal

        # 悬空预提（冲回覆盖率低于阈值）
        if coverage < coverage_threshold:
            findings.append(CrossYearFinding(
                category="预提冲回配对",
                description=f"{yr_n}年末预提{total_accrual:,.0f}，{window_label}仅冲回{total_reversal:,.0f}（{coverage:.0%}），悬空{unmatched:,.0f}",
                years_involved=[yr_n, yr_n1],
                voucher_ids=dec_accruals["凭证编号"].unique().tolist(),
                amount=unmatched,
                severity="高" if unmatched > high_severity_amount else "中",
                evidence={
                    "accrual_amount": round(total_accrual, 2),
                    "reversal_amount": round(total_reversal, 2),
                    "coverage_ratio": round(coverage, 4),
                    "threshold_used": coverage_threshold,  # 审计留痕：实际生效阈值
                    "match_window_days": window_days,
                },
            ))
        # 金额不等的冲回（调节利润）
        elif abs(coverage - 1.0) > mismatch_tolerance:
            findings.append(CrossYearFinding(
                category="预提冲回金额不符",
                description=f"{yr_n}年末预提与{window_label}冲回金额差异{abs(coverage-1):.0%}，疑似调节跨年损益",
                years_involved=[yr_n, yr_n1],
                voucher_ids=dec_accruals["凭证编号"].unique().tolist(),
                amount=abs(total_accrual - total_reversal),
                severity="中",
                evidence={"coverage_ratio": round(coverage, 4), "match_window_days": window_days},
            ))

    return findings


# ─────────────────────────────────────────────
# 2. 收入确认时点漂移
# ─────────────────────────────────────────────

def _revenue_timing_drift(
    year_map: dict[int, pd.DataFrame],
    dec_multiplier: float = _DEFAULT_DEC_MULTIPLIER,
) -> list[CrossYearFinding]:
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
        monthly = rev.groupby("_month")["凭证货币价值"].apply(lambda x: x.abs().sum())
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
            jan_red = df_n1[
                (df_n1["过账日期"].dt.month == 1)
                & df_n1["_acct_category"].eq(CAT_REVENUE)
                & (df_n1["凭证货币价值"] < 0)
            ]
            if not jan_red.empty:
                red_amt = jan_red["凭证货币价值"].abs().sum()
                findings.append(CrossYearFinding(
                    category="收入跨年确认",
                    description=f"{yr_n}年12月收入是前11月均值的{ratio_n:.1f}倍，且{yr_n1}年1月出现红字冲回{red_amt:,.0f}，疑似提前确认收入",
                    years_involved=[yr_n, yr_n1],
                    voucher_ids=jan_red["凭证编号"].unique().tolist(),
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
# 3. 期末余额异常堆积（应收/预付/其他应收）
# ─────────────────────────────────────────────

def _yearend_balance_buildup(
    year_map: dict[int, pd.DataFrame],
    growth_ratio: float = _CROSS_YEAR_DETECTION_DEFAULTS["balance_buildup_growth_ratio"],
) -> list[CrossYearFinding]:
    findings = []
    # 跟踪应收类科目的期末余额异常堆积。命中分类 = 自动从科目名称识别。
    watch_categories = {
        "应收账款": CAT_AR,
        "其他应收": CAT_OTHER_RECEIVABLE,
    }

    for acct_name, category in watch_categories.items():
        year_end_balances: dict[int, float] = {}
        year_avg_balances: dict[int, float] = {}

        for yr, df in year_map.items():
            df = ensure_category(df)
            acct_rows = df[df["_acct_category"].eq(category)]
            if acct_rows.empty:
                continue
            dec_rows = acct_rows[acct_rows["过账日期"].dt.month == 12]
            year_end_balances[yr] = float(dec_rows["凭证货币价值"].abs().sum())
            year_avg_balances[yr] = float(acct_rows["凭证货币价值"].abs().sum() / 12)

        if len(year_end_balances) < 2:
            continue

        # 检测余额逐年递增
        bal_list = [(yr, year_end_balances[yr]) for yr in sorted(year_end_balances.keys())]
        if all(bal_list[i][1] < bal_list[i+1][1] for i in range(len(bal_list)-1)):
            last_yr, last_bal = bal_list[-1]
            first_yr, first_bal = bal_list[0]
            if first_bal > 0 and last_bal / first_bal > growth_ratio:
                findings.append(CrossYearFinding(
                    category="期末余额持续累积",
                    description=f"{acct_name}年末余额从{first_yr}到{last_yr}持续增长，累计增幅{last_bal/first_bal:.1f}x，疑似虚增资产或收入造假积累",
                    years_involved=list(year_end_balances.keys()),
                    voucher_ids=[],
                    amount=last_bal - first_bal,
                    severity="中",
                    evidence={yr: round(b, 2) for yr, b in bal_list},
                ))

    return findings


# ─────────────────────────────────────────────
# 4. 对手方跨年资金循环
# ─────────────────────────────────────────────

def _counterparty_circular_flow(
    year_map: dict[int, pd.DataFrame],
    large_amount: float = _CROSS_YEAR_DETECTION_DEFAULTS["circular_large_amount"],
    match_ratio: float = _CROSS_YEAR_DETECTION_DEFAULTS["circular_match_ratio"],
) -> list[CrossYearFinding]:
    findings = []
    years = sorted(year_map.keys())

    for i in range(len(years) - 1):
        yr_n, yr_n1 = years[i], years[i + 1]
        df_n = year_map[yr_n]
        df_n1 = year_map[yr_n1]

        # Year N 年末大额支出
        dec_large_out = df_n[
            (df_n["过账日期"].dt.month == 12)
            & (df_n["凭证货币价值"] < -large_amount)
            & df_n["供应商编号"].notna()
        ]

        if dec_large_out.empty:
            continue

        # Year N+1 年初同供应商大额收入（反向）
        q1_large_in = df_n1[
            (df_n1["过账日期"].dt.month <= 3)
            & (df_n1["凭证货币价值"] > large_amount)
            & df_n1["供应商编号"].notna()
        ]

        if q1_large_in.empty:
            continue

        overlap_vendors = set(dec_large_out["供应商编号"]) & set(q1_large_in["供应商编号"])
        for vendor in overlap_vendors:
            out_amt = dec_large_out[dec_large_out["供应商编号"] == vendor]["凭证货币价值"].abs().sum()
            in_amt = q1_large_in[q1_large_in["供应商编号"] == vendor]["凭证货币价值"].abs().sum()
            ratio = min(out_amt, in_amt) / max(out_amt, in_amt) if max(out_amt, in_amt) > 0 else 0

            if ratio > match_ratio:
                vids = (
                    dec_large_out[dec_large_out["供应商编号"] == vendor]["凭证编号"].tolist()
                    + q1_large_in[q1_large_in["供应商编号"] == vendor]["凭证编号"].tolist()
                )
                vendor_name = dec_large_out[dec_large_out["供应商编号"] == vendor]["供应商科目：名称 1"].iloc[0] if "供应商科目：名称 1" in dec_large_out.columns else vendor
                findings.append(CrossYearFinding(
                    category="对手方跨年资金循环",
                    description=f"供应商{vendor_name}：{yr_n}年末付出{out_amt:,.0f}，{yr_n1}年Q1收回{in_amt:,.0f}（匹配度{ratio:.0%}），疑似资金空转",
                    years_involved=[yr_n, yr_n1],
                    voucher_ids=list(set(vids)),
                    amount=(out_amt + in_amt) / 2,
                    severity="高",
                    evidence={"vendor": str(vendor), "out_amount": round(out_amt, 2), "in_amount": round(in_amt, 2)},
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
            year_totals[yr] = float(rows["凭证货币价值"].abs().sum())

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
    for yr, df in year_map.items():
        if "凭证类型" not in df.columns or df.empty:
            continue
        manual = (~df["凭证类型"].isin(AUTO_VOUCHER_TYPES)).sum()
        year_ratios[yr] = round(manual / len(df), 4)

    if len(year_ratios) < 2:
        return findings

    ratios = [(yr, year_ratios[yr]) for yr in sorted(year_ratios.keys())]
    # 逐年上升且末年比首年高 15ppt
    if all(ratios[i][1] <= ratios[i+1][1] for i in range(len(ratios)-1)):
        delta = ratios[-1][1] - ratios[0][1]
        if delta > delta_threshold:
            findings.append(CrossYearFinding(
                category="手工凭证占比持续上升",
                description=f"手工凭证占比从{ratios[0][0]}年的{ratios[0][1]:.1%}逐年上升至{ratios[-1][0]}年的{ratios[-1][1]:.1%}，内控可能在弱化",
                years_involved=[r[0] for r in ratios],
                voucher_ids=[],
                amount=0,
                severity="中",
                evidence={str(yr): round(r, 4) for yr, r in ratios},
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
