"""跨年勾稽回归测试。"""

from __future__ import annotations

import pandas as pd
from audit_engine.cross_year import (
    _CROSS_YEAR_DETECTION_DEFAULTS,
    _accrual_reversal_pairs,
    _cross_year_thresholds,
    _manual_entry_trend,
    _revenue_timing_drift,
    _thresholds_signature,
    _yearend_balance_buildup,
    run_cross_year_analysis,
)


def _accrual_df(
    year: int,
    amount: float,
    text: str,
    month: int = 12,
    *,
    vendor: str = "V1",
    account: str = "2202010001",
    dc: str = "H",
) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "凭证编号": [f"{year}-{month}-001"],
            "过账日期": pd.to_datetime([f"{year}-{month:02d}-20"]),
            "文本": [text],
            "凭证货币价值": [amount],
            "总账科目": [account],
            "借/贷标识": [dc],
            "供应商编号": [vendor],
            "凭证类型": ["SA"],
        }
    )


def test_detects_dangling_accrual() -> None:
    year_map = {
        2023: _accrual_df(2023, 2_000_000.0, "年末预提费用"),
        2024: _accrual_df(2024, 5_000.0, "正常费用", month=2, dc="S"),
    }
    findings = _accrual_reversal_pairs(year_map)
    assert any(f.category == "预提冲回配对" for f in findings)


def test_small_accrual_ignored() -> None:
    year_map = {
        2023: _accrual_df(2023, 500.0, "年末预提费用"),
        2024: _accrual_df(2024, 0.0, "无", month=2),
    }
    assert _accrual_reversal_pairs(year_map) == []


def test_full_reversal_not_flagged() -> None:
    year_map = {
        2023: _accrual_df(2023, 1_000_000.0, "年末预提费用", dc="H"),
        2024: _accrual_df(2024, 1_000_000.0, "冲销预提", month=1, dc="S"),
    }
    findings = _accrual_reversal_pairs(year_map)
    assert not any(f.category == "预提冲回配对" for f in findings)


def test_single_year_returns_empty() -> None:
    assert run_cross_year_analysis({2023: _accrual_df(2023, 1_000_000.0, "预提")}) == []


def test_accrual_coverage_threshold_respected() -> None:
    """4 笔预提中 3 笔配对 → 覆盖 75%：阈值 0.80 命中，0.70 不命中。"""
    accruals = pd.concat([
        _accrual_df(2023, 250_000.0, "年末预提费用", vendor=f"V{i}").assign(
            **{"凭证编号": f"2023-12-A{i}"}
        )
        for i in range(4)
    ], ignore_index=True)
    reversals = pd.concat([
        _accrual_df(2024, 250_000.0, "冲销预提", month=1, vendor=f"V{i}", dc="S").assign(
            **{"凭证编号": f"2024-01-R{i}"}
        )
        for i in range(3)
    ], ignore_index=True)
    year_map = {2023: accruals, 2024: reversals}

    flagged_080 = _accrual_reversal_pairs(year_map, coverage_threshold=0.80)
    flagged_070 = _accrual_reversal_pairs(year_map, coverage_threshold=0.70)
    assert any(f.category == "预提冲回配对" for f in flagged_080)
    assert not any(f.category == "预提冲回配对" for f in flagged_070)


def test_accrual_match_window_days_respected() -> None:
    year_map = {
        2023: _accrual_df(2023, 1_000_000.0, "年末预提费用"),
        2024: _accrual_df(2024, 1_000_000.0, "冲销预提", month=4, dc="S"),
    }
    flagged_90 = _accrual_reversal_pairs(year_map, match_window_days=90)
    flagged_120 = _accrual_reversal_pairs(year_map, match_window_days=120)
    assert any(f.category == "预提冲回配对" for f in flagged_90)
    assert not any(f.category == "预提冲回配对" for f in flagged_120)


def _revenue_df(year: int, monthly_amounts: dict[int, float]) -> pd.DataFrame:
    rows = [
        {
            "凭证编号": f"{year}-{month}-R",
            "过账日期": pd.Timestamp(f"{year}-{month:02d}-15"),
            "总账科目": "6001",
            "总账科目：长文本": "主营业务收入",
            "凭证货币价值": amt,
            "借/贷标识": "H" if amt >= 0 else "S",
            "文本": "收入",
            "凭证类型": "SA",
            "供应商编号": "",
        }
        for month, amt in monthly_amounts.items()
    ]
    return pd.DataFrame(rows)


def test_revenue_multiplier_threshold_respected() -> None:
    months = {m: 100_000.0 for m in range(1, 12)}
    months[12] = 165_000.0
    year_map = {
        2023: _revenue_df(2023, months),
        2024: _revenue_df(2024, {1: -50_000.0}),
    }
    flagged_18 = _revenue_timing_drift(year_map, dec_multiplier=1.8)
    flagged_15 = _revenue_timing_drift(year_map, dec_multiplier=1.5)
    assert not any(f.category == "收入跨年确认" for f in flagged_18)
    assert any(f.category == "收入跨年确认" for f in flagged_15)


def test_cross_year_thresholds_defaults_and_detection_block() -> None:
    defaults = _cross_year_thresholds(None)
    assert defaults["coverage_threshold"] == 0.80
    assert defaults["match_window_days"] == 90
    for key, val in _CROSS_YEAR_DETECTION_DEFAULTS.items():
        assert defaults[key] == val


def test_thresholds_signature_includes_detection_keys() -> None:
    sig = dict(_thresholds_signature(_cross_year_thresholds(None)))
    assert "expense_spike_multiplier" in sig


def test_balance_buildup_uses_cumulative_net_not_december_abs() -> None:
    def ar(year: int, debit: float) -> pd.DataFrame:
        return pd.DataFrame([{
            "凭证编号": f"{year}-AR-S",
            "过账日期": pd.Timestamp(f"{year}-06-15"),
            "总账科目": "1122010001",
            "总账科目：长文本": "应收账款",
            "借/贷标识": "S",
            "凭证货币价值": debit,
            "文本": "应收增加",
            "凭证类型": "SA",
            "供应商编号": "",
        }])

    year_map = {2022: ar(2022, 1_000_000.0), 2023: ar(2023, 1_000_000.0)}
    findings = _yearend_balance_buildup(year_map, growth_ratio=1.5)
    assert any(f.category == "应收累计净发生持续累积" for f in findings)
    finding = next(f for f in findings if f.category == "应收累计净发生持续累积")
    assert finding.evidence[2022] == 1_000_000.0
    assert finding.evidence[2023] == 2_000_000.0


def _manual_df(year: int, n_total: int, n_manual: int) -> pd.DataFrame:
    types = ["SA"] * n_manual + ["AA"] * (n_total - n_manual)
    return pd.DataFrame({
        "凭证编号": [f"{year}-{i}" for i in range(n_total)],
        "凭证类型": types,
        "过账日期": pd.to_datetime([f"{year}-06-15"] * n_total),
        "文本": ["x"] * n_total,
        "凭证货币价值": [1000.0] * n_total,
        "总账科目": ["6601"] * n_total,
        "供应商编号": [""] * n_total,
    })


def test_manual_entry_uses_voucher_ratio_not_row_ratio() -> None:
    """同一凭证多行不应抬高手写占比。"""
    # 1 张手工凭证 5 行 + 9 张自动各 1 行 → 凭证占比 10%，行占比 ~35%
    manual_rows = pd.DataFrame({
        "凭证编号": ["M1"] * 5,
        "凭证类型": ["SA"] * 5,
        "过账日期": pd.to_datetime(["2023-06-15"] * 5),
        "文本": ["x"] * 5,
        "凭证货币价值": [1000.0] * 5,
        "总账科目": ["6601"] * 5,
        "供应商编号": [""] * 5,
    })
    auto_rows = _manual_df(2023, 9, 0)
    year_map = {
        2023: pd.concat([manual_rows, auto_rows], ignore_index=True),
        2024: _manual_df(2024, 4, 1),  # 25% voucher
    }
    # 凭证占比 10% → 25%，delta=0.15；阈值 0.15 不触发，0.10 触发
    flagged_015 = _manual_entry_trend(year_map, delta_threshold=0.15)
    flagged_010 = _manual_entry_trend(year_map, delta_threshold=0.10)
    assert not any(f.category == "手工凭证占比持续上升" for f in flagged_015)
    assert any(f.category == "手工凭证占比持续上升" for f in flagged_010)
    finding = next(f for f in flagged_010 if f.category == "手工凭证占比持续上升")
    assert finding.evidence.get("basis") == "voucher_count"
