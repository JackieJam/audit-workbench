"""
规则执行引擎：从 rules_config 读参数，对统一 DataFrame 执行全部规则。
修复原版已知问题：
- 大额整数按凭证去重（不再每行重复触发）
- 手工凭证 elif 改为独立条件叠加
- 化整为零阈值从 config 读取
- 跨年规则由 cross_year_findings 驱动，直接转为 RuleHit
"""

from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from audit_engine.account_classifier import (
    CAT_COST,
    CAT_REVENUE,
)
from audit_engine.data_columns import ensure_category
from audit_engine.data_columns import pnl_category as _pnl_category


@dataclass(frozen=True)
class RuleHit:
    voucher_id: str
    rule_type: str
    evidence: str
    line_indices: tuple[int, ...]
    priority: int = 1          # 1-5，数字越大优先级越高
    year: int | None = None
    group_id: str | None = None
    related_voucher_ids: tuple[str, ...] = ()
    relation_evidence: str = ""


@dataclass
class RuleResult:
    rule_name: str
    hits: list[RuleHit] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.hits)


# ─────────────────────────────────────────────
# 白名单过滤
# ─────────────────────────────────────────────

def apply_whitelist(df: pd.DataFrame, cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """排除常规机械分录（科目名+文本+自动凭证类型）。兼容旧 whitelist_* 字段。"""
    from audit_engine.routine_filter import apply_routine_exclusion

    return apply_routine_exclusion(df, cfg, purpose="rules")


# ─────────────────────────────────────────────
# 辅助函数
# ─────────────────────────────────────────────

def _month_end_day(date: pd.Timestamp) -> int:
    import calendar
    return calendar.monthrange(date.year, date.month)[1]


def _is_last_n_days(date: pd.Timestamp, n: int) -> bool:
    return date.day > (_month_end_day(date) - n)


def _is_holiday_vectorized(dates: pd.Series) -> pd.Series:
    """向量化节假日检测（替代逐行 is_holiday）。"""
    try:
        import chinese_calendar
        y_min, y_max = int(dates.dt.year.min()), int(dates.dt.year.max())
        holidays = chinese_calendar.get_holidays(range(y_min, y_max + 2))
        holiday_set = {pd.Timestamp(h) for h in holidays}
        return dates.isin(holiday_set)
    except Exception:
        return dates.dt.dayofweek >= 5


def _is_round(amount: float, threshold: float) -> bool:
    abs_amt = abs(amount)
    return abs_amt >= threshold and abs_amt == int(abs_amt) and int(abs_amt) % 10_000 == 0


def _clean_fact_text(values: pd.Series) -> pd.Series:
    cleaned = values.astype("string").str.strip()
    return cleaned.mask(cleaned.str.lower().isin(["", "nan", "未维护"]))


def _text_terms(text: str) -> set[str]:
    common_terms = {
        "主营业务", "其他业务", "收入", "成本", "销售", "结转", "凭证", "过账",
        "发票", "客户", "供应商", "传统", "业务",
    }
    terms = set()
    for term in re.findall(r"[A-Za-z0-9]{2,}|[\u4e00-\u9fff]{2,}", text.lower()):
        if term not in common_terms:
            terms.add(term)
    return terms


def _business_base(category: str) -> str:
    if not category:
        return ""
    return str(category).split("-", 1)[0]


def _build_trade_voucher_facts(
    df: pd.DataFrame,
) -> pd.DataFrame:
    """构建凭证级事实表，给融资性贸易规则用。

    收入/成本判定基于 _acct_category（自动分类），不再依赖前缀清单。
    """
    work = ensure_category(df).copy()
    work["凭证编号"] = work["凭证编号"].astype(str)
    work["_acct4"] = work["总账科目"].astype(str).str[:4]
    work["_dc"] = work["借/贷标识"].astype(str).str.strip()
    work["_amount_abs"] = pd.to_numeric(work["凭证货币价值"], errors="coerce").fillna(0).abs()

    income_mask = work["_acct_category"].eq(CAT_REVENUE) & (work["_dc"] == "H")
    cost_mask = work["_acct_category"].eq(CAT_COST) & (work["_dc"] == "S")

    trade_ids = set(work.loc[income_mask | cost_mask, "凭证编号"])
    if not trade_ids:
        return pd.DataFrame()

    income_amount = work.loc[income_mask].groupby("凭证编号")["_amount_abs"].sum()
    cost_amount = work.loc[cost_mask].groupby("凭证编号")["_amount_abs"].sum()
    income_lines = {
        str(vid): tuple(indices.tolist())
        for vid, indices in work.loc[income_mask].groupby("凭证编号").groups.items()
    }
    cost_lines = {
        str(vid): tuple(indices.tolist())
        for vid, indices in work.loc[cost_mask].groupby("凭证编号").groups.items()
    }

    source_columns = [
        "凭证编号", "过账日期", "_acct4", "_year", "总账科目：长文本",
        "凭证抬头摘要", "文本", "客户", "客户科目：姓名 1", "供应商编号",
        "供应商科目：名称 1", "用户名", "凭证类型",
    ]
    source = work.loc[work["凭证编号"].isin(trade_ids), [
        col for col in source_columns if col in work.columns
    ]].copy()
    for col in source_columns:
        if col not in source.columns:
            source[col] = pd.NA
    for col in source_columns:
        if col not in {"凭证编号", "过账日期", "_year"}:
            source[col] = _clean_fact_text(source[col])

    grouped = source.groupby("凭证编号", sort=False)
    facts = grouped.first().reset_index()
    facts["date"] = facts["凭证编号"].map(grouped["过账日期"].min())
    all_lines = {
        str(vid): tuple(indices.tolist())
        for vid, indices in grouped.groups.items()
    }

    def display_party(code: Any, name: Any) -> str:
        code_text = "" if pd.isna(code) else str(code)
        name_text = "" if pd.isna(name) else str(name)
        return f"{code_text} - {name_text}" if code_text and name_text else code_text or name_text

    facts["text"] = (
        facts["凭证抬头摘要"].fillna("").astype(str)
        + " "
        + facts["文本"].fillna("").astype(str)
    ).str.strip()
    facts["terms"] = facts["text"].map(_text_terms)
    facts["customer"] = [
        display_party(code, name)
        for code, name in zip(facts["客户"], facts["客户科目：姓名 1"], strict=True)
    ]
    facts["vendor"] = [
        display_party(code, name)
        for code, name in zip(facts["供应商编号"], facts["供应商科目：名称 1"], strict=True)
    ]
    facts["user"] = facts["用户名"].fillna("").astype(str)
    facts["voucher_type"] = facts["凭证类型"].fillna("").astype(str)
    facts["category"] = [
        _pnl_category("" if pd.isna(code) else str(code), "" if pd.isna(name) else str(name))
        for code, name in zip(facts["_acct4"], facts["总账科目：长文本"], strict=True)
    ]
    facts["revenue_amount"] = facts["凭证编号"].map(income_amount).fillna(0.0).astype(float)
    facts["cost_amount"] = facts["凭证编号"].map(cost_amount).fillna(0.0).astype(float)
    def tuple_or_empty(value: Any) -> tuple[int, ...]:
        return value if isinstance(value, tuple) else ()

    facts["income_line_indices"] = facts["凭证编号"].map(income_lines).map(tuple_or_empty)
    facts["cost_line_indices"] = facts["凭证编号"].map(cost_lines).map(tuple_or_empty)
    facts["all_line_indices"] = facts["凭证编号"].map(all_lines).map(tuple_or_empty)
    facts["voucher_id"] = facts["凭证编号"]
    facts["year"] = pd.to_numeric(facts["_year"], errors="coerce").fillna(facts["date"].dt.year)
    return facts[[
        "voucher_id", "date", "year", "text", "terms", "customer", "vendor", "user",
        "voucher_type", "category", "revenue_amount", "cost_amount", "income_line_indices",
        "cost_line_indices", "all_line_indices",
    ]]


def _score_trade_relation(rev: pd.Series, cost: pd.Series, window_days: int) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []

    if not isinstance(rev["date"], pd.Timestamp) or not isinstance(cost["date"], pd.Timestamp):
        return score, reasons

    day_gap = abs((cost["date"] - rev["date"]).days)
    if day_gap > window_days:
        return score, reasons

    if day_gap <= 3:
        score += 0.20
        reasons.append(f"日期相差{day_gap}天")
    elif day_gap <= 7:
        score += 0.15
        reasons.append(f"日期相差{day_gap}天")
    elif day_gap <= 14:
        score += 0.10
        reasons.append(f"日期相差{day_gap}天")
    else:
        score += 0.05
        reasons.append(f"日期相差{day_gap}天")

    party_pairs = [
        (rev.get("customer", ""), cost.get("customer", "")),
        (rev.get("customer", ""), cost.get("vendor", "")),
        (rev.get("vendor", ""), cost.get("customer", "")),
        (rev.get("vendor", ""), cost.get("vendor", "")),
    ]
    if any(left and right and left == right for left, right in party_pairs):
        score += 0.35
        reasons.append("对手方一致")

    shared_terms = sorted(set(rev.get("terms", set())) & set(cost.get("terms", set())))
    if shared_terms:
        score += 0.20
        reasons.append(f"文本共享关键词：{'/'.join(shared_terms[:3])}")

    if _business_base(rev.get("category", "")) and _business_base(rev.get("category", "")) == _business_base(cost.get("category", "")):
        score += 0.15
        reasons.append(f"业务类别同为{_business_base(rev.get('category', ''))}")

    revenue_amount = float(rev.get("revenue_amount", 0.0))
    cost_amount = float(cost.get("cost_amount", 0.0))
    amount_ratio = cost_amount / revenue_amount if revenue_amount > 0 else 0.0
    if 0.80 <= amount_ratio <= 1.20:
        score += 0.20
        reasons.append(f"成本/收入比{amount_ratio:.0%}")
    elif 0.60 <= amount_ratio <= 1.40:
        score += 0.15
        reasons.append(f"成本/收入比{amount_ratio:.0%}")
    elif 0.40 <= amount_ratio <= 1.60:
        score += 0.10
        reasons.append(f"成本/收入比{amount_ratio:.0%}")

    return min(score, 1.0), reasons


# ─────────────────────────────────────────────
# Rule 1: 化整为零
# ─────────────────────────────────────────────

def rule_splitting(df: pd.DataFrame, cfg: dict) -> RuleResult:
    c = cfg.get("splitting", {})
    max_single: float = c.get("max_single_amount", 100_000)
    min_total: float = c.get("min_total", 500_000)
    window_days: int = c.get("window_days", 14)
    min_count: int = c.get("min_txn_count", 5)

    result = RuleResult(rule_name="化整为零")
    if not c.get("enabled", True):
        return result

    amounts = pd.to_numeric(df["凭证货币价值"], errors="coerce").fillna(0).abs()
    mask = df["供应商编号"].notna() & amounts.gt(0) & amounts.lt(max_single)
    pay = pd.DataFrame({
        "_vendor": df.loc[mask, "供应商编号"].astype(str).to_numpy(dtype=object),
        "_date": pd.to_datetime(df.loc[mask, "过账日期"], errors="coerce").to_numpy(),
        "_abs": amounts.loc[mask].to_numpy(dtype=float),
        "_vid": df.loc[mask, "凭证编号"].astype(str).to_numpy(dtype=object),
        "_line": df.index.to_numpy()[mask.to_numpy()],
    })

    if pay.empty:
        return result

    flagged: set[str] = set()

    # 维度1：同日同供应商 — 金额高度相似（差异≤15%）才是化整为零的核心特征
    day_grouped = pay.groupby(["_vendor", "_date"], sort=False, dropna=False)
    day_stats = day_grouped["_abs"].agg(["size", "sum", "mean", "min", "max"])
    day_stats = day_stats[
        (day_stats["size"] >= min_count)
        & (day_stats["sum"] >= min_total)
        & (day_stats["mean"] > 0)
    ]
    day_stats = day_stats[
        np.maximum(
            (day_stats["max"] - day_stats["mean"]).abs(),
            (day_stats["min"] - day_stats["mean"]).abs(),
        ).div(day_stats["mean"]) <= 0.15
    ]
    day_groups = day_grouped.indices
    vids = pay["_vid"].to_numpy(dtype=object)
    lines = pay["_line"].to_numpy()
    for (vendor, day), stats in day_stats.iterrows():
        positions = day_groups.get((vendor, day))
        if positions is None:
            continue
        group_vids = vids[positions]
        for vid in pd.unique(group_vids):
            if vid not in flagged:
                flagged.add(vid)
                result.hits.append(RuleHit(
                    voucher_id=vid,
                    rule_type="化整为零(同日拆分)",
                    evidence=(
                        f"供应商{vendor}同日{int(stats['size'])}笔金额相似"
                        f"（均值{stats['mean']:,.0f}，差异≤15%），合计{stats['sum']:,.0f}"
                    ),
                    line_indices=tuple(lines[positions][group_vids == vid].tolist()),
                    priority=5,
                ))

    # 维度2：窗口期内金额高度相似（差异≤10%）
    dates = pay["_date"].to_numpy()
    abs_values = pay["_abs"].to_numpy(dtype=float)
    vendor_groups = pay.groupby("_vendor", sort=False).indices
    window_delta = np.timedelta64(window_days, "D")
    for vendor, positions in vendor_groups.items():
        if len(positions) < min_count:
            continue
        order = positions[np.argsort(dates[positions], kind="stable")]
        vendor_dates = dates[order]
        vendor_amounts = abs_values[order]
        vendor_vids = vids[order]
        seen_windows = np.zeros(len(order), dtype=bool)
        right = 0

        for left in range(len(order)):
            if seen_windows[left] or np.isnat(vendor_dates[left]):
                continue
            if right < left:
                right = left
            window_end = vendor_dates[left] + window_delta
            while right < len(order) and vendor_dates[right] <= window_end:
                right += 1
            if right - left < min_count:
                continue
            amts = vendor_amounts[left:right]
            mean_amt = amts.mean()
            if mean_amt == 0:
                continue
            variance = float(np.max(np.abs(amts - mean_amt) / mean_amt))
            if variance <= 0.10:
                seen_windows[left:right] = True
                window_vids = vendor_vids[left:right]
                window_lines = lines[order[left:right]]
                for vid in pd.unique(window_vids):
                    if vid not in flagged:
                        flagged.add(vid)
                        result.hits.append(RuleHit(
                            voucher_id=vid,
                            rule_type="化整为零(窗口相似)",
                            evidence=f"供应商{vendor}在{window_days}天内{right - left}笔金额相似（差异≤10%）",
                            line_indices=tuple(window_lines[window_vids == vid].tolist()),
                            priority=4,
                        ))
    return result


# ─────────────────────────────────────────────
# Rule 2: 大额异常
# ─────────────────────────────────────────────

def rule_large_anomaly(df: pd.DataFrame, cfg: dict) -> RuleResult:
    c = cfg.get("large_amount", {})
    round_threshold: float = c.get("round_number_threshold", 1_000_000)
    repeat_threshold: float = c.get("repeat_threshold", 10_000_000)
    repeat_window: int = c.get("repeat_window_days", 30)
    repeat_min: int = c.get("repeat_min_count", 2)
    holiday_min: float = c.get("holiday_min_amount", 100_000)

    result = RuleResult(rule_name="大额异常")
    if not c.get("enabled", True):
        return result

    # 情形A：大额整数（按凭证去重）
    flagged_round: set[str] = set()
    large = df[df["凭证货币价值"].abs().fillna(0) >= round_threshold]
    for vid, grp in large.groupby("凭证编号"):
        if str(vid) in flagged_round:
            continue
        max_amt = grp["凭证货币价值"].abs().max()
        if _is_round(max_amt, round_threshold):
            flagged_round.add(str(vid))
            result.hits.append(RuleHit(
                voucher_id=str(vid),
                rule_type="大额整数",
                evidence=f"凭证最大行金额{max_amt:,.0f}为整数",
                line_indices=tuple(grp.index.tolist()),
                priority=2,
            ))

    # 情形B：大额重复（同供应商窗口内多笔）
    if "供应商编号" in df.columns:
        flagged_repeat: set[str] = set()
        for vendor, v_rows in df[df["供应商编号"].notna()].groupby("供应商编号"):
            large_v = v_rows[v_rows["凭证货币价值"].abs().fillna(0) >= repeat_threshold].sort_values("过账日期")
            if len(large_v) < repeat_min:
                continue
            dates = large_v["过账日期"].values
            for i in range(len(dates)):
                window_end = dates[i] + pd.Timedelta(days=repeat_window)
                window = large_v[(large_v["过账日期"] >= dates[i]) & (large_v["过账日期"] <= window_end)]
                if len(window) >= repeat_min:
                    for vid in window["凭证编号"].unique():
                        if str(vid) not in flagged_repeat:
                            flagged_repeat.add(str(vid))
                            result.hits.append(RuleHit(
                                voucher_id=str(vid),
                                rule_type="大额重复",
                                evidence=f"供应商{vendor}在{repeat_window}天内≥{repeat_min}笔，每笔≥{repeat_threshold/1e4:.0f}万",
                                line_indices=tuple(window[window["凭证编号"] == vid].index.tolist()),
                                priority=4,
                            ))

    # 情形C：节假日/周末过账 — 只标记周末率远高于公司均值的用户
    system_types = {"AA", "AB", "ZP", "CO"}
    df_work = df[~df["凭证类型"].isin(system_types)].copy()
    df_work["_is_weekend"] = df_work["过账日期"].dt.dayofweek >= 5

    # 基线：公司整体周末率
    company_weekend_rate = df_work["_is_weekend"].mean()
    if company_weekend_rate < 0.05:
        # 公司几乎不在周末过账，任何周末都异常
        weekend_threshold = 0.05
    else:
        # 周末率>5%的公司，只标记超过均值2倍的用户
        weekend_threshold = company_weekend_rate * 2

    # 用户周末率
    user_total = df_work.groupby("用户名").size()
    user_weekend = df_work[df_work["_is_weekend"]].groupby("用户名").size()
    flagged_users: set[str] = set()
    for user in user_total.index:
        rate = user_weekend.get(user, 0) / user_total[user]
        if rate > weekend_threshold and user_total[user] >= 100:
            flagged_users.add(user)

    if flagged_users:
        voucher_info = df.groupby("凭证编号").agg(
            date=("过账日期", "first"),
            max_amt=("凭证货币价值", lambda x: x.abs().max()),
            user=("用户名", "first"),
        )
        holiday_mask = _is_holiday_vectorized(voucher_info["date"])
        amount_mask = voucher_info["max_amt"] >= holiday_min
        user_mask = voucher_info["user"].isin(flagged_users)

        for vid in voucher_info[holiday_mask & amount_mask & user_mask].index:
            row = voucher_info.loc[vid]
            rate = user_weekend.get(row["user"], 0) / user_total[row["user"]]
            result.hits.append(RuleHit(
                voucher_id=str(vid),
                rule_type="异常周末过账",
                evidence=f"用户{row['user']}周末率{rate:.0%}(公司{company_weekend_rate:.0%})，{row['date'].strftime('%Y-%m-%d')}金额{row['max_amt']:,.0f}",
                line_indices=tuple(df[df["凭证编号"] == vid].index.tolist()),
                priority=3,
            ))

    # 情形D：凌晨录入（按凭证去重）
    if "录入时间" in df.columns:
        flagged_night: set[str] = set()
        for vid, grp in df.groupby("凭证编号"):
            if str(vid) in flagged_night:
                continue
            time_str = grp["录入时间"].iloc[0]
            if isinstance(time_str, str) and re.match(r"^\d{2}:\d{2}:\d{2}$", time_str):
                if int(time_str[:2]) < 6:
                    flagged_night.add(str(vid))
                    result.hits.append(RuleHit(
                        voucher_id=str(vid),
                        rule_type="凌晨录入",
                        evidence=f"录入时间{time_str}（凌晨0-6点）",
                        line_indices=tuple(grp.index.tolist()),
                        priority=3,
                    ))
    return result


# ─────────────────────────────────────────────
# Rule 3: 手工凭证
# ─────────────────────────────────────────────

def rule_manual_entries(df: pd.DataFrame, cfg: dict, key_personnel: list[str] | None = None) -> RuleResult:
    c = cfg.get("manual_entry", {})
    pnl_threshold: float = c.get("pnl_amount_threshold", 100_000)
    month_end_days: int = c.get("month_end_days", 5)

    result = RuleResult(rule_name="手工凭证")
    if not c.get("enabled", True):
        return result

    key_personnel = key_personnel or []

    text_col = "文本" if "文本" in df.columns else ("摘要" if "摘要" in df.columns else None)
    if text_col is None:
        return result

    # SA型手工凭证 + 文本含"手工"的凭证
    sa_mask = df["凭证类型"] == "SA"
    manual_kw_mask = df[text_col].astype(str).str.contains("手工", na=False)
    manual_df = df[sa_mask | manual_kw_mask]

    if manual_df.empty:
        return result

    # 基线：公司整体手工率
    company_manual_rate = len(manual_df) / len(df)
    # 用户手工率 — 只标记远高于均值的用户
    user_total = df.groupby("用户名").size()
    user_manual = manual_df.groupby("用户名").size()
    flagged_users: set[str] = set()
    for user in user_total.index:
        m = user_manual.get(user, 0)
        rate = m / user_total[user]
        if rate > max(company_manual_rate * 3, 0.02) and m >= 10:
            flagged_users.add(user)

    # 只处理异常用户的凭证
    suspect_df = manual_df[manual_df["用户名"].isin(flagged_users)]

    for vid, grp in suspect_df.groupby("凭证编号"):
        user = str(grp["用户名"].iloc[0]) if "用户名" in grp.columns else ""
        date = grp["过账日期"].iloc[0]
        max_amt = grp["凭证货币价值"].abs().max()
        m = user_manual.get(user, 0)
        rate = m / user_total[user]

        evidence_parts: list[str] = [f"用户{user}手工率{rate:.1%}(公司{company_manual_rate:.1%})"]
        priority = 2

        if user in key_personnel:
            evidence_parts.append("关键人员")
            priority = max(priority, 5)

        accts = grp["总账科目"].astype(str)
        has_pnl = accts.str[:1].isin(["5", "6"]).any()
        if has_pnl and max_amt > pnl_threshold:
            evidence_parts.append(f"损益科目{max_amt:,.0f}")
            priority = max(priority, 3)

        if isinstance(date, pd.Timestamp) and _is_last_n_days(date, month_end_days):
            timing = "年末" if (date.month == 12 and date.day >= 28) else "月末"
            evidence_parts.append(f"{timing}手工调整")
            priority = max(priority, 2)

        if evidence_parts:
            result.hits.append(RuleHit(
                voucher_id=str(vid),
                rule_type="手工凭证",
                evidence="，".join(evidence_parts),
                line_indices=tuple(grp.index.tolist()),
                priority=priority,
            ))

    return result


# ─────────────────────────────────────────────
# Rule 4: 预提冲销异常
# ─────────────────────────────────────────────

def rule_accrual_anomaly(df: pd.DataFrame, cfg: dict) -> RuleResult:
    c = cfg.get("accrual_anomaly", {})
    window_days: int = c.get("match_window_days", 90)
    tolerance: float = c.get("amount_tolerance", 0.10)
    min_amount: float = c.get("min_amount", 100_000)

    result = RuleResult(rule_name="计提异常")
    if not c.get("enabled", True):
        return result

    text_col = "文本" if "文本" in df.columns else ("摘要" if "摘要" in df.columns else None)
    if text_col is None:
        return result

    # 排除常规月度计提（人工费、折旧、摊销等自动计提）
    routine_kw = "人工费|折旧|摊销|社保|公积金|工资|税费|利息"
    accrual_rows = df[
        df[text_col].astype(str).str.contains("预提|计提", na=False)
        & ~df[text_col].astype(str).str.contains("冲销预提|冲预提|冲回|冲计提|冲销计提", na=False)
        & ~df[text_col].astype(str).str.contains(routine_kw, na=False)
        & (df["凭证货币价值"].fillna(0).abs() >= min_amount)
    ]
    if accrual_rows.empty:
        return result

    seen: set[str] = set()

    # 维度1：用户集中度 — 谁在做非常规计提
    user_counts = accrual_rows["用户名"].value_counts()
    total_accruals = len(accrual_rows)
    for user, cnt in user_counts.items():
        if cnt / total_accruals > 0.5 and cnt >= 20:
            vids = accrual_rows[accrual_rows["用户名"] == user]["凭证编号"].unique()
            for vid in vids:
                vid_str = str(vid)
                if vid_str not in seen:
                    seen.add(vid_str)
                    grp = accrual_rows[accrual_rows["凭证编号"] == vid]
                    result.hits.append(RuleHit(
                        voucher_id=vid_str,
                        rule_type="计提集中(用户独占)",
                    evidence=f"用户{user}非常规计提{cnt}笔(占{cnt/total_accruals:.0%})，单行金额≥{min_amount:,.0f}",
                        line_indices=tuple(grp.index.tolist()),
                        priority=4,
                    ))

    # 维度2：悬空计提 — 借方无对应贷方冲销（向量化 merge 替代双层 iterrows）
    for acct_prefix in accrual_rows["总账科目"].astype(str).str[:4].unique():
        acct_df = accrual_rows[accrual_rows["总账科目"].astype(str).str[:4] == acct_prefix].copy()
        debit = acct_df[acct_df["借/贷标识"] == "S"].copy()
        credit = acct_df[acct_df["借/贷标识"] == "H"].copy()

        if debit.empty:
            continue

        # 准备 join 字段
        debit["_d_date"] = pd.to_datetime(debit["过账日期"])
        debit["_d_amt"] = debit["凭证货币价值"].abs()
        debit["_d_key"] = range(len(debit))

        credit["_c_date"] = pd.to_datetime(credit["过账日期"])
        credit["_c_amt"] = credit["凭证货币价值"].abs()

        if credit.empty:
            # 无贷方记录，全部为悬空
            for _, d_row in debit.iterrows():
                vid = str(d_row["凭证编号"])
                if vid not in seen:
                    seen.add(vid)
                    result.hits.append(RuleHit(
                        voucher_id=vid,
                        rule_type="悬空计提",
                        evidence=f"科目{acct_prefix}计提{d_row['_d_amt']:,.0f}，无对应贷方冲销",
                        line_indices=tuple([d_row.name]),
                        priority=3,
                    ))
            continue

        # Cross join on科目前缀 → filter by date range + amount tolerance
        merged = debit[["_d_key", "凭证编号", "_d_date", "_d_amt"]].merge(
            credit[["凭证编号", "_c_date", "_c_amt"]],
            how="cross", suffixes=("_d", "_c")
        )
        # 日期窗口过滤
        window_end = merged["_d_date"] + pd.Timedelta(days=window_days)
        merged = merged[
            (merged["_c_date"] > merged["_d_date"]) & (merged["_c_date"] <= window_end)
        ]
        # 金额容差过滤
        merged = merged[
            merged["_d_amt"] > 0
        ]
        merged["_tol"] = (merged["_c_amt"] - merged["_d_amt"]).abs() / merged["_d_amt"]
        merged = merged[merged["_tol"] <= tolerance]

        # 找到有匹配的 debit keys
        matched_keys = set(merged["_d_key"].unique())
        unmatched_debit = debit[~debit["_d_key"].isin(matched_keys)]

        for _, d_row in unmatched_debit.iterrows():
            vid = str(d_row["凭证编号"])
            if vid not in seen:
                seen.add(vid)
                result.hits.append(RuleHit(
                    voucher_id=vid,
                    rule_type="悬空计提",
                    evidence=f"科目{acct_prefix}计提{d_row['_d_amt']:,.0f}，{window_days}天内无冲销",
                    line_indices=tuple([d_row.name]),
                    priority=3,
                ))

    return result


# ─────────────────────────────────────────────
# Rule 5: 年末突击确认
# ─────────────────────────────────────────────

def rule_yearend_surge(df: pd.DataFrame, cfg: dict) -> RuleResult:
    c = cfg.get("yearend_surge", {})
    multiplier: float = c.get("multiplier", 2.0)
    months: list[int] = c.get("months", [12])

    result = RuleResult(rule_name="收入突增")
    if not c.get("enabled", True):
        return result

    df = ensure_category(df)
    rev = df[df["_acct_category"].eq(CAT_REVENUE) & (df["借/贷标识"] == "H")].copy()
    if rev.empty:
        return result

    rev["_month"] = rev["过账日期"].dt.month
    monthly = rev.groupby("_month")["凭证货币价值"].apply(lambda x: x.abs().sum())

    if len(monthly) < 3:
        return result

    baseline_months = [m for m in monthly.index if m not in months]
    baseline = monthly.loc[baseline_months].mean() if baseline_months else monthly.mean()

    # 检测配置月份是否超过基准月份均值 multiplier 倍
    for month, amount in monthly.items():
        if month not in months:
            continue
        if baseline > 0 and amount > baseline * multiplier:
            month_name = f"{month}月"
            surge_vids = rev[rev["_month"] == month]["凭证编号"].unique()
            for vid in surge_vids:
                result.hits.append(RuleHit(
                    voucher_id=str(vid),
                    rule_type=f"收入突增({month_name})",
                    evidence=f"{month_name}收入{amount/1e4:,.0f}万，其他月份均值{baseline/1e4:,.0f}万，{amount/baseline:.1f}x",
                    line_indices=tuple(
                        rev[(rev["凭证编号"] == vid) & (rev["_month"] == month)].index.tolist()
                    ),
                    priority=3,
                ))
    return result


# ─────────────────────────────────────────────
# Rule 6: 融资性贸易
# ─────────────────────────────────────────────

def rule_financing_trade(df: pd.DataFrame, cfg: dict) -> RuleResult:
    c = cfg.get("financing_trade", {})
    min_revenue_amount: float = c.get("min_revenue_amount", 1_000_000)
    low_margin_threshold: float = c.get("low_margin_threshold", c.get("margin_threshold", 0.05))
    max_loss_rate: float = c.get("max_loss_rate", 0.50)
    min_match_score: float = c.get("min_match_score", 0.55)
    max_candidate_groups: int = c.get("max_candidate_groups", 50)
    max_related_vouchers: int = c.get("max_related_vouchers", 5)
    window_days: int = c.get("window_days", 30)
    keywords: list[str] = c.get("keywords", ["代垫", "代采购", "委托贸易", "保理"])

    result = RuleResult(rule_name="融资性贸易")
    if not c.get("enabled", True):
        return result

    non_trade_kw = ["租金", "利息", "存款", "理财", "保险", "补贴", "计提", "冲销", "预提"]

    facts = _build_trade_voucher_facts(df)
    if facts.empty:
        return result

    revenues = facts[facts["revenue_amount"] >= min_revenue_amount].sort_values("revenue_amount", ascending=False)
    costs = (
        facts[facts["cost_amount"] > 0]
        .sort_values("date", kind="stable")
        .reset_index(drop=True)
    )
    if revenues.empty:
        return result
    cost_records = costs.to_dict(orient="records")
    cost_dates = [row["date"] for row in cost_records]

    group_count = 0
    seen_groups: set[str] = set()

    for _, rev in revenues.iterrows():
        if group_count >= max_candidate_groups:
            break

        if any(kw in str(rev["text"]) for kw in non_trade_kw):
            continue

        same_voucher_cost = float(rev.get("cost_amount", 0.0))
        if same_voucher_cost > 0:
            same_margin = (float(rev["revenue_amount"]) - same_voucher_cost) / float(rev["revenue_amount"])
            if -max_loss_rate <= same_margin <= low_margin_threshold:
                group_id = f"FT-{rev['voucher_id']}-SAME"
                if group_id not in seen_groups:
                    seen_groups.add(group_id)
                    result.hits.append(RuleHit(
                        voucher_id=str(rev["voucher_id"]),
                        rule_type="融资性贸易(同凭证低毛利)",
                        evidence=(
                            f"同凭证收入{float(rev['revenue_amount']):,.0f}、成本{same_voucher_cost:,.0f}，"
                            f"毛利率{same_margin:.1%}，需复核贸易实质"
                        ),
                        line_indices=tuple(rev["income_line_indices"] or rev["all_line_indices"]) + tuple(rev["cost_line_indices"] or ()),
                        priority=4 if same_margin >= 0 else 5,
                        year=int(rev["year"]) if pd.notna(rev["year"]) else None,
                        group_id=group_id,
                        relation_evidence="收入与成本已经在同一凭证内出现，优先核对合同、出入库和定价依据",
                    ))
                    group_count += 1
                    if group_count >= max_candidate_groups:
                        break
                continue

        scored_costs: list[dict[str, Any]] = []
        rev_date = rev["date"]
        if not isinstance(rev_date, pd.Timestamp):
            continue
        start_date = rev_date - pd.Timedelta(days=window_days)
        end_date = rev_date + pd.Timedelta(days=window_days)
        left = bisect_left(cost_dates, start_date)
        right = bisect_right(cost_dates, end_date)
        for cost in cost_records[left:right]:
            if rev["voucher_id"] == cost["voucher_id"]:
                continue
            score, reasons = _score_trade_relation(rev, cost, window_days)
            if score < min_match_score:
                continue
            scored_costs.append({
                "voucher_id": cost["voucher_id"],
                "date": cost["date"],
                "cost_amount": float(cost["cost_amount"]),
                "score": score,
                "reasons": reasons,
                "line_indices": tuple(cost["cost_line_indices"] or cost["all_line_indices"]),
            })

        scored_costs.sort(
            key=lambda item: (
                -item["score"],
                abs((item["date"] - rev["date"]).days) if isinstance(item["date"], pd.Timestamp) else 9999,
                -item["cost_amount"],
            )
        )

        selected: list[dict[str, Any]] = []
        total_cost = 0.0
        for candidate in scored_costs:
            if len(selected) >= max_related_vouchers:
                break
            next_cost = total_cost + candidate["cost_amount"]
            next_margin = (float(rev["revenue_amount"]) - next_cost) / float(rev["revenue_amount"])
            if next_margin < -max_loss_rate and selected:
                continue
            if next_margin < -max_loss_rate and not selected:
                continue
            selected.append(candidate)
            total_cost = next_cost
            if next_margin <= low_margin_threshold:
                break

        margin = (float(rev["revenue_amount"]) - total_cost) / float(rev["revenue_amount"]) if rev["revenue_amount"] else 0.0
        if selected and -max_loss_rate <= margin <= low_margin_threshold:
            related_ids = tuple(str(item["voucher_id"]) for item in selected)
            group_id = f"FT-{rev['voucher_id']}-{'-'.join(related_ids[:3])}"
            if group_id in seen_groups:
                continue
            seen_groups.add(group_id)

            relation_bits = []
            for item in selected:
                day_gap = abs((item["date"] - rev["date"]).days) if isinstance(item["date"], pd.Timestamp) else None
                gap_text = f"相差{day_gap}天" if day_gap is not None else "日期缺失"
                reason_text = "、".join(item["reasons"][:3])
                relation_bits.append(
                    f"{item['voucher_id']}({gap_text}，成本{item['cost_amount']:,.0f}，匹配{item['score']:.0%}：{reason_text})"
                )
            relation_evidence = "；".join(relation_bits)
            line_indices = tuple(rev["income_line_indices"] or rev["all_line_indices"]) + tuple(
                idx for item in selected for idx in item["line_indices"]
            )
            result.hits.append(RuleHit(
                voucher_id=str(rev["voucher_id"]),
                rule_type="融资性贸易(收入-成本组合低毛利)",
                evidence=(
                    f"收入{float(rev['revenue_amount']):,.0f}，匹配成本{total_cost:,.0f}，"
                    f"组合毛利率{margin:.1%}，需复核是否为贸易形式的资金通道"
                ),
                line_indices=line_indices,
                priority=4 if margin >= 0 else 5,
                year=int(rev["year"]) if pd.notna(rev["year"]) else None,
                group_id=group_id,
                related_voucher_ids=related_ids,
                relation_evidence=relation_evidence,
            ))
            group_count += 1

        elif not selected and any(kw in str(rev["text"]) for kw in keywords):
            group_id = f"FT-{rev['voucher_id']}-NO-COST"
            if group_id in seen_groups:
                continue
            seen_groups.add(group_id)
            result.hits.append(RuleHit(
                voucher_id=str(rev["voucher_id"]),
                rule_type="融资性贸易(关键词收入无匹配成本)",
                evidence=f"收入{float(rev['revenue_amount']):,.0f}，文本含融资/代采类关键词，{window_days}天内未找到可解释成本凭证",
                line_indices=tuple(rev["income_line_indices"] or rev["all_line_indices"]),
                priority=4,
                year=int(rev["year"]) if pd.notna(rev["year"]) else None,
                group_id=group_id,
                relation_evidence="无关联成本凭证，需结合合同、物流和收付款进一步复核",
            ))
            group_count += 1

    return result


# ─────────────────────────────────────────────
# Rule 7: 资金池/同名划转穿透
# ─────────────────────────────────────────────

def rule_cash_pool(df: pd.DataFrame, cfg: dict) -> RuleResult:
    """检测资金池、同名划转、关联方资金占用等风险。"""
    c = cfg.get("cash_pool", {})
    keywords: list[str] = c.get("keywords", ["资金池", "同名划转", "上划", "下拨"])
    large_threshold: float = c.get("large_threshold", 10_000_000)

    result = RuleResult(rule_name="资金池划转")
    if not c.get("enabled", True):
        return result

    text_col = "文本" if "文本" in df.columns else ("摘要" if "摘要" in df.columns else None)
    if text_col is None:
        return result

    text = df[text_col].astype(str).fillna("")
    mask = pd.Series(False, index=df.index)
    for kw in keywords:
        mask |= text.str.contains(kw, na=False)
    flagged_df = df[mask]
    if flagged_df.empty:
        return result

    seen: set[str] = set()
    for vid, grp in flagged_df.groupby("凭证编号"):
        vid_str = str(vid)
        if vid_str in seen:
            continue
        max_amt = grp["凭证货币价值"].abs().max()
        if max_amt >= large_threshold:
            seen.add(vid_str)
            result.hits.append(RuleHit(
                voucher_id=vid_str,
                rule_type="资金池大额划转",
                evidence=f"凭证含资金池关键词，最大行{max_amt:,.0f}",
                line_indices=tuple(grp.index.tolist()),
                priority=3,
            ))

    return result


# ─────────────────────────────────────────────
# Rule 8: 用户集中度异常
# ─────────────────────────────────────────────

def rule_user_concentration(df: pd.DataFrame, cfg: dict) -> RuleResult:
    """检测单用户过账量异常集中（行数占比，非凭证数）。"""
    c = cfg.get("user_concentration", {})
    threshold: float = c.get("concentration_threshold", 0.25)

    result = RuleResult(rule_name="用户集中度异常")
    if not c.get("enabled", True):
        return result

    if "用户名" not in df.columns:
        return result

    total = len(df)
    user_counts = df["用户名"].value_counts()

    for user, cnt in user_counts.items():
        ratio = cnt / total
        if ratio >= threshold:
            result.hits.append(RuleHit(
                voucher_id=f"USER:{user}",
                rule_type="用户集中度",
                evidence=f"用户{user}过账{cnt:,}行，占总量{ratio:.1%}",
                line_indices=(),
                priority=3,
            ))

    return result


# ─────────────────────────────────────────────
# Rule 9: 冲销/反记账模式
# ─────────────────────────────────────────────

def rule_reversal_pattern(df: pd.DataFrame, cfg: dict) -> RuleResult:
    """检测高频冲销、大额冲销、期后冲销等异常模式。"""
    c = cfg.get("reversal_pattern", {})
    frequent_count: int = c.get("frequent_count", 5)
    large_threshold: float = c.get("large_threshold", 500_000)

    result = RuleResult(rule_name="冲销反记账异常")
    if not c.get("enabled", True):
        return result

    text_col = "文本" if "文本" in df.columns else ("摘要" if "摘要" in df.columns else None)
    if text_col is None:
        return result

    text = df[text_col].astype(str).fillna("")
    reversal_mask = text.str.contains("冲销|反记帐|反记账", na=False)
    reversal_df = df[reversal_mask]
    if reversal_df.empty:
        return result

    seen: set[str] = set()

    # 大额冲销
    for vid, grp in reversal_df.groupby("凭证编号"):
        vid_str = str(vid)
        if vid_str in seen:
            continue
        max_amt = grp["凭证货币价值"].abs().max()
        if max_amt >= large_threshold:
            seen.add(vid_str)
            result.hits.append(RuleHit(
                voucher_id=vid_str,
                rule_type="大额冲销",
                evidence=f"冲销凭证最大行{max_amt:,.0f}",
                line_indices=tuple(grp.index.tolist()),
                priority=3,
            ))

    # 频繁冲销用户
    if "用户名" in reversal_df.columns:
        user_rev_counts = reversal_df.groupby("用户名")["凭证编号"].nunique()
        for user, cnt in user_rev_counts.items():
            if cnt >= frequent_count:
                vids = reversal_df[reversal_df["用户名"] == user]["凭证编号"].unique()
                for vid in vids:
                    vid_str = str(vid)
                    if vid_str not in seen:
                        seen.add(vid_str)
                        result.hits.append(RuleHit(
                            voucher_id=vid_str,
                            rule_type="频繁冲销用户",
                            evidence=f"用户{user}冲销{cnt}笔",
                            line_indices=tuple(
                                reversal_df[(reversal_df["凭证编号"] == vid) & (reversal_df["用户名"] == user)].index.tolist()
                            ),
                            priority=2,
                        ))

    return result


# ─────────────────────────────────────────────
# Rule 10: 敏感费用筛查
# ─────────────────────────────────────────────

def rule_sensitive_fees(df: pd.DataFrame, cfg: dict) -> RuleResult:
    """筛查敏感费用关键词（咨询、代理、招待、捐赠等），结合金额阈值过滤常规小额。"""
    c = cfg.get("sensitive_fees", {})
    categories: dict[str, dict] = c.get("categories", {
        "咨询费": {"keywords": ["咨询", "顾问"], "exclude": [], "threshold": 10000},
        "代理费": {"keywords": ["代理", "代办"], "exclude": ["货运代理", "报关", "快递"], "threshold": 10000},
        "中介费": {"keywords": ["中介", "经纪"], "exclude": [], "threshold": 100000},
        "设计费": {"keywords": ["设计", "策划"], "exclude": ["机械设计"], "threshold": 100000},
        "捐赠赞助": {"keywords": ["捐赠", "赞助"], "exclude": [], "threshold": 10000},
        "罚款赔偿": {"keywords": ["罚款", "罚金", "滞纳金", "违约金"], "exclude": [], "threshold": 10000},
        "招待费": {"keywords": ["招待", "接待"], "exclude": [], "threshold": 50000},
        "旅游团建": {"keywords": ["旅游", "团建", "考察"], "exclude": ["出差", "差旅"], "threshold": 10000},
    })
    baseline_multiplier: float = c.get("baseline_multiplier", 3.0)

    result = RuleResult(rule_name="敏感费用筛查")
    if not c.get("enabled", True):
        return result

    text_col = "文本" if "文本" in df.columns else ("摘要" if "摘要" in df.columns else None)
    if text_col is None:
        return result

    seen: set[str] = set()

    for cat_name, cat_cfg in categories.items():
        keywords = cat_cfg.get("keywords", [])
        exclude = cat_cfg.get("exclude", [])
        threshold = cat_cfg.get("threshold", 10000)

        if not keywords:
            continue

        kw_pattern = "|".join(keywords)
        exclude_pattern = "|".join(exclude) if exclude else None

        text = df[text_col].astype(str).fillna("")
        mask = text.str.contains(kw_pattern, na=False)
        if exclude_pattern:
            mask &= ~text.str.contains(exclude_pattern, na=False)

        cat_df = df[mask].copy()
        if cat_df.empty:
            continue

        # 全部命中数量
        cat_count = len(cat_df)
        total = len(df)
        cat_rate = cat_count / total if total > 0 else 0

        # 用户维度：谁的敏感费用占比异常高
        user_total = df.groupby("用户名").size()
        user_cat = cat_df.groupby("用户名").size()
        flagged_users: set[str] = set()
        for user in user_total.index:
            u_count = user_cat.get(user, 0)
            if u_count == 0:
                continue
            u_rate = u_count / user_total[user]
            # 用户敏感费用率 > 公司均值 × multiplier，且至少 5 笔
            if u_rate > cat_rate * baseline_multiplier and u_count >= 10:
                flagged_users.add(user)

        # 异常用户：只取金额最大的 top 20 凭证（避免单用户淹没样本）
        flagged_user_top_n = 20
        for user in flagged_users:
            user_cat = cat_df[cat_df["用户名"] == user].copy()
            user_vids = user_cat.groupby("凭证编号")["凭证货币价值"].apply(lambda x: x.abs().max())
            top_vids = user_vids.nlargest(flagged_user_top_n).index
            u_count = user_cat["凭证编号"].nunique() if not user_cat.empty else 0

            for vid in top_vids:
                vid_str = str(vid)
                if vid_str in seen:
                    continue
                seen.add(vid_str)
                grp = user_cat[user_cat["凭证编号"] == vid]
                amt = grp["凭证货币价值"].abs().max()
                result.hits.append(RuleHit(
                    voucher_id=vid_str,
                    rule_type=f"敏感费用({cat_name})",
                    evidence=f"{cat_name}，用户{user}敏感费用率异常(>{baseline_multiplier:.0f}x公司均值，共{u_count}笔)，金额{amt:,.0f}",
                    line_indices=tuple(grp.index.tolist()),
                    priority=3,
                ))

        # 非异常用户：仅标记超阈值的（向量化，避免 iterrows）
        cat_df["_vid"] = cat_df["凭证编号"].astype(str)
        cat_df["_user"] = cat_df.get("用户名", pd.Series("")).fillna("").astype(str)
        cat_df["_amt"] = cat_df["凭证货币价值"].abs()
        over_threshold = cat_df[
            (~cat_df["_vid"].isin(seen))
            & (~cat_df["_user"].isin(flagged_users))
            & (cat_df["_amt"] >= threshold)
        ]
        for _, row in over_threshold.head(200).iterrows():  # 安全上限，避免输出爆炸
            vid = row["_vid"]
            seen.add(vid)
            result.hits.append(RuleHit(
                voucher_id=vid,
                rule_type=f"敏感费用({cat_name})",
                evidence=f"{cat_name}金额{row['_amt']:,.0f}，文本：{str(row.get(text_col, ''))[:40]}",
                line_indices=tuple(cat_df[cat_df["凭证编号"] == vid].index.tolist()),
                priority=2,
            ))

    return result


# ─────────────────────────────────────────────
# 跨年规则 → RuleHit 转换
# ─────────────────────────────────────────────

_CROSS_YEAR_CATEGORY_RULE_MAP = {
    "预提冲回配对": "cross_year_accrual",
    "预提冲回金额不符": "cross_year_accrual",
    "收入跨年确认": "cross_year_revenue",
}


def _rule_enabled(cfg: dict | None, rule_key: str, default: bool = True) -> bool:
    section = (cfg or {}).get(rule_key)
    if not isinstance(section, dict):
        return default
    return bool(section.get("enabled", default))


def _cross_year_detection_enabled(cfg: dict | None) -> bool:
    section = (cfg or {}).get("cross_year_detection")
    if isinstance(section, dict) and "enabled" in section:
        return bool(section.get("enabled"))
    return (
        _rule_enabled(cfg, "cross_year_accrual", default=True)
        or _rule_enabled(cfg, "cross_year_revenue", default=True)
    )


def _include_cross_year_finding(finding: Any, cfg: dict | None) -> bool:
    category = str(getattr(finding, "category", ""))
    rule_key = _CROSS_YEAR_CATEGORY_RULE_MAP.get(category)
    if rule_key:
        return _rule_enabled(cfg, rule_key, default=True)
    return _cross_year_detection_enabled(cfg)


def cross_year_findings_to_hits(findings: list, cfg: dict | None = None) -> RuleResult:
    """将 cross_year.CrossYearFinding 列表转为 RuleResult。"""
    result = RuleResult(rule_name="跨年异常")
    for f in findings:
        if not _include_cross_year_finding(f, cfg):
            continue
        severity_priority = {"高": 5, "中": 3, "低": 2}.get(f.severity, 2)
        for vid in f.voucher_ids:
            result.hits.append(RuleHit(
                voucher_id=str(vid),
                rule_type=f"跨年:{f.category}",
                evidence=f.description[:120],
                line_indices=(),
                priority=severity_priority,
                year=f.years_involved[0] if f.years_involved else None,
            ))
    return result


# ─────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────

RULE_DISPATCH = {
    "splitting": rule_splitting,
    "large_amount": rule_large_anomaly,
    "manual_entry": rule_manual_entries,
    "accrual_anomaly": rule_accrual_anomaly,
    "yearend_surge": rule_yearend_surge,
    "financing_trade": rule_financing_trade,
    "cash_pool": rule_cash_pool,
    "user_concentration": rule_user_concentration,
    "reversal_pattern": rule_reversal_pattern,
    "sensitive_fees": rule_sensitive_fees,
}


def _base_rule_key(rule_key: str) -> str:
    """Strip _custom_N suffix to get base rule key."""
    return re.sub(r"_custom_\d+$", "", rule_key)


def _enabled_rule_keys(cfg: dict) -> list[str]:
    """Return all enabled rule keys from config, excluding whitelist/meta keys."""
    meta_keys = {
        "whitelist_keywords",
        "whitelist_voucher_types",
        "routine_exclusion",
        "max_sample_size",
    }
    return sorted(
        k for k, v in cfg.items()
        if k not in meta_keys and isinstance(v, dict) and v.get("enabled", False)
    )


def run_all_rules(
    df: pd.DataFrame,
    cfg: dict,
    cross_year_findings: list | None = None,
    key_personnel: list[str] | None = None,
    candidate_voucher_ids: set[str] | list[str] | None = None,
) -> list[RuleResult]:
    df_filtered, _ = apply_whitelist(df, cfg)
    candidate_set = {str(v) for v in candidate_voucher_ids or [] if str(v)}
    if candidate_set and "凭证编号" in df_filtered.columns:
        df_filtered = df_filtered[df_filtered["凭证编号"].astype(str).isin(candidate_set)].copy()

    max_sample_size = int(cfg.get("max_sample_size", 50))

    results = []
    dispatched_base_keys = set()

    enabled_rule_keys = _enabled_rule_keys(cfg)

    for rule_key in enabled_rule_keys:
        base_key = _base_rule_key(rule_key)
        fn = RULE_DISPATCH.get(base_key)
        if fn is None:
            continue

        if rule_key == base_key:
            result = fn(df_filtered, cfg)
        else:
            temp_cfg = {**cfg, base_key: cfg[rule_key]}
            result = fn(df_filtered, temp_cfg)
            result.rule_name = rule_key

        results.append(result)
        dispatched_base_keys.add(base_key)

    if cross_year_findings:
        scoped_findings = cross_year_findings
        if candidate_set:
            scoped_findings = [
                f for f in cross_year_findings
                if set(str(v) for v in getattr(f, "voucher_ids", [])).intersection(candidate_set)
            ]
        cross_year_result = cross_year_findings_to_hits(scoped_findings, cfg)
        if cross_year_result.hits:
            results.append(cross_year_result)

    if candidate_set:
        for result in results:
            result.hits = [
                hit for hit in result.hits
                if str(hit.voucher_id) in candidate_set
                or set(str(v) for v in hit.related_voucher_ids).intersection(candidate_set)
            ]

    # ── 按 max_sample_size 截断 ──
    # 收集所有命中，按凭证去重，按优先级(降序)排序，取前 max_sample_size 个凭证
    vid_priority: dict[str, int] = {}
    for r in results:
        for hit in r.hits:
            vid = hit.voucher_id
            vid_priority[vid] = max(vid_priority.get(vid, 0), hit.priority)

    top_voucher_ids = set(
        sorted(vid_priority, key=lambda v: (vid_priority[v], v), reverse=True)
        [:max_sample_size]
    )

    # 过滤每个 RuleResult 的 hits
    for r in results:
        r.hits = [hit for hit in r.hits if hit.voucher_id in top_voucher_ids]

    return results


def hits_summary(results: list[RuleResult]) -> list[dict]:
    rows = []
    for r in results:
        voucher_count = len({h.voucher_id for h in r.hits})
        rows.append({"规则": r.rule_name, "命中数": r.count, "凭证数": voucher_count})
    return rows
