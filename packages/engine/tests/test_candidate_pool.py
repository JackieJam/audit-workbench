"""Tests for candidate pool."""

from __future__ import annotations

import pandas as pd
from audit_engine.candidate_pool import add_candidate_group, build_candidate_group, pool_stats


def test_build_and_add_candidate_group() -> None:
    detail = pd.DataFrame(
        {
            "凭证编号": ["A1", "A1", "A2"],
            "收入影响": [100.0, -50.0, 200.0],
        }
    )
    group = build_candidate_group(
        title="测试组",
        source_module="收入成本",
        source_view="月度收入成本",
        detail=detail,
        selector={"kind": "monthly_income_cost", "year": 2024, "month": 3, "metric": "revenue"},
        reason="测试",
        tags=["月度"],
    )
    assert group["voucher_count"] == 2
    assert group["row_count"] == 3
    assert group["amount_total"] == 350.0

    pool = add_candidate_group([], group)
    assert len(pool) == 1
    same = build_candidate_group(
        title="测试组更新",
        source_module="收入成本",
        source_view="月度收入成本",
        detail=detail,
        selector={"kind": "monthly_income_cost", "year": 2024, "month": 3, "metric": "revenue"},
    )
    pool = add_candidate_group(pool, same)
    assert len(pool) == 1
    assert pool[0]["title"] == "测试组更新"

    stats = pool_stats(pool)
    assert stats["groups"] == 1
    assert stats["active_vouchers"] == 2


def test_update_candidate_fields() -> None:
    from audit_engine.candidate_pool import update_candidate_fields

    detail = pd.DataFrame({"凭证编号": ["A1"], "收入影响": [100.0]})
    group = build_candidate_group(
        title="测试组",
        source_module="收入成本",
        source_view="月度",
        detail=detail,
        selector={"year": 2024},
        reason="旧理由",
        tags=["旧"],
    )
    pool = update_candidate_fields(
        [group],
        group["group_id"],
        status="排除",
        reason="新理由",
        tags=["a", "b"],
    )
    assert pool[0]["status"] == "排除"
    assert pool[0]["reason"] == "新理由"
    assert pool[0]["tags"] == ["a", "b"]
