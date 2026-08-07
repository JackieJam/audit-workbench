"""科目自动分类回归测试 — 锁住优先级与分段匹配不变量。"""

from __future__ import annotations

import pandas as pd
import pytest
from audit_engine.account_classifier import (
    CAT_AP,
    CAT_AP_ACCRUAL,
    CAT_AR,
    CAT_CASH,
    CAT_COST,
    CAT_EXPENSE,
    CAT_MANUFACTURING_COST,
    CAT_NON_OPERATING_INCOME,
    CAT_OTHER_PAYABLE,
    CAT_OTHER_RECEIVABLE,
    CAT_RD_EXPENSE,
    CAT_REVENUE,
    CAT_UNCATEGORIZED,
    apply_prefix_category,
    auto_classify,
    build_account_overview,
    classify_dataframe,
    uncategorized_reason,
)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("应付账款-暂估", CAT_AP_ACCRUAL),
        ("GR/IR 暂估", CAT_AP_ACCRUAL),
        ("其他应收款", CAT_OTHER_RECEIVABLE),
        ("其他应付款", CAT_OTHER_PAYABLE),
        ("应收账款", CAT_AR),
        ("应付账款", CAT_AP),
        ("主营业务收入", CAT_REVENUE),
        ("主营业务成本", CAT_COST),
        ("管理费用", CAT_EXPENSE),
        ("生产成本-人工", CAT_MANUFACTURING_COST),
        ("制造费用-折旧", CAT_MANUFACTURING_COST),
        ("营业外收入", CAT_NON_OPERATING_INCOME),
        ("合同履约成本", "合同成本"),
        ("销售费用-现金折扣", CAT_EXPENSE),
        ("银行存款", CAT_CASH),
        ("研发费用-工资", CAT_RD_EXPENSE),
    ],
)
def test_priority_order(name: str, expected: str) -> None:
    assert auto_classify(name) == expected


def test_bare_keyword_requires_segment_match() -> None:
    """裸「收入」不得把含无关子串的名称误判；分段内才命中。"""
    assert auto_classify("主营业务收入") == CAT_REVENUE
    # 「收入」作为分段后缀
    assert auto_classify("其他业务-收入") == CAT_REVENUE
    # 「费用」分段
    assert auto_classify("销售费用") == CAT_EXPENSE


def test_prefix_upgrades_grir_and_fills_blank_name() -> None:
    assert apply_prefix_category("2202040001", CAT_AP) == CAT_AP_ACCRUAL
    assert apply_prefix_category("6001010001", CAT_UNCATEGORIZED) == CAT_REVENUE
    assert apply_prefix_category("6401010001", CAT_UNCATEGORIZED) == CAT_COST
    assert apply_prefix_category("6001010001", CAT_EXPENSE) == CAT_EXPENSE
    assert apply_prefix_category("5001010001", CAT_UNCATEGORIZED) == CAT_MANUFACTURING_COST


def test_classify_dataframe_applies_grir_prefix() -> None:
    df = pd.DataFrame({
        "总账科目": ["2202040001"],
        "总账科目：长文本": ["应付账款"],
    })
    out = classify_dataframe(df)
    assert out["_acct_category"].tolist() == [CAT_AP_ACCRUAL]


@pytest.mark.parametrize("value", [None, "", "   ", "nan", "NaN", "无关科目"])
def test_uncategorized(value: object) -> None:
    assert auto_classify(value) == CAT_UNCATEGORIZED  # type: ignore[arg-type]


def test_classify_dataframe_is_immutable() -> None:
    df = pd.DataFrame({
        "总账科目": ["1122", "2202"],
        "总账科目：长文本": ["应收账款", "应付账款"],
    })
    out = classify_dataframe(df)
    assert "_acct_category" not in df.columns
    assert out["_acct_category"].tolist() == [CAT_AR, CAT_AP]


def test_classify_dataframe_override_wins() -> None:
    df = pd.DataFrame({
        "总账科目": ["1122"],
        "总账科目：长文本": ["应收账款"],
    })
    out = classify_dataframe(df, overrides={"1122": CAT_COST})
    assert out["_acct_category"].tolist() == [CAT_COST]


def test_classify_dataframe_ignores_invalid_override() -> None:
    df = pd.DataFrame({
        "总账科目": ["1122"],
        "总账科目：长文本": ["应收账款"],
    })
    out = classify_dataframe(df, overrides={"1122": "不存在的类别"})
    assert out["_acct_category"].tolist() == [CAT_AR]


def test_system_protected_override_does_not_downgrade() -> None:
    """制造成本等保护口径不得被人工覆盖降级。"""
    df = pd.DataFrame({
        "总账科目": ["500101"],
        "总账科目：长文本": ["生产成本"],
    })
    out = classify_dataframe(df, overrides={"500101": CAT_COST})
    assert out["_acct_category"].tolist() == [CAT_MANUFACTURING_COST]


def test_build_account_overview_effective_category() -> None:
    df = pd.DataFrame({
        "总账科目": ["1122", "1122", "6001"],
        "总账科目：长文本": ["应收账款", "应收账款", "主营业务收入"],
        "凭证货币价值": [100.0, -50.0, 200.0],
    })
    overview = build_account_overview(df, overrides={"1122": CAT_COST})
    row = overview.set_index("科目编号").loc["1122"]
    assert row["生效分类"] == CAT_COST
    assert int(row["行数"]) == 2
    assert float(row["金额"]) == 150.0


def test_uncategorized_reason_marks_protected_as_intentional() -> None:
    assert uncategorized_reason("500101", "生产成本") == "intentional_exclusion"
    assert uncategorized_reason("999901", "神秘科目") == "needs_mapping"
    assert uncategorized_reason("", "") == "missing_identity"
