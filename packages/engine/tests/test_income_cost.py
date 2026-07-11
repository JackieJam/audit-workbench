from __future__ import annotations

import pandas as pd
from audit_engine.analysis.drilldown import monthly_income_cost_entries
from audit_engine.analysis.income_cost import income_cost_categories, monthly_revenue_cost
from audit_engine.data_columns import add_analysis_columns


def _sample_work() -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "凭证编号": ["1", "1", "2", "2", "3", "3"],
            "过账日期": pd.to_datetime(
                ["2024-01-15", "2024-01-15", "2024-02-10", "2024-02-10", "2024-01-20", "2024-01-20"]
            ),
            "借/贷标识": ["H", "S", "H", "S", "S", "H"],
            "凭证货币价值": [-1000.0, 500.0, -2000.0, 800.0, 9000.0, -9000.0],
            "总账科目": [
                "6001010000",
                "6401010000",
                "6001010000",
                "6401010000",
                "5001010000",
                "1405010000",
            ],
            "总账科目：短文本": [
                "主营业务收入-第三方",
                "主营业务成本-第三方",
                "主营业务收入-第三方",
                "主营业务成本-第三方",
                "生产成本-材料",
                "库存商品",
            ],
        }
    )
    return add_analysis_columns(df)


def test_monthly_revenue_cost_shape() -> None:
    work = _sample_work()
    view = monthly_revenue_cost(work)
    assert len(view) == 12
    jan = view.loc[view["月份"] == 1].iloc[0]
    assert jan["净收入"] != 0
    assert "毛利" in view.columns


def test_categories_include_total() -> None:
    cats = income_cost_categories(_sample_work())
    assert cats[0] == "总计"


def test_production_cost_excluded_from_cogs_and_drilldown() -> None:
    work = _sample_work()
    view = monthly_revenue_cost(work)
    jan = view.loc[view["月份"] == 1].iloc[0]
    assert abs(float(jan["净成本"]) - 500.0) < 1e-6

    detail = monthly_income_cost_entries(work, month=1, metric="cost", category="总计")
    names = detail["科目名称"].astype(str).tolist() if not detail.empty else []
    assert all("生产" not in n for n in names)
    assert any("主营业务成本" in n for n in names)
