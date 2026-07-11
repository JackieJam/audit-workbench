from __future__ import annotations

import pandas as pd
from audit_engine import rule_engine


def test_splitting_rule_keeps_same_day_and_window_evidence() -> None:
    frame = pd.DataFrame({
        "供应商编号": ["V1"] * 5 + ["V2"] * 5,
        "过账日期": pd.to_datetime(
            ["2024-01-05"] * 5
            + ["2024-02-01", "2024-02-03", "2024-02-05", "2024-02-07", "2024-02-09"]
        ),
        "凭证货币价值": [100.0, 102.0, 98.0, 101.0, 99.0] + [90.0, 91.0, 89.0, 92.0, 88.0],
        "凭证编号": [f"D{i}" for i in range(5)] + [f"W{i}" for i in range(5)],
    })
    cfg = {
        "splitting": {
            "enabled": True,
            "max_single_amount": 200,
            "min_total": 400,
            "window_days": 10,
            "min_txn_count": 5,
        }
    }

    result = rule_engine.rule_splitting(frame, cfg)
    hits = {hit.voucher_id: hit for hit in result.hits}

    assert set(hits) == {f"D{i}" for i in range(5)} | {f"W{i}" for i in range(5)}
    assert hits["D0"].rule_type == "化整为零(同日拆分)"
    assert hits["D0"].line_indices == (0,)
    assert hits["W0"].rule_type == "化整为零(窗口相似)"
    assert hits["W0"].line_indices == (5,)


def test_trade_voucher_facts_preserve_amount_parties_and_line_evidence() -> None:
    frame = pd.DataFrame({
        "凭证编号": ["REV", "REV", "COST", "COST"],
        "过账日期": pd.to_datetime(["2024-06-15"] * 2 + ["2024-06-20"] * 2),
        "总账科目": ["600100", "112200", "640100", "140500"],
        "总账科目：长文本": ["主营业务收入", "应收账款", "主营业务成本", "库存商品"],
        "借/贷标识": ["H", "S", "S", "H"],
        "凭证货币价值": [-1_000.0, 1_000.0, 950.0, -950.0],
        "凭证抬头摘要": ["销售设备", None, "结转设备成本", None],
        "文本": [None, "客户A", None, "客户A"],
        "客户": [None, "C001", None, None],
        "客户科目：姓名 1": [None, "客户A", None, None],
        "供应商编号": [None, None, None, "V001"],
        "供应商科目：名称 1": [None, None, None, "供应商A"],
        "用户名": ["u1", "u1", "u2", "u2"],
        "凭证类型": ["SA", "SA", "ML", "ML"],
        "_year": [2024] * 4,
    })

    facts = rule_engine._build_trade_voucher_facts(frame).set_index("voucher_id")

    assert facts.loc["REV", "revenue_amount"] == 1_000.0
    assert facts.loc["COST", "cost_amount"] == 950.0
    assert facts.loc["REV", "customer"] == "C001 - 客户A"
    assert facts.loc["COST", "vendor"] == "V001 - 供应商A"
    assert facts.loc["REV", "income_line_indices"] == (0,)
    assert facts.loc["COST", "cost_line_indices"] == (2,)
    assert facts.loc["REV", "all_line_indices"] == (0, 1)


def test_financing_trade_scores_only_costs_inside_date_window(monkeypatch) -> None:
    facts = pd.DataFrame([
        {
            "voucher_id": "REV",
            "date": pd.Timestamp("2024-06-15"),
            "year": 2024,
            "text": "销售设备",
            "terms": {"设备"},
            "customer": "客户A",
            "vendor": "",
            "user": "u1",
            "voucher_type": "SA",
            "category": "主营业务",
            "revenue_amount": 1_000.0,
            "cost_amount": 0.0,
            "income_line_indices": (1,),
            "cost_line_indices": (),
            "all_line_indices": (1,),
        },
        {
            "voucher_id": "COST-IN",
            "date": pd.Timestamp("2024-06-20"),
            "year": 2024,
            "text": "设备采购",
            "terms": {"设备"},
            "customer": "",
            "vendor": "客户A",
            "user": "u2",
            "voucher_type": "KR",
            "category": "主营业务",
            "revenue_amount": 0.0,
            "cost_amount": 950.0,
            "income_line_indices": (),
            "cost_line_indices": (2,),
            "all_line_indices": (2,),
        },
        {
            "voucher_id": "COST-OUT",
            "date": pd.Timestamp("2024-01-01"),
            "year": 2024,
            "text": "设备采购",
            "terms": {"设备"},
            "customer": "",
            "vendor": "客户A",
            "user": "u2",
            "voucher_type": "KR",
            "category": "主营业务",
            "revenue_amount": 0.0,
            "cost_amount": 950.0,
            "income_line_indices": (),
            "cost_line_indices": (3,),
            "all_line_indices": (3,),
        },
    ])
    scored: list[str] = []

    monkeypatch.setattr(rule_engine, "_build_trade_voucher_facts", lambda _df: facts)

    def fake_score(_rev, cost, _window_days):
        scored.append(str(cost["voucher_id"]))
        return 0.8, ["测试关系"]

    monkeypatch.setattr(rule_engine, "_score_trade_relation", fake_score)
    result = rule_engine.rule_financing_trade(
        pd.DataFrame(),
        {
            "financing_trade": {
                "min_revenue_amount": 1,
                "window_days": 30,
                "min_match_score": 0.55,
                "low_margin_threshold": 0.1,
            }
        },
    )

    assert scored == ["COST-IN"]
    assert len(result.hits) == 1
    assert result.hits[0].related_voucher_ids == ("COST-IN",)
