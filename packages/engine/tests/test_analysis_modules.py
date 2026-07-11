"""Tests for analysis sub-modules."""

from __future__ import annotations

import pandas as pd

from audit_engine.analysis.adjustment import adjustment_summary
from audit_engine.analysis.balance_sheet import balance_sheet_categories, category_monthly_movement
from audit_engine.analysis.expense import cross_year_expense_table, expense_category_entries
from audit_engine.analysis.working_capital import ap_accrual_monthly, other_receivable_monthly
from audit_engine.data_columns import add_analysis_columns
from audit_engine.profiler import build_financial_summary


def _sample_work() -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "凭证编号": ["1001", "1001", "2001", "2001", "3001", "3001"],
            "过账日期": pd.to_datetime(
                ["2024-01-15", "2024-01-15", "2024-02-10", "2024-02-10", "2024-03-20", "2024-03-20"]
            ),
            "凭证货币价值": [100000.0, -100000.0, 50000.0, -50000.0, 80000.0, -80000.0],
            "借/贷标识": ["S", "H", "S", "H", "S", "H"],
            "总账科目": ["6602010000", "1122010000", "6604010000", "1221010000", "6001010000", "6401010000"],
            "总账科目：长文本": ["管理费用-人工", "应收账款", "研发费用", "其他应收款", "主营业务收入", "主营业务成本"],
            "凭证类型": ["AF", "AF", "SA", "SA", "SA", "SA"],
            "文本": ["工资", "销售", "研发", "往来", "收入", "成本"],
        }
    )
    return add_analysis_columns(df)


def test_expense_cross_year_and_entries() -> None:
    work = _sample_work()
    financial = build_financial_summary(work, 2024)
    table = cross_year_expense_table({2024: financial})
    assert not table.empty
    entries = expense_category_entries(work, "研发费用")
    assert len(entries) >= 1


def test_working_capital_monthly() -> None:
    work = _sample_work()
    ap = ap_accrual_monthly(work)
    assert len(ap) == 12
    orm = other_receivable_monthly(work)
    assert len(orm) == 12


def test_balance_sheet_and_adjustment() -> None:
    work = _sample_work()
    cats = balance_sheet_categories(work)
    assert isinstance(cats, list)
    if cats:
        monthly = category_monthly_movement(work, cats[0])
        assert len(monthly) == 12
    work2 = work.copy()
    work2.loc[work2.index[0], "文本"] = "年末冲销调整"
    work2 = add_analysis_columns(work2)
    summary = adjustment_summary(work2)
    assert len(summary) >= 1
