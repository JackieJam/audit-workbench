from __future__ import annotations

import random

import pandas as pd
import pytest
from audit_engine.candidate_pool import sample_from_pool
from audit_engine.data_columns import ensure_analysis_columns
from audit_engine.sampling_plan import (
    apply_minimum_coverage,
    build_population_snapshot,
    build_selection_trace,
    normalize_sampling_plan,
)


def test_sampling_plan_validates_strategy_and_population() -> None:
    plan = normalize_sampling_plan({
        "plan_name": "收入截止测试",
        "population_scope": "full_population",
        "strategy": "stratified",
        "method": "stratified",
        "size": 30,
        "seed": 7,
        "years": [2024, 2023, 2024],
        "stratify_by": "month",
        "stratify_mode": "equal",
        "coverage_constraints": {"min_per_month": 2, "max_same_risk_signal_ratio": 0.4},
    })
    assert plan.years == (2023, 2024)
    assert plan.size == 30
    assert plan.coverage_constraints.min_per_month == 2

    with pytest.raises(ValueError, match="不一致"):
        normalize_sampling_plan({"strategy": "random", "method": "by_rule"})
    with pytest.raises(ValueError, match="总体范围"):
        normalize_sampling_plan({"population_scope": "candidate_pool"})


def test_population_and_selection_trace_are_versioned_and_reproducible() -> None:
    frame = pd.DataFrame({
        "_voucher_key": ["C1|2024|1", "C1|2024|1", "C1|2024|2"],
        "_year": [2024, 2024, 2024],
        "_amount_abs": [100.0, 100.0, 50.0],
        "_dc": ["S", "H", "S"],
    })
    plan = normalize_sampling_plan({
        "population_scope": "full_population",
        "strategy": "random",
        "method": "random",
        "size": 2,
        "seed": 9,
    })
    snapshot = build_population_snapshot(
        frame,
        project_id="p1",
        plan=plan,
        data_version="dv1",
        classification_revision="cv1",
        currency_scope={"currency": "CNY"},
        rules={"large_amount": {"enabled": True}},
    )
    assert snapshot["voucher_count"] == 2
    assert snapshot["data_version"] == "dv1"
    assert snapshot["amount_absolute"] == 150.0

    samples = [
        {"_voucher_key": "C1|2024|1", "_month": 1, "规则类型": "大额"},
        {"_voucher_key": "C1|2024|2", "_month": 2, "规则类型": "手工"},
    ]
    first = build_selection_trace(plan, snapshot, samples)
    second = build_selection_trace(plan, snapshot, samples)
    assert first["selection_id"] == second["selection_id"]
    assert first["statistical_projection_allowed"] is True
    assert first["selected_voucher_count"] == 2
    assert first["selected_voucher_keys"] == ["C1|2024|1", "C1|2024|2"]
    assert first["selected_voucher_keys_digest"] == second["selected_voucher_keys_digest"]


def test_risk_signal_sampling_is_not_marked_statistically_projectable() -> None:
    plan = normalize_sampling_plan({
        "population_scope": "risk_signals",
        "strategy": "random",
        "method": "random",
        "size": 10,
    })
    trace = build_selection_trace(plan, {"population_id": "pop1"}, [])
    assert trace["inclusion_probability_available"] is False
    assert trace["statistical_projection_allowed"] is False


def test_coverage_constraints_change_selection_and_check_full_population_values() -> None:
    raw = pd.DataFrame({
        "公司代码": ["C1"] * 8,
        "会计年度": [2024] * 8,
        "凭证编号": ["1", "1", "2", "2", "3", "3", "4", "4"],
        "过账日期": pd.to_datetime([
            "2024-01-10", "2024-01-10",
            "2024-01-11", "2024-01-11",
            "2024-02-10", "2024-02-10",
            "2024-02-11", "2024-02-11",
        ]),
        "总账科目": [
            "600100", "112200", "640100", "100100",
            "600100", "112200", "640100", "100100",
        ],
        "总账科目：长文本": [
            "主营业务收入", "应收账款", "主营业务成本", "银行存款",
            "主营业务收入", "应收账款", "主营业务成本", "银行存款",
        ],
        "借/贷标识": ["H", "S"] * 4,
        "凭证货币价值": [-100.0, 100.0] * 4,
        "凭证货币代码": ["CNY"] * 8,
    })
    work = ensure_analysis_columns(raw)
    keys = list(work["_voucher_key"].drop_duplicates())
    plan = normalize_sampling_plan({
        "population_scope": "full_population",
        "strategy": "random",
        "method": "random",
        "size": 4,
        "seed": 17,
        "coverage_constraints": {
            "min_per_month": 1,
            "min_per_account_category": 1,
        },
    })
    selected, disclosures = apply_minimum_coverage(
        work,
        keys[:1],
        size=plan.size,
        constraints=plan.coverage_constraints,
        seed=plan.seed,
    )
    assert len(selected) <= 4
    selected_rows = work[work["_voucher_key"].isin(selected)].drop_duplicates("_voucher_key")
    assert set(selected_rows["_month"]) == {1, 2}
    assert set(selected_rows["_acct_category"]) == set(
        work.drop_duplicates("_voucher_key")["_acct_category"]
    )
    assert {item["constraint"] for item in disclosures} == {
        "min_per_month",
        "min_per_account_category",
    }

    snapshot = build_population_snapshot(
        work,
        project_id="p-coverage",
        plan=plan,
        data_version="dv",
        classification_revision="cv",
        currency_scope={"selected_currency": "CNY"},
        rules={},
    )
    incomplete_samples = [
        {"_voucher_key": keys[0], "_month": 1, "_acct_category": "收入"},
    ]
    trace = build_selection_trace(plan, snapshot, incomplete_samples)
    assert all(check["status"] == "unmet" for check in trace["coverage_checks"])
    assert trace["inclusion_probability_available"] is False
    assert trace["statistical_projection_allowed"] is False
    assert "改变了等概率选择" in trace["projection_boundary"]


def test_coverage_plan_rejects_infeasible_sample_size() -> None:
    raw = pd.DataFrame({
        "公司代码": ["C1"] * 6,
        "会计年度": [2024] * 6,
        "凭证编号": ["1", "1", "2", "2", "3", "3"],
        "过账日期": pd.to_datetime([
            "2024-01-10", "2024-01-10",
            "2024-02-10", "2024-02-10",
            "2024-03-10", "2024-03-10",
        ]),
        "总账科目": ["600100", "112200"] * 3,
        "总账科目：长文本": ["主营业务收入", "应收账款"] * 3,
        "借/贷标识": ["H", "S"] * 3,
        "凭证货币价值": [-100.0, 100.0] * 3,
    })
    work = ensure_analysis_columns(raw)
    plan = normalize_sampling_plan({
        "population_scope": "full_population",
        "strategy": "random",
        "method": "random",
        "size": 2,
        "coverage_constraints": {"min_per_month": 1},
    })
    with pytest.raises(ValueError, match="超过计划样本量"):
        apply_minimum_coverage(
            work,
            [],
            size=plan.size,
            constraints=plan.coverage_constraints,
            seed=plan.seed,
        )


def test_sampling_uses_request_local_rng_and_is_reproducible() -> None:
    raw = pd.DataFrame({
        "公司代码": ["C1"] * 8,
        "会计年度": [2024] * 8,
        "凭证编号": ["1", "1", "2", "2", "3", "3", "4", "4"],
        "过账日期": pd.to_datetime(["2024-01-10"] * 8),
        "总账科目": ["600100", "112200"] * 4,
        "总账科目：长文本": ["主营业务收入", "应收账款"] * 4,
        "借/贷标识": ["H", "S"] * 4,
        "凭证货币价值": [-100.0, 100.0] * 4,
    })
    work = ensure_analysis_columns(raw)
    keys = sorted(work["_voucher_key"].drop_duplicates())
    pool = [{
        "group_id": "population",
        "status": "候选",
        "voucher_keys": keys,
        "voucher_ids": [],
        "source_module": "测试总体",
    }]

    expected_next_global_value = random.Random(913).random()
    random.seed(913)
    first = sample_from_pool(pool, work, method="random", size=2, seed=17)
    assert random.random() == expected_next_global_value

    second = sample_from_pool(pool, work, method="random", size=2, seed=17)
    first_keys = {row["_voucher_key"] for row in first}
    second_keys = {row["_voucher_key"] for row in second}
    assert len(first_keys) == 2
    assert first_keys == second_keys
