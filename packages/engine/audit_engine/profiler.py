"""
单体统计画像模块：对单年序时账生成结构化画像，供 LLM 规则校准和可视化使用。
输出 profile dict，可序列化为 JSON。

做账逻辑说明（基于 SAP 序时账）：
- 每张凭证至少 2 行，借贷必平衡（凭证货币价值在凭证级 sum = 0）
- 借/贷标识：S=借方（正值），H=贷方（负值）
- 凭证类型分为：系统自动生成 vs 用户手工录入
- 总账科目为 10 位码，前 N 位标识科目层级
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from audit_engine.account_classifier import (
    investment_income_mask,
    non_operating_expense_mask,
    non_operating_income_mask,
    operating_cost_mask,
    operating_revenue_mask,
)
from audit_engine.analysis.income_cost import monthly_revenue_cost
from audit_engine.analysis.statistical_profile import (
    benford_eligible_rows,
    first_digit,
)
from audit_engine.config.accounts import AUTO_VOUCHER_TYPES, classify_expense_subcategory
from audit_engine.data_columns import (
    VOUCHER_KEY_COLUMN,
    analysis_quality_summary,
    ensure_analysis_columns,
    ensure_voucher_identity,
)

PROFILE_SCHEMA_VERSION = 2


def _voucher_id_col(df: pd.DataFrame) -> str:
    """凭证级聚合必须用 voucher_key；仅在缺失时降级到展示编号。"""
    work = df if VOUCHER_KEY_COLUMN in df.columns else ensure_voucher_identity(df)
    if VOUCHER_KEY_COLUMN in work.columns:
        return VOUCHER_KEY_COLUMN
    return "凭证编号"

# ── 主函数 ──

def build_profile(df: pd.DataFrame, year: int) -> dict[str, Any]:
    """对单年 DataFrame 生成完整画像。"""
    return {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "year": year,
        "overview": _overview(df),
        "amount_distribution": _amount_distribution(df),
        "benford_first_digit": _benford_first_digit(df),
        "temporal_patterns": _temporal_patterns(df),
        "voucher_type_structure": _voucher_type_structure(df),
        "account_structure": _account_structure(df),
        "user_patterns": _user_patterns(df),
        "vendor_patterns": _vendor_patterns(df),
        "text_patterns": _text_patterns(df),
        "manual_entry_ratio": _manual_entry_ratio(df),
    }


# ── 画像子函数 ──

def _overview(df: pd.DataFrame) -> dict:
    work = ensure_voucher_identity(df)
    vcol = _voucher_id_col(work)
    total_vouchers = int(work[vcol].nunique())
    return {
        "total_rows": len(work),
        "total_vouchers": total_vouchers,
        "avg_rows_per_voucher": round(len(work) / total_vouchers, 1) if total_vouchers > 0 else 0,
        "date_range": {
            "start": str(work["过账日期"].min().date()),
            "end": str(work["过账日期"].max().date()),
        },
        "period13_rows": int(work.get("_is_period13", pd.Series(False, index=work.index)).sum()),
        "currencies": work["凭证货币代码"].value_counts().to_dict() if "凭证货币代码" in work.columns else {},
    }


def _amount_distribution(df: pd.DataFrame) -> dict:
    """金额分布：行级 + 凭证级（凭证级更有审计意义）。"""
    work = ensure_analysis_columns(df)
    vcol = _voucher_id_col(work)
    amt = work["_amount_abs"].dropna()
    nonzero = amt[amt > 0]

    def _percentiles(s: pd.Series) -> dict:
        if s.empty:
            return {}
        return {
            "p25": round(float(s.quantile(0.25)), 2),
            "p50": round(float(s.quantile(0.50)), 2),
            "p75": round(float(s.quantile(0.75)), 2),
            "p90": round(float(s.quantile(0.90)), 2),
            "p95": round(float(s.quantile(0.95)), 2),
            "p99": round(float(s.quantile(0.99)), 2),
            "max": round(float(s.max()), 2),
            "mean": round(float(s.mean()), 2),
        }

    # 凭证级金额：每张凭证的借方合计（按 voucher_key）
    voucher_debit = (
        work[work["_dc"] == "S"]
        .groupby(vcol)["_amount_abs"]
        .sum()
    )
    voucher_nonzero = voucher_debit[voucher_debit > 0]
    top_1pct_count = max(1, int(np.ceil(len(voucher_nonzero) * 0.01))) if len(voucher_nonzero) else 0
    top_1pct_amount = float(voucher_nonzero.nlargest(top_1pct_count).sum()) if top_1pct_count else 0.0
    voucher_total = float(voucher_nonzero.sum())

    by_type: dict[str, dict] = {}
    if "凭证类型" in df.columns:
        for vtype, grp in df.groupby("凭证类型"):
            s = grp["凭证货币价值"].abs().dropna()
            s = s[s > 0]
            if len(s) >= 5:
                by_type[str(vtype)] = _percentiles(s)

    # 大额整数占比（行级，尾数为整万）
    round_count = int((nonzero % 10_000 == 0).sum())

    row_pct = _percentiles(nonzero)
    voucher_pct = _percentiles(voucher_nonzero)
    return {
        "overall": row_pct,          # 向后兼容
        "row_level": row_pct,
        "voucher_level": voucher_pct,
        "by_voucher_type": by_type,
        "round_number_count": round_count,
        "round_number_ratio": round(round_count / len(nonzero), 4) if len(nonzero) > 0 else 0,
        "top_1pct_voucher_count": top_1pct_count,
        "top_1pct_amount_ratio": round(top_1pct_amount / voucher_total, 4) if voucher_total else 0,
    }


def _benford_first_digit(df: pd.DataFrame) -> dict:
    """本福特定律首位数字分布，用于识别非自然金额模式。"""
    eligible = benford_eligible_rows(df)
    amounts = eligible["_amount_abs"]
    expected = {d: float(np.log10(1 + 1 / d)) for d in range(1, 10)}
    if amounts.empty:
        return {
            "sample_size": 0,
            "observed": {},
            "expected": {d: round(value, 4) for d, value in expected.items()},
            "deviation": {},
            "total_variation_distance": 0,
            "mean_absolute_deviation": 0,
            "chi_square": 0,
            "top_deviation_digit": None,
            "order_magnitude_count": 0,
            "applicable": False,
            "conformity": "样本不足",
            "basis": "借方分录绝对额 ≥ 10",
        }

    first_digits = first_digit(amounts).dropna().astype(int)
    first_digits = first_digits[(first_digits >= 1) & (first_digits <= 9)]
    counts = first_digits.value_counts().sort_index()
    sample_size = int(counts.sum())
    observed = {d: round(float(counts.get(d, 0) / sample_size), 4) for d in range(1, 10)} if sample_size else {}
    deviation = {d: round(observed.get(d, 0) - expected[d], 4) for d in range(1, 10)}
    mad = float(sum(abs(value) for value in deviation.values()) / 9)
    magnitude_count = int(np.floor(np.log10(amounts)).nunique())
    applicable = sample_size >= 100 and magnitude_count >= 3
    if not applicable:
        conformity = "样本不足"
    elif mad <= 0.006:
        conformity = "高度符合"
    elif mad <= 0.012:
        conformity = "可接受"
    elif mad <= 0.015:
        conformity = "临界偏离"
    else:
        conformity = "明显偏离"
    expected_counts = {d: expected[d] * sample_size for d in range(1, 10)}
    chi_square = sum(
        ((counts.get(d, 0) - expected_counts[d]) ** 2) / expected_counts[d]
        for d in range(1, 10)
        if expected_counts[d] > 0
    )
    top_digit = max(deviation, key=lambda d: abs(deviation[d])) if deviation else None

    return {
        "sample_size": sample_size,
        "observed": observed,
        "expected": {d: round(v, 4) for d, v in expected.items()},
        "deviation": deviation,
        "total_variation_distance": round(float(0.5 * sum(abs(v) for v in deviation.values())), 4),
        "mean_absolute_deviation": round(mad, 4),
        "chi_square": round(float(chi_square), 2),
        "top_deviation_digit": int(top_digit) if top_digit is not None else None,
        "order_magnitude_count": magnitude_count,
        "applicable": applicable,
        "conformity": conformity,
        "basis": "借方分录绝对额 ≥ 10",
    }


def _temporal_patterns(df: pd.DataFrame) -> dict:
    """时间规律：月度、月末集中度、异常时段过账。"""
    df = ensure_analysis_columns(df).copy()
    vcol = _voucher_id_col(df)
    df["_dom"] = df["过账日期"].dt.day
    df["_dow"] = df["过账日期"].dt.dayofweek  # 0=Mon

    monthly_count = df.groupby("_month")[vcol].nunique()
    monthly_amount = df[df["_dc"].eq("S")].groupby("_month")["_amount_abs"].apply(
        lambda values: round(float(values.sum()), 2)
    )

    # 月末集中度：每月最后 5 天的凭证数占该月凭证数比例；Period 13 单独排除。
    month_end_ratios: dict[int, float] = {}
    for m, grp in df.groupby("_month"):
        if int(m) == 13:
            continue
        vouchers = grp[[vcol, "过账日期"]].drop_duplicates(vcol)
        last_day = vouchers["过账日期"].dt.days_in_month
        is_end = vouchers["过账日期"].dt.day > (last_day - 5)
        month_end_ratios[int(m)] = round(float(is_end.sum() / len(vouchers)), 4) if len(vouchers) > 0 else 0

    # 节假日/周末过账（向量化：先获取节假日集合，再用 isin 过滤）
    weekend_count = int((df["_dow"] >= 5).sum())
    holiday_count = 0
    try:
        import chinese_calendar
        year_min = int(df["_year"].min()) if "_year" in df.columns else int(df["过账日期"].dt.year.min())
        year_max = int(df["_year"].max()) if "_year" in df.columns else int(df["过账日期"].dt.year.max())
        holidays = chinese_calendar.get_holidays(range(year_min, year_max + 2))
        holiday_set = {pd.Timestamp(h) for h in holidays}
        holiday_count = int(df["过账日期"].dropna().isin(holiday_set).sum())
    except Exception:
        pass

    # 凌晨录入（0-6点）— 仅当有录入时间列时
    night_count = 0
    if "录入时间" in df.columns:
        time_mask = df["录入时间"].astype(str).str.match(r"^\d{2}:\d{2}:\d{2}$")
        hours = df.loc[time_mask, "录入时间"].astype(str).str[:2].astype(int)
        night_count = int((hours < 6).sum())

    # 12月 vs 前11月均值（年末突击指标）
    dec_count = int(monthly_count.get(12, 0))
    other_months = [int(v) for k, v in monthly_count.items() if k not in (12, 13)]
    avg_other = sum(other_months) / len(other_months) if other_months else 0

    return {
        "monthly_count": {int(k): int(v) for k, v in monthly_count.items()},
        "monthly_amount": {int(k): float(v) for k, v in monthly_amount.items()},
        "month_end_concentration": month_end_ratios,
        "weekend_entry_count": weekend_count,
        "holiday_entry_count": holiday_count,
        "night_entry_count": night_count,
        "dec_vs_avg_multiplier": round(dec_count / avg_other, 2) if avg_other > 0 else 0,
    }


def _voucher_type_structure(df: pd.DataFrame) -> dict:
    """凭证类型分布：系统自动 vs 手工录入。"""
    if "凭证类型" not in df.columns:
        return {}

    counts = df["凭证类型"].value_counts()

    # 凭证级统计
    voucher_type_count = df.groupby("凭证类型")[_voucher_id_col(df)].nunique()

    manual_types: dict[str, int] = {}
    auto_types: dict[str, int] = {}
    for vtype, count in counts.items():
        if vtype in AUTO_VOUCHER_TYPES:
            auto_types[str(vtype)] = int(count)
        else:
            manual_types[str(vtype)] = int(count)

    manual_total = sum(manual_types.values())

    return {
        "all": {str(k): int(v) for k, v in counts.items()},
        "voucher_count_by_type": {str(k): int(v) for k, v in voucher_type_count.items()},
        "manual": manual_types,
        "auto": auto_types,
        "manual_ratio": round(manual_total / len(df), 4) if len(df) > 0 else 0,
    }


def _account_structure(df: pd.DataFrame) -> dict:
    """科目结构：按前缀层级分析，适配 10 位科目码。"""
    if "总账科目" not in df.columns:
        return {}

    df = df.copy()
    acct = df["总账科目"].astype(str).str.strip()
    df["_acct1"] = acct.str[:1]   # 一级：1=资产, 2=负债, 3=权益, 5=成本, 6=损益
    df["_acct4"] = acct.str[:4]   # 二级：6001=主营收入, 6401=主营成本, etc.
    df["_acct6"] = acct.str[:6]   # 三级：600101=主营收入-第三方, etc.

    # 一级科目汇总
    by_class = {}
    for cls, grp in df.groupby("_acct1"):
        by_class[str(cls)] = {
            "count": int(len(grp)),
            "total": round(float(grp["凭证货币价值"].abs().sum()), 2),
        }

    # 二级科目 Top20
    top_accounts = df["_acct4"].value_counts().head(20)

    # 三级科目 Top30（更细粒度）
    top_acct6 = df["_acct6"].value_counts().head(30)

    # 关键科目是否存在（按自动分类结果检测，与可视化模块口径一致）
    acct4_set = set(df["_acct4"].astype(str).unique())
    name_col = next(
        (c for c in ("总账科目：长文本", "总账科目：短文本") if c in df.columns),
        None,
    )
    if name_col is not None:
        from audit_engine.account_classifier import (
            CAT_COST,
            CAT_REVENUE,
            auto_classify,
        )
        unique_names = df[name_col].fillna("").astype(str).unique()
        cats_seen = {auto_classify(n) for n in unique_names}
        has_revenue = CAT_REVENUE in cats_seen
        has_cost = CAT_COST in cats_seen
    else:
        has_revenue = False
        has_cost = False
    has_mfg_cost = "8143" in acct4_set  # 制造费用分摊
    has_production_cost = "5001" in acct4_set  # 生产成本

    return {
        "by_class": by_class,
        "top_accounts": {str(k): int(v) for k, v in top_accounts.items()},
        "top_accounts_detail": {str(k): int(v) for k, v in top_acct6.items()},
        "has_revenue_account": has_revenue,
        "has_cost_account": has_cost,
        "has_mfg_cost_account": has_mfg_cost,
        "has_production_cost_account": has_production_cost,
    }


def _user_patterns(df: pd.DataFrame) -> dict:
    """用户操作模式：集中度 + 凭证类型偏好。"""
    if "用户名" not in df.columns:
        return {}

    user_counts = df["用户名"].value_counts()
    total = len(df)

    top_users = user_counts.head(10)

    # 集中度：前3名用户占比
    top3_ratio = round(float(user_counts.head(3).sum() / total), 4) if total > 0 else 0

    # 哪些用户操作过损益科目（6/5开头）
    pnl_users: list[str] = []
    if "总账科目" in df.columns:
        pnl_rows = df[df["总账科目"].astype(str).str[:1].isin(["5", "6"])]
        pnl_users = pnl_rows["用户名"].dropna().unique().tolist()

    # 用户 × 凭证类型交叉（谁在用什么类型的凭证）
    user_voucher_type: dict[str, dict] = {}
    if "凭证类型" in df.columns:
        for user in user_counts.head(5).index:
            udf = df[df["用户名"] == user]
            user_voucher_type[str(user)] = udf["凭证类型"].value_counts().head(5).to_dict()

    return {
        "top_users": {str(k): int(v) for k, v in top_users.items()},
        "total_users": int(user_counts.count()),
        "top3_concentration": top3_ratio,
        "pnl_account_users": [str(u) for u in pnl_users],
        "user_voucher_type_preference": user_voucher_type,
    }


def _vendor_patterns(df: pd.DataFrame) -> dict:
    """供应商交易模式（仅当有供应商数据时）。"""
    if "供应商编号" not in df.columns:
        return {"total_vendors": 0}

    vendor_col = df["供应商编号"].dropna()
    if vendor_col.empty:
        return {"total_vendors": 0}

    vendor_counts = vendor_col.value_counts()

    # 每供应商的交易频率和金额
    vendor_df = df[df["供应商编号"].notna()].copy()
    vcol = _voucher_id_col(vendor_df)
    vendor_stats = vendor_df.groupby("供应商编号").agg(
        txn_count=("凭证货币价值", "count"),
        total_amount=("凭证货币价值", lambda x: round(float(x.abs().sum()), 2)),
        avg_amount=("凭证货币价值", lambda x: round(float(x.abs().mean()), 2)),
        unique_vouchers=(vcol, "nunique"),
    )

    # 同日多笔供应商（化整为零候选）
    high_freq_same_day = 0
    if "过账日期" in df.columns:
        same_day = vendor_df.groupby(["供应商编号", "过账日期"]).size()
        high_freq_same_day = int((same_day >= 3).sum())

    # 供应商单笔金额分布
    vendor_amt = vendor_df["凭证货币价值"].abs()

    return {
        "total_vendors": int(vendor_counts.count()),
        "top_vendors_by_amount": vendor_stats.nlargest(10, "total_amount").to_dict("index"),
        "high_freq_same_day_vendor_days": high_freq_same_day,
        "single_txn_amount_p75": round(float(vendor_amt.quantile(0.75)), 2) if len(vendor_amt) > 0 else 0,
        "single_txn_amount_p90": round(float(vendor_amt.quantile(0.90)), 2) if len(vendor_amt) > 0 else 0,
    }


def _text_patterns(df: pd.DataFrame) -> dict:
    """摘要文本模式分析。使用 文本 列（由 ingestion 从 摘要 映射而来）。"""
    # 合并两个文本列：优先用 文本（摘要），空值用 凭证抬头摘要
    text_col = df.get("文本")
    header_col = df.get("凭证抬头摘要")

    if text_col is None and header_col is None:
        return {}

    if text_col is not None:
        texts = text_col.fillna(header_col if header_col is not None else "")
    elif header_col is not None:
        texts = header_col
    else:
        return {}

    texts = texts.dropna()
    if texts.empty:
        return {}

    non_empty = texts[texts.str.strip().str.len() > 0]
    if non_empty.empty:
        return {}

    # 高频摘要前缀（前8字符，比6字符更能区分业务场景）
    prefix_counts = non_empty.str[:8].value_counts().head(20)

    # 含关键词的行数
    accrual_count = int(non_empty.str.contains("预提|计提", na=False).sum())
    reversal_count = int(non_empty.str.contains("冲销|冲回|红字|反冲", na=False).sum())
    adjustment_count = int(non_empty.str.contains("调整|调账", na=False).sum())
    writeoff_count = int(non_empty.str.contains("差异|结转|分摊", na=False).sum())

    return {
        "top_text_prefixes": {str(k): int(v) for k, v in prefix_counts.items()},
        "accrual_text_count": accrual_count,
        "reversal_text_count": reversal_count,
        "adjustment_text_count": adjustment_count,
        "writeoff_text_count": writeoff_count,
        "total_unique_texts": int(non_empty.nunique()),
        "empty_text_ratio": round(1 - len(non_empty) / len(df), 4) if len(df) > 0 else 0,
    }


def _manual_entry_ratio(df: pd.DataFrame) -> dict:
    """手工凭证 vs 系统自动凭证。"""
    if "凭证类型" not in df.columns:
        return {}

    manual_mask = ~df["凭证类型"].isin(AUTO_VOUCHER_TYPES)
    manual_count = int(manual_mask.sum())
    total = len(df)

    # 凭证级：手工凭证的凭证数
    vcol = _voucher_id_col(df)
    manual_vouchers = int(df.loc[manual_mask, vcol].nunique())
    total_vouchers = int(df[vcol].nunique())

    return {
        "manual_row_count": manual_count,
        "manual_ratio": round(manual_count / total, 4) if total > 0 else 0,
        "manual_voucher_count": manual_vouchers,
        "manual_voucher_ratio": round(manual_vouchers / total_vouchers, 4) if total_vouchers > 0 else 0,
        "auto_row_count": total - manual_count,
        "auto_types_found": sorted(df[df["凭证类型"].isin(AUTO_VOUCHER_TYPES)]["凭证类型"].unique().tolist()),
    }


# ── 财务摘要 ──

def build_financial_summary(df: pd.DataFrame, year: int) -> dict[str, Any]:
    """
    从序时账提取损益类数据，生成财务概况。
    科目体系：10 位码，前 4 位为二级科目，前 6 位为三级科目。
    """
    df = df.copy()
    acct = df["总账科目"].astype(str).str.strip()
    df["_acct4"] = acct.str[:4]
    df["_acct6"] = acct.str[:6]
    df["_acct2"] = acct.str[:2]
    df["_text"] = df["总账科目：长文本"].astype(str).fillna("")
    visual_work = ensure_analysis_columns(df)
    quality = analysis_quality_summary(visual_work)
    if quality.get("mixed_analysis_currency") or quality["mixed_document_currency"]:
        raise ValueError("存在多种凭证币/分析币种，禁止直接汇总金额（请先选择单一报告币种）")
    monthly_pnl_view = monthly_revenue_cost(visual_work)

    # 所有财务图表统一使用结合借贷标识与原始正负推断后的金额，避免各模块口径漂移。
    df["_val"] = visual_work["_amount_raw"].values
    df["_abs"] = visual_work["_amount_abs"].values
    df["_debit_amount"] = visual_work["_debit_abs"].values
    df["_credit_amount"] = visual_work["_credit_abs"].values
    df["_month"] = visual_work["_month"].values

    # 把分类列回填到 df，便于后续按类别筛选
    df["_acct_category"] = visual_work["_acct_category"].values

    from audit_engine.account_classifier import (
        CAT_EXPENSE,
        CAT_FINANCIAL_EXPENSE,
        CAT_RD_EXPENSE,
        CAT_TAX_SURCHARGE,
    )

    def _sum_by_acct4(prefix: str, col: str = "_abs") -> float:
        # 兼容历史调用：4 位精确匹配（用于投资收益 6111 / 营业外 6301/6711 等无对应分类的兜底）
        return float(df[df["_acct4"] == prefix][col].sum())

    def _sum_pnl_by_mask(mask: pd.Series, category: str | None = None) -> float:
        rows = visual_work.loc[mask]
        if category:
            rows = rows[rows["_pnl_category"] == category]
        return float(rows["_pnl_effect"].sum())

    revenue_mask = operating_revenue_mask(visual_work)
    cost_mask = operating_cost_mask(visual_work)

    # ── 收入分类（类别细分用 _pnl_category，主要凭 SAP 标准 6001 才有"主营业务-X"标签）──
    revenue_categorized = {
        "主营业务收入_第三方": _sum_pnl_by_mask(revenue_mask, "主营业务-第三方"),
        "主营业务收入_内部关联方": _sum_pnl_by_mask(revenue_mask, "主营业务-内部关联方"),
        "主营业务收入_外部关联方": _sum_pnl_by_mask(revenue_mask, "主营业务-外部关联方"),
    }
    total_revenue = _sum_pnl_by_mask(revenue_mask)
    revenue = {
        **revenue_categorized,
        # 其他业务收入 = 总收入减去三个主营分类（兼容非 6001 前缀的收入科目）
        "其他业务收入": total_revenue - sum(revenue_categorized.values()),
    }
    revenue["total"] = total_revenue

    # ── 成本分类 ──
    cost_categorized = {
        "主营业务成本_第三方": -_sum_pnl_by_mask(cost_mask, "主营业务-第三方"),
        "主营业务成本_内部关联方": -_sum_pnl_by_mask(cost_mask, "主营业务-内部关联方"),
        "主营业务成本_外部关联方": -_sum_pnl_by_mask(cost_mask, "主营业务-外部关联方"),
    }
    total_cost = -_sum_pnl_by_mask(cost_mask)
    cost = {
        **cost_categorized,
        "其他业务成本": total_cost - sum(cost_categorized.values()),
    }
    cost["total"] = total_cost

    gross_profit = revenue["total"] - cost["total"]
    gross_margin = gross_profit / revenue["total"] if revenue["total"] > 0 else 0

    # ── 费用分类（按总账科目：长文本的关键词匹配）──
    exp_mask = df["_acct_category"].eq(CAT_EXPENSE)
    exp_df = df[exp_mask].copy()
    exp_df["_expense_subcategory"] = exp_df["_text"].map(classify_expense_subcategory)
    expenses: dict[str, float] = {}
    for category, rows in exp_df.groupby("_expense_subcategory"):
        net_amount = float(rows["_val"].sum())
        if abs(net_amount) > 1e-9:
            expenses[str(category)] = net_amount

    # ── 制造费用分摊（8143 明细科目）──
    mfg_expenses: dict[str, float] = {}
    df8143 = df[df["_acct4"] == "8143"]
    if not df8143.empty:
        mfg_by_text = df8143.groupby("_text")["_abs"].sum()
        for txt, val in mfg_by_text.items():
            if val > 0:
                # 去掉 "JZX " 前缀
                clean_name = txt.replace("JZX ", "").strip()
                mfg_expenses[clean_name] = float(val)

    # ── 生产成本结构（5001 子目）──
    production_cost: dict[str, float] = {}
    df5001 = df[df["_acct4"] == "5001"]
    if not df5001.empty:
        prod_by_text = df5001.groupby("_text")["_abs"].sum()
        for txt, val in prod_by_text.items():
            if val > 0:
                clean_name = txt.replace("生产成本-", "").strip()
                production_cost[clean_name] = float(val)

    # ── 其他损益科目（按自动分类）──
    def _sum_by_category(category: str) -> float:
        return float(df.loc[df["_acct_category"].eq(category), "_val"].sum())

    rd_expense = _sum_by_category(CAT_RD_EXPENSE)
    financial_expense = _sum_by_category(CAT_FINANCIAL_EXPENSE)
    investment_income = float(visual_work.loc[investment_income_mask(visual_work), "_pnl_effect"].sum())
    non_operating_income = float(visual_work.loc[non_operating_income_mask(visual_work), "_pnl_effect"].sum())
    non_operating_expense = float(visual_work.loc[non_operating_expense_mask(visual_work), "_amount_raw"].sum())
    tax_surcharge = _sum_by_category(CAT_TAX_SURCHARGE)

    # ── 月度趋势（收入/成本/毛利），与审计可视化总计口径一致 ──
    monthly_by_month = monthly_pnl_view.set_index("月份")
    periods = [int(month) for month in monthly_by_month.index]
    monthly_revenue = {
        int(m): float(monthly_by_month.loc[m, "净收入"]) if m in monthly_by_month.index else 0
        for m in periods
    }
    monthly_cost = {
        int(m): float(-monthly_by_month.loc[m, "净成本影响"]) if m in monthly_by_month.index else 0
        for m in periods
    }
    monthly_gp = {
        int(m): float(monthly_by_month.loc[m, "毛利"]) if m in monthly_by_month.index else 0
        for m in periods
    }
    cost_structure = production_cost if production_cost else mfg_expenses

    return {
        "year": year,
        "revenue": revenue,
        "cost": cost,
        "gross_profit": gross_profit,
        "gross_margin": round(gross_margin, 4),
        "expenses": expenses,
        "cost_structure": cost_structure,
        "mfg_expenses": mfg_expenses,
        "production_cost": production_cost,
        "rd_expense": rd_expense,
        "financial_expense": financial_expense,
        "investment_income": investment_income,
        "non_operating_income": non_operating_income,
        "non_operating_expense": non_operating_expense,
        "tax_surcharge": tax_surcharge,
        "monthly_revenue": monthly_revenue,
        "monthly_cost": monthly_cost,
        "monthly_gp": monthly_gp,
        "rd_ratio": round(rd_expense / revenue["total"], 4) if revenue["total"] > 0 else 0,
    }


# ── 文本摘要（供 LLM 使用）──

def financials_to_summary_text(financials: dict[int, dict]) -> str:
    """将多年财务概况转为 LLM prompt 用的文本摘要。"""
    lines = []
    for year in sorted(financials.keys()):
        f = financials[year]
        rev = f["revenue"]
        cost = f["cost"]
        lines.append(f"\n=== {year}年 财务概况 ===")
        lines.append(
            f"收入: 总额{rev['total']/1e4:,.0f}万 — "
            f"主营(第三方){rev['主营业务收入_第三方']/1e4:,.0f}万 / "
            f"(内部关联){rev['主营业务收入_内部关联方']/1e4:,.0f}万 / "
            f"(外部关联){rev['主营业务收入_外部关联方']/1e4:,.0f}万 / "
            f"其他{rev['其他业务收入']/1e4:,.0f}万"
        )
        lines.append(
            f"成本: 总额{cost['total']/1e4:,.0f}万 — "
            f"主营(第三方){cost['主营业务成本_第三方']/1e4:,.0f}万 / "
            f"(内部关联){cost['主营业务成本_内部关联方']/1e4:,.0f}万 / "
            f"(外部关联){cost['主营业务成本_外部关联方']/1e4:,.0f}万 / "
            f"其他{cost['其他业务成本']/1e4:,.0f}万"
        )
        lines.append(f"毛利: {f['gross_profit']/1e4:,.0f}万，毛利率: {f['gross_margin']:.1%}")
        lines.append(
            f"费用: {', '.join(f'{k}{v/1e4:,.0f}万' for k, v in sorted(f['expenses'].items(), key=lambda x: -x[1]))}"
        )
        if f.get("mfg_expenses"):
            lines.append(
                f"制造费用: {', '.join(f'{k}{v/1e4:,.0f}万' for k, v in sorted(f['mfg_expenses'].items(), key=lambda x: -x[1]) if v > 0)}"
            )
        if f.get("production_cost"):
            lines.append(
                f"生产成本: {', '.join(f'{k}{v/1e4:,.0f}万' for k, v in sorted(f['production_cost'].items(), key=lambda x: -x[1]) if v > 0)}"
            )
        lines.append(f"研发费用: {f['rd_expense']/1e4:,.0f}万 (占收入{f['rd_ratio']:.1%})")
        lines.append(f"财务费用: {f['financial_expense']/1e4:,.0f}万")
        lines.append(f"投资收益: {f['investment_income']/1e4:,.0f}万")
    return "\n".join(lines)


def profiles_to_summary_text(profiles: dict[int, dict]) -> str:
    """将多年画像转为 LLM prompt 用的文本摘要。"""
    lines = []
    for year in sorted(profiles.keys()):
        p = profiles[year]
        ov = p["overview"]
        amt = p["amount_distribution"]
        tp = p["temporal_patterns"]
        vt = p["voucher_type_structure"]
        vd = p.get("vendor_patterns", {})
        me = p.get("manual_entry_ratio", {})
        bf = p.get("benford_first_digit", {})

        lines.append(f"\n=== {year}年 ===")
        lines.append(
            f"总行数: {ov['total_rows']:,}，凭证数: {ov['total_vouchers']:,}，"
            f"平均{ov.get('avg_rows_per_voucher', 0):.1f}行/凭证，"
            f"Period13行数: {ov.get('period13_rows', 0)}"
        )

        # 凭证级金额分布更有审计意义
        v_amt = amt.get("voucher_level", {})
        if v_amt:
            lines.append(
            f"凭证级金额分布 (借方合计): "
            f"P25={v_amt.get('p25',0):,.0f}  P50={v_amt.get('p50',0):,.0f}  P75={v_amt.get('p75',0):,.0f}  "
            f"P90={v_amt.get('p90',0):,.0f}  P95={v_amt.get('p95',0):,.0f}  "
            f"P99={v_amt.get('p99',0):,.0f}  Max={v_amt.get('max',0):,.0f}"
        )

        lines.append(f"大额整数占比: {amt.get('round_number_ratio', 0):.1%}")
        if bf:
            lines.append(
                f"本福特首位数字: 样本{bf.get('sample_size', 0):,}行，"
                f"总偏离度{bf.get('total_variation_distance', 0):.1%}，"
                f"最大偏离首位数字={bf.get('top_deviation_digit')}"
            )

        # 月末集中度
        mec = tp.get("month_end_concentration", {})
        if mec:
            avg_mec = sum(mec.values()) / len(mec)
            lines.append(f"月末最后5天占比（年均）: {avg_mec:.1%}")
            # 标记异常月份
            high_months = [m for m, r in mec.items() if r > 0.6]
            if high_months:
                lines.append(f"月末集中度>60%的月份: {high_months}")

        lines.append(f"节假日过账: {tp.get('holiday_entry_count', 0):,}次，周末过账: {tp.get('weekend_entry_count', 0):,}次")
        lines.append(f"12月/前11月均值: {tp.get('dec_vs_avg_multiplier', 0):.2f}x")

        # 凭证类型
        manual = vt.get("manual", {})
        auto = vt.get("auto", {})
        lines.append(f"手工凭证类型: {manual}")
        lines.append(f"系统凭证类型: {auto}")
        lines.append(f"手工凭证行占比: {me.get('manual_ratio', 0):.1%}，手工凭证数占比: {me.get('manual_voucher_ratio', 0):.1%}")

        if vd and vd.get("total_vendors", 0) > 0:
            lines.append(f"供应商数: {vd['total_vendors']}，同日多笔(>=3)的供应商-日组合: {vd.get('high_freq_same_day_vendor_days', 0)}")
            lines.append(
                f"供应商单笔金额 P75={vd.get('single_txn_amount_p75', 0):,.0f}  "
                f"P90={vd.get('single_txn_amount_p90', 0):,.0f}"
            )

    return "\n".join(lines)
