from __future__ import annotations

import pandas as pd
import pytest
from audit_engine.account_classifier import (
    CAT_CONTRACT_ASSET,
    CAT_COST_VARIANCE,
    CAT_CREDIT_IMPAIRMENT,
    CAT_DEFERRED_TAX_ASSET,
    CAT_DERIVATIVE_LIABILITY,
    CAT_EMPLOYEE_PAYABLE,
    CAT_EQUITY,
    CAT_FAIR_VALUE_CHANGE,
    CAT_FINANCIAL_EXPENSE,
    CAT_INCOME_TAX,
    CAT_INVESTMENT_INCOME,
    CAT_LONG_TERM_PREPAID,
    CAT_NON_OPERATING_EXPENSE,
    CAT_NON_OPERATING_INCOME,
    CAT_PROVISION,
    CAT_RD_EXPENSE,
    auto_classify,
)
from audit_engine.analysis.adjustment import adjustment_summary
from audit_engine.analysis.balance_sheet import category_month_entries, category_monthly_movement
from audit_engine.analysis.cost_variance import (
    cost_variance_entries,
    cost_variance_summary,
    monthly_cost_variance,
)
from audit_engine.analysis.drilldown import customer_revenue_entries
from audit_engine.analysis.expense import expense_category_entries
from audit_engine.analysis.income_cost import customer_revenue_top, monthly_revenue_cost
from audit_engine.analysis.other_pnl import monthly_other_pnl, other_pnl_entries
from audit_engine.candidate_pool import samples_for_voucher_ids
from audit_engine.data_columns import (
    AMOUNT_MODE_DC_MULTIPLIER,
    AMOUNT_MODE_SIGNED,
    add_analysis_columns,
    analysis_quality_summary,
)
from audit_engine.profiler import build_financial_summary


def _work(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["过账日期"] = pd.to_datetime(df["过账日期"])
    defaults = {
        "过账期间": 1,
        "凭证类型": "SA",
        "文本": "",
        "客户": "",
        "客户科目：姓名 1": "",
        "供应商编号": "",
        "供应商科目：名称 1": "",
        "反记账": "",
    }
    for column, value in defaults.items():
        if column not in df.columns:
            df[column] = value
    return add_analysis_columns(df)


def test_amount_mode_detects_signed_and_dc_multiplier_and_preserves_reversals() -> None:
    signed = _work([
        {"凭证编号": "S1", "过账日期": "2024-01-01", "借/贷标识": "S", "凭证货币价值": 100, "总账科目": "1001", "总账科目：长文本": "现金"},
        {"凭证编号": "S1", "过账日期": "2024-01-01", "借/贷标识": "H", "凭证货币价值": -100, "总账科目": "2001", "总账科目：长文本": "负债"},
    ])
    assert signed["_amount_sign_mode"].iat[0] == AMOUNT_MODE_SIGNED
    assert signed["_amount_raw"].tolist() == [100, -100]

    dc = _work([
        {"凭证编号": "D1", "过账日期": "2024-01-01", "借/贷标识": "S", "凭证货币价值": 100, "总账科目": "1001", "总账科目：长文本": "现金"},
        {"凭证编号": "D1", "过账日期": "2024-01-01", "借/贷标识": "H", "凭证货币价值": 100, "总账科目": "2001", "总账科目：长文本": "负债"},
        {"凭证编号": "D2", "过账日期": "2024-01-02", "借/贷标识": "S", "凭证货币价值": -30, "总账科目": "1001", "总账科目：长文本": "现金"},
        {"凭证编号": "D2", "过账日期": "2024-01-02", "借/贷标识": "H", "凭证货币价值": -30, "总账科目": "2001", "总账科目：长文本": "负债"},
    ])
    assert dc["_amount_sign_mode"].iat[0] == AMOUNT_MODE_DC_MULTIPLIER
    assert dc["_amount_raw"].tolist() == [100, -100, -30, 30]


def test_expense_net_and_subcategories_are_reconciled_without_overlap() -> None:
    work = _work([
        {"凭证编号": "E1", "过账日期": "2024-01-01", "借/贷标识": "S", "凭证货币价值": 100, "总账科目": "660201", "总账科目：长文本": "管理费用-差旅交通运输费"},
        {"凭证编号": "E1", "过账日期": "2024-01-01", "借/贷标识": "H", "凭证货币价值": 100, "总账科目": "100201", "总账科目：长文本": "银行存款"},
        {"凭证编号": "E2", "过账日期": "2024-01-02", "借/贷标识": "H", "凭证货币价值": 40, "总账科目": "660201", "总账科目：长文本": "管理费用-差旅交通运输费"},
        {"凭证编号": "E2", "过账日期": "2024-01-02", "借/贷标识": "S", "凭证货币价值": 40, "总账科目": "100201", "总账科目：长文本": "银行存款"},
    ])
    financial = build_financial_summary(work, 2024)
    assert financial["expenses"] == {"差旅费": 60.0}
    detail = expense_category_entries(work, "差旅费")
    assert detail["费用发生额"].sum() == 60
    assert expense_category_entries(work, "运输费").empty


def test_period13_customer_context_and_monthly_totals_reconcile() -> None:
    work = _work([
        {"凭证编号": "R1", "过账日期": "2024-02-01", "过账期间": 2, "借/贷标识": "H", "凭证货币价值": 200, "总账科目": "600101", "总账科目：长文本": "主营业务收入"},
        {"凭证编号": "R1", "过账日期": "2024-02-01", "过账期间": 2, "借/贷标识": "S", "凭证货币价值": 200, "总账科目": "112201", "总账科目：长文本": "应收账款", "客户": "C1", "客户科目：姓名 1": "客户甲"},
        {"凭证编号": "R13", "过账日期": "2024-12-31", "过账期间": 13, "借/贷标识": "H", "凭证货币价值": 300, "总账科目": "600101", "总账科目：长文本": "主营业务收入"},
        {"凭证编号": "R13", "过账日期": "2024-12-31", "过账期间": 13, "借/贷标识": "S", "凭证货币价值": 300, "总账科目": "112201", "总账科目：长文本": "应收账款", "客户": "C2", "客户科目：姓名 1": "客户乙"},
    ])
    monthly = monthly_revenue_cost(work)
    assert monthly["月份"].tolist()[-1] == 13
    assert monthly["净收入"].sum() == 500
    assert build_financial_summary(work, 2024)["revenue"]["total"] == 500

    customers = customer_revenue_top(work)
    assert set(customers["客户"]) == {"C1 - 客户甲", "C2 - 客户乙"}
    detail = customer_revenue_entries(work, customer="C1 - 客户甲")
    assert detail["收入影响"].sum() == 200


def test_other_pnl_chart_and_drilldown_reconcile() -> None:
    work = _work([
        {"凭证编号": "I1", "过账日期": "2024-03-01", "过账期间": 3, "借/贷标识": "H", "凭证货币价值": 50, "总账科目": "611101", "总账科目：长文本": "投资收益"},
        {"凭证编号": "I1", "过账日期": "2024-03-01", "过账期间": 3, "借/贷标识": "S", "凭证货币价值": 50, "总账科目": "100201", "总账科目：长文本": "银行存款"},
        {"凭证编号": "N1", "过账日期": "2024-03-02", "过账期间": 3, "借/贷标识": "H", "凭证货币价值": 20, "总账科目": "630101", "总账科目：长文本": "营业外收入"},
        {"凭证编号": "N1", "过账日期": "2024-03-02", "过账期间": 3, "借/贷标识": "S", "凭证货币价值": 20, "总账科目": "100201", "总账科目：长文本": "银行存款"},
        {"凭证编号": "X1", "过账日期": "2024-03-03", "过账期间": 3, "借/贷标识": "S", "凭证货币价值": 10, "总账科目": "671101", "总账科目：长文本": "营业外支出"},
        {"凭证编号": "X1", "过账日期": "2024-03-03", "过账期间": 3, "借/贷标识": "H", "凭证货币价值": 10, "总账科目": "100201", "总账科目：长文本": "银行存款"},
    ])
    march = monthly_other_pnl(work).query("月份 == 3").iloc[0]
    assert march["投资收益"] == 50
    assert march["营业外收入"] == 20
    assert march["营业外支出"] == 10
    assert march["净影响"] == 60
    assert other_pnl_entries(work, month=3, metric="investment_income")["投资收益"].sum() == 50


def test_other_pnl_includes_fair_value_impairment_and_income_tax_without_overlap() -> None:
    work = _work([
        {"凭证编号": "F1", "过账日期": "2024-03-01", "借/贷标识": "H", "凭证货币价值": 30, "总账科目": "610101", "总账科目：长文本": "公允价值变动损益"},
        {"凭证编号": "F1", "过账日期": "2024-03-01", "借/贷标识": "S", "凭证货币价值": 30, "总账科目": "110101", "总账科目：长文本": "交易性金融资产"},
        {"凭证编号": "L1", "过账日期": "2024-03-02", "借/贷标识": "S", "凭证货币价值": 8, "总账科目": "670201", "总账科目：长文本": "信用减值损失-应收账款"},
        {"凭证编号": "L1", "过账日期": "2024-03-02", "借/贷标识": "H", "凭证货币价值": 8, "总账科目": "123101", "总账科目：长文本": "坏账准备"},
        {"凭证编号": "T1", "过账日期": "2024-03-03", "借/贷标识": "S", "凭证货币价值": 12, "总账科目": "680101", "总账科目：长文本": "所得税费用"},
        {"凭证编号": "T1", "过账日期": "2024-03-03", "借/贷标识": "H", "凭证货币价值": 12, "总账科目": "222101", "总账科目：长文本": "应交税费"},
    ])
    march = monthly_other_pnl(work).query("月份 == 3").iloc[0]
    assert march["公允价值变动损益"] == 30
    assert march["信用减值损失"] == 8
    assert march["所得税费用"] == 12
    assert march["净影响"] == 10
    assert build_financial_summary(work, 2024)["expenses"] == {}


def test_other_pnl_has_distinct_categories_and_accepts_manual_mapping() -> None:
    assert auto_classify("投资收益") == CAT_INVESTMENT_INCOME
    assert auto_classify("营业外收入") == CAT_NON_OPERATING_INCOME
    assert auto_classify("营业外支出") == CAT_NON_OPERATING_EXPENSE

    raw = pd.DataFrame([
        {
            "凭证编号": "X1",
            "过账日期": pd.Timestamp("2024-03-03"),
            "过账期间": 3,
            "借/贷标识": "S",
            "凭证货币价值": 12,
            "总账科目": "999901",
            "总账科目：长文本": "特殊损失",
        },
    ])
    work = add_analysis_columns(
        raw,
        category_overrides={"999901": CAT_NON_OPERATING_EXPENSE},
    )

    march = monthly_other_pnl(work).query("月份 == 3").iloc[0]
    assert march["营业外支出"] == 12
    detail = other_pnl_entries(work, month=3, metric="non_operating_expense")
    assert detail["营业外支出"].sum() == 12


def test_other_pnl_standard_prefixes_cannot_leak_into_operating_charts() -> None:
    work = _work([
        {
            "凭证编号": "N1",
            "过账日期": "2024-03-01",
            "借/贷标识": "H",
            "凭证货币价值": 20,
            "总账科目": "630101",
            "总账科目：长文本": "其他收入",
        },
        {
            "凭证编号": "X1",
            "过账日期": "2024-03-02",
            "借/贷标识": "S",
            "凭证货币价值": 10,
            "总账科目": "671101",
            "总账科目：长文本": "其他费用",
        },
        {
            "凭证编号": "I1",
            "过账日期": "2024-03-03",
            "借/贷标识": "H",
            "凭证货币价值": 30,
            "总账科目": "611101",
            "总账科目：长文本": "投资业务收入",
        },
    ])

    assert work["_acct_category"].tolist() == [
        CAT_NON_OPERATING_INCOME,
        CAT_NON_OPERATING_EXPENSE,
        CAT_INVESTMENT_INCOME,
    ]
    operating = monthly_revenue_cost(work)
    assert operating["净收入"].sum() == 0
    assert operating["净成本"].sum() == 0
    march = monthly_other_pnl(work).query("月份 == 3").iloc[0]
    assert march["营业外收入"] == 20
    assert march["营业外支出"] == 10
    assert march["投资收益"] == 30


def test_specific_financial_semantics_precede_generic_name_fragments() -> None:
    expected = {
        "信用减值损失-应收账款": CAT_CREDIT_IMPAIRMENT,
        "所得税费用-递延所得税": CAT_INCOME_TAX,
        "递延所得税资产": CAT_DEFERRED_TAX_ASSET,
        "衍生金融负债": CAT_DERIVATIVE_LIABILITY,
        "预计负债": CAT_PROVISION,
        "合同资产": CAT_CONTRACT_ASSET,
        "公允价值变动损益": CAT_FAIR_VALUE_CHANGE,
        "差异-差异结转": CAT_COST_VARIANCE,
        "长期待摊费用-装修费": CAT_LONG_TERM_PREPAID,
        "利润分配-应付现金股利或利润": CAT_EQUITY,
        "财务费用-借款利息": CAT_FINANCIAL_EXPENSE,
        "研发费用-工艺开发及设计制造费用": CAT_RD_EXPENSE,
    }
    assert {name: auto_classify(name) for name in expected} == expected

    raw = pd.DataFrame([
        {
            "凭证编号": "P1",
            "过账日期": pd.Timestamp("2024-01-31"),
            "借/贷标识": "S",
            "凭证货币价值": 100,
            "总账科目": "67020101",
            "总账科目：长文本": "信用减值损失-应收账款",
        },
        {
            "凭证编号": "P2",
            "过账日期": pd.Timestamp("2024-01-31"),
            "借/贷标识": "S",
            "凭证货币价值": 100,
            "总账科目": "18110201",
            "总账科目：长文本": "递延所得税资产",
        },
        {
            "凭证编号": "P3",
            "过账日期": pd.Timestamp("2024-01-31"),
            "借/贷标识": "S",
            "凭证货币价值": 100,
            "总账科目": "69100702",
            "总账科目：长文本": "费用-固定资产折旧费-机器设备",
        },
    ])
    work = add_analysis_columns(
        raw,
        category_overrides={"67020101": "应收", "18110201": "固定资产"},
    )
    assert work["_acct_category"].tolist() == [
        CAT_CREDIT_IMPAIRMENT,
        CAT_DEFERRED_TAX_ASSET,
        "费用",
    ]


def test_cost_variance_is_reconciled_by_destination_not_mixed_into_gross_margin() -> None:
    work = _work([
        {
            "凭证编号": "V1",
            "过账日期": "2024-01-31",
            "借/贷标识": "S",
            "凭证货币价值": 100,
            "总账科目": "6990099101",
            "总账科目：长文本": "差异-差异结转",
        },
        {
            "凭证编号": "V1",
            "过账日期": "2024-01-31",
            "借/贷标识": "H",
            "凭证货币价值": 60,
            "总账科目": "64019801",
            "总账科目：长文本": "主营业务成本-其他",
        },
        {
            "凭证编号": "V1",
            "过账日期": "2024-01-31",
            "借/贷标识": "H",
            "凭证货币价值": 40,
            "总账科目": "14050101",
            "总账科目：长文本": "库存商品",
        },
    ])

    january = monthly_cost_variance(work).query("月份 == 1").iloc[0]
    assert january["差异科目净额"] == 100
    assert january["差异绝对发生额"] == 100
    assert january["结转营业成本"] == -60
    assert january["结转存货"] == -40
    assert january["期末五日占比"] == 1
    assert cost_variance_summary(work)["cogs_impact"] == -60
    assert cost_variance_entries(work, month=1, metric="cogs")["结转营业成本影响"].sum() == -60

    gross_margin = monthly_revenue_cost(work).query("月份 == 1").iloc[0]
    assert gross_margin["净成本"] == -60
    assert work.loc[work["总账科目"].eq("6990099101"), "_acct_category"].iat[0] == CAT_COST_VARIANCE


def test_quality_review_explains_historical_override_corrected_by_system() -> None:
    work = _work([
        {
            "凭证编号": "Q1",
            "过账日期": "2024-01-31",
            "借/贷标识": "S",
            "凭证货币价值": 100,
            "总账科目": "6990099101",
            "总账科目：长文本": "差异-差异结转",
        },
    ])
    quality = analysis_quality_summary(
        work,
        classification_decisions={
            "6990099101": {
                "decision": "map",
                "category": "成本",
                "rationale": "历史人工映射",
            },
        },
    )
    account = quality["review_accounts"][0]
    assert account["reason"] == "system_corrected"
    assert account["effective_category"] == CAT_COST_VARIANCE
    assert account["mapping_allowed"] is False
    assert quality["review_required_amount"] == 0


def test_multi_customer_voucher_is_not_silently_guessed() -> None:
    work = _work([
        {"凭证编号": "M1", "过账日期": "2024-04-01", "借/贷标识": "H", "凭证货币价值": 200, "总账科目": "600101", "总账科目：长文本": "主营业务收入"},
        {"凭证编号": "M1", "过账日期": "2024-04-01", "借/贷标识": "S", "凭证货币价值": 100, "总账科目": "112201", "总账科目：长文本": "应收账款", "客户": "C1", "客户科目：姓名 1": "客户甲"},
        {"凭证编号": "M1", "过账日期": "2024-04-01", "借/贷标识": "S", "凭证货币价值": 100, "总账科目": "112201", "总账科目：长文本": "应收账款", "客户": "C2", "客户科目：姓名 1": "客户乙"},
    ])
    revenue_row = work[work["总账科目"].eq("600101")].iloc[0]
    assert revenue_row["_customer_display"] == "未维护"
    assert revenue_row["_customer_source"] == "missing"
    assert customer_revenue_top(work).empty


def test_false_reversal_flag_does_not_create_adjustment_and_payables_are_specific() -> None:
    work = _work([
        {"凭证编号": "A1", "过账日期": "2024-01-01", "借/贷标识": "S", "凭证货币价值": 10, "总账科目": "660201", "总账科目：长文本": "管理费用", "反记账": "N", "文本": "正常报销"},
        {"凭证编号": "A1", "过账日期": "2024-01-01", "借/贷标识": "H", "凭证货币价值": 10, "总账科目": "100201", "总账科目：长文本": "银行存款", "反记账": "N", "文本": "正常报销"},
    ])
    assert adjustment_summary(work).empty
    assert auto_classify("应付职工薪酬") == CAT_EMPLOYEE_PAYABLE


def test_reverse_side_amounts_flow_to_balance_chart_and_samples() -> None:
    work = _work([
        {"凭证编号": "B1", "过账日期": "2024-01-01", "借/贷标识": "S", "凭证货币价值": 100, "总账科目": "100201", "总账科目：长文本": "银行存款"},
        {"凭证编号": "B1", "过账日期": "2024-01-01", "借/贷标识": "H", "凭证货币价值": 100, "总账科目": "220201", "总账科目：长文本": "应付账款"},
        {"凭证编号": "B2", "过账日期": "2024-01-02", "借/贷标识": "S", "凭证货币价值": -20, "总账科目": "100201", "总账科目：长文本": "银行存款"},
        {"凭证编号": "B2", "过账日期": "2024-01-02", "借/贷标识": "H", "凭证货币价值": -20, "总账科目": "220201", "总账科目：长文本": "应付账款"},
    ])
    january = category_monthly_movement(work, "货币资金").query("月份 == 1").iloc[0]
    assert january["借方发生额"] == 80
    detail = category_month_entries(work, "货币资金", 1, "debit")
    assert detail["借方发生额"].sum() == 80

    samples = samples_for_voucher_ids({"B1", "B2"}, work)
    debit_amounts = {row["凭证编号"]: row["借方金额"] for row in samples if row["借方金额"]}
    assert debit_amounts == {"B1": 100.0, "B2": -20.0}


def test_mixed_document_currencies_are_blocked_without_company_currency_amount() -> None:
    work = _work([
        {"凭证编号": "C1", "过账日期": "2024-01-01", "借/贷标识": "S", "凭证货币价值": 100, "凭证货币代码": "CNY", "总账科目": "100201", "总账科目：长文本": "银行存款"},
        {"凭证编号": "C1", "过账日期": "2024-01-01", "借/贷标识": "H", "凭证货币价值": 100, "凭证货币代码": "CNY", "总账科目": "600101", "总账科目：长文本": "主营业务收入"},
        {"凭证编号": "C2", "过账日期": "2024-01-02", "借/贷标识": "S", "凭证货币价值": 10, "凭证货币代码": "USD", "总账科目": "100201", "总账科目：长文本": "银行存款"},
        {"凭证编号": "C2", "过账日期": "2024-01-02", "借/贷标识": "H", "凭证货币价值": 10, "凭证货币代码": "USD", "总账科目": "600101", "总账科目：长文本": "主营业务收入"},
    ])
    with pytest.raises(ValueError, match="多种凭证币"):
        build_financial_summary(work, 2024)
