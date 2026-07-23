from __future__ import annotations

import pandas as pd
from audit_engine.analysis.drilldown import resolve_drilldown
from audit_engine.data_columns import add_analysis_columns
from audit_engine.profiler import build_profile


def _profile_work() -> pd.DataFrame:
    vouchers = [
        ("A", "2024-01-10", 123.0, 1),
        ("B", "2024-01-28", 245.0, 1),
        ("C", "2024-01-29", 399.0, 1),
        ("D", "2024-01-30", 1500.0, 1),
        ("P13", "2024-12-31", 9000.0, 13),
    ]
    rows: list[dict] = []
    for voucher, date, amount, period in vouchers:
        rows.extend(
            [
                {
                    "凭证编号": voucher,
                    "过账日期": pd.Timestamp(date),
                    "过账期间": period,
                    "凭证货币价值": amount,
                    "借/贷标识": "S",
                    "总账科目": "100201",
                    "总账科目：长文本": "银行存款",
                    "凭证类型": "SA",
                },
                {
                    "凭证编号": voucher,
                    "过账日期": pd.Timestamp(date),
                    "过账期间": period,
                    "凭证货币价值": -amount,
                    "借/贷标识": "H",
                    "总账科目": "220201",
                    "总账科目：长文本": "应付账款",
                    "凭证类型": "SA",
                },
            ]
        )
    return add_analysis_columns(pd.DataFrame(rows))


def test_profile_uses_voucher_level_temporal_and_amount_metrics() -> None:
    profile = build_profile(_profile_work(), 2024)

    assert profile["schema_version"] == 2
    temporal = profile["temporal_patterns"]
    assert temporal["monthly_count"][1] == 4
    assert temporal["monthly_count"][13] == 1
    assert temporal["month_end_concentration"][1] == 0.75
    assert 13 not in temporal["month_end_concentration"]

    amounts = profile["amount_distribution"]
    assert amounts["top_1pct_voucher_count"] == 1
    assert amounts["top_1pct_amount_ratio"] == round(9000 / (123 + 245 + 399 + 1500 + 9000), 4)


def test_benford_uses_one_debit_side_and_supports_drilldown() -> None:
    work = _profile_work()
    benford = build_profile(work, 2024)["benford_first_digit"]

    assert benford["sample_size"] == 5
    assert benford["basis"] == "借方分录绝对额 ≥ 10"
    assert benford["applicable"] is False
    assert benford["observed"][1] == 0.4

    digit_rows = resolve_drilldown(
        work,
        {"kind": "profile_benford_digit", "year": 2024, "digit": 1},
    )
    assert len(digit_rows) == 2
    assert set(digit_rows["首位数字"]) == {1}
    assert set(digit_rows["凭证编号"]) == {"A", "D"}

    month_end_rows = resolve_drilldown(
        work,
        {"kind": "profile_month_end", "year": 2024, "month": 1},
    )
    assert set(month_end_rows["凭证编号"]) == {"B", "C", "D"}
