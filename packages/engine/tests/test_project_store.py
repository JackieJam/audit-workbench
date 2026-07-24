from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from audit_engine.store import ProjectStore


@pytest.fixture
def store(tmp_path: Path) -> ProjectStore:
    return ProjectStore(root=tmp_path)


def test_create_and_list_project(store: ProjectStore) -> None:
    manifest = store.create_project("演示公司_2024")
    assert manifest.project_id
    listed = store.list_projects()
    assert len(listed) == 1
    assert listed[0].project_name == "演示公司_2024"


def test_save_journal_and_query(store: ProjectStore) -> None:
    manifest = store.create_project("测试项目")
    df = pd.DataFrame(
        {
            "凭证编号": ["1001", "1001", "1002"],
            "过账日期": pd.to_datetime(["2024-01-15", "2024-01-15", "2024-02-01"]),
            "凭证货币价值": [1000.0, -1000.0, 500.0],
            "借/贷标识": ["S", "H", "S"],
        }
    )
    rows = store.save_journal_year(manifest.project_id, 2024, df)
    assert rows == 3

    reloaded = store.load_manifest(manifest.project_id)
    assert reloaded.total_rows == 3
    assert reloaded.years == [2024]

    page, total = store.query_journal(manifest.project_id, 2024, limit=2, offset=0)
    assert total == 3
    assert len(page) == 2

    page2, _ = store.query_journal(manifest.project_id, 2024, limit=2, offset=2)
    assert len(page2) == 1


def test_reingest_invalidates_derived_state_and_versions_data(store: ProjectStore) -> None:
    manifest = store.create_project("重导项目")
    pid = manifest.project_id
    first = pd.DataFrame({
        "凭证编号": ["1", "1"],
        "过账日期": pd.to_datetime(["2024-01-01", "2024-01-01"]),
        "借/贷标识": ["S", "H"],
        "凭证货币价值": [100.0, -100.0],
    })
    store.ingest_journal(pid, {2024: first}, column_mapping={}, missing_columns=[], year_summary=[])
    first_version = store.load_state(pid)["data_version"]

    state = store.load_state(pid)
    state.update({
        "profiles": {"2024": {}},
        "rule_results": [{"rule_name": "旧规则"}],
        "samples": [{"凭证编号": "1"}],
        "candidate_pool": [{"group_id": "old"}],
        "module_insights": {"费用": {}},
        "rules_config": {"max_sample_size": 20},
        "agent_thread": {
            "messages": [{"role": "user", "content": "旧问题"}],
            "pinned_context": {"label": "旧图表"},
        },
    })
    store.save_state(pid, state)

    second = first.copy()
    second["凭证货币价值"] = [200.0, -200.0]
    store.ingest_journal(pid, {2024: second}, column_mapping={}, missing_columns=[], year_summary=[])
    refreshed = store.load_state(pid)
    assert refreshed["data_version"] != first_version
    for key in ("profiles", "rule_results", "samples", "candidate_pool", "module_insights"):
        assert key not in refreshed
    assert refreshed["rules_config"] == {"max_sample_size": 20}
    assert refreshed["agent_thread"]["pinned_context"] is None
    assert "重新导入" in refreshed["agent_thread"]["messages"][-1]["content"]


def test_append_year_invalidates_derived_state_and_updates_version(store: ProjectStore) -> None:
    manifest = store.create_project("追加年度")
    pid = manifest.project_id
    frame = pd.DataFrame({
        "凭证编号": ["1", "1"],
        "过账日期": pd.to_datetime(["2023-01-01", "2023-01-01"]),
        "借/贷标识": ["S", "H"],
        "凭证货币价值": [100.0, -100.0],
    })
    store.save_journal_year(pid, 2023, frame)
    first_version = store.load_state(pid)["data_version"]
    state = store.load_state(pid)
    state["profiles"] = {"2023": {}}
    state["rules_config"] = {"max_sample_size": 20}
    store.save_state(pid, state)

    next_year = frame.copy()
    next_year["过账日期"] = pd.to_datetime(["2024-01-01", "2024-01-01"])
    store.save_journal_year(pid, 2024, next_year)
    refreshed = store.load_state(pid)

    assert refreshed["data_version"] != first_version
    assert "profiles" not in refreshed
    assert refreshed["rules_config"] == {"max_sample_size": 20}


def test_classification_decisions_invalidate_once_and_rebuild_work(store: ProjectStore) -> None:
    manifest = store.create_project("分类决策")
    pid = manifest.project_id
    frame = pd.DataFrame({
        "凭证编号": ["1", "1"],
        "过账日期": pd.to_datetime(["2024-01-01", "2024-01-01"]),
        "借/贷标识": ["S", "H"],
        "凭证货币价值": [100.0, -100.0],
        "总账科目": ["999901", "999902"],
        "总账科目：短文本": ["待判断科目", "对方科目"],
    })
    store.ingest_journal(pid, {2024: frame}, column_mapping={}, missing_columns=[], year_summary=[])
    store.get_work_df(pid, 2024)
    state = store.load_state(pid)
    state["profiles"] = {"2024": {"old": True}}
    state["candidate_pool"] = [{"group_id": "old"}]
    store.save_state(pid, state)

    result = store.apply_account_classification_decisions(
        pid,
        [
            {"account_code": "999901", "decision": "map", "category": "费用"},
            {"account_code": "999902", "decision": "defer"},
        ],
    )

    refreshed = store.load_state(pid)
    assert result["applied_count"] == 2
    assert refreshed["account_category_overrides"]["999901"] == "费用"
    assert refreshed["account_classification_decisions"]["999902"]["decision"] == "defer"
    assert "profiles" not in refreshed
    assert "candidate_pool" not in refreshed
    rebuilt = store.get_work_df(pid, 2024)
    mapped = rebuilt.loc[rebuilt["_acct"].eq("999901"), "_acct_category"]
    assert mapped.eq("费用").all()


def test_legacy_project_data_version_is_computed_from_raw_files(store: ProjectStore) -> None:
    manifest = store.create_project("旧项目")
    pid = manifest.project_id
    frame = pd.DataFrame({
        "凭证编号": ["1"],
        "过账日期": pd.to_datetime(["2024-01-01"]),
        "借/贷标识": ["S"],
        "凭证货币价值": [100.0],
    })
    store.save_journal_year(pid, 2024, frame)
    state = store.load_state(pid)
    state.pop("data_version", None)
    store.save_state(pid, state)

    assert store.current_data_version(pid)


def test_intentional_exclusion_cannot_be_mapped_into_generic_category(store: ProjectStore) -> None:
    manifest = store.create_project("排除口径")
    pid = manifest.project_id
    frame = pd.DataFrame({
        "凭证编号": ["1"],
        "过账日期": pd.to_datetime(["2024-01-01"]),
        "借/贷标识": ["H"],
        "凭证货币价值": [100.0],
        "总账科目": ["611101"],
        "总账科目：短文本": ["投资收益"],
    })
    store.ingest_journal(pid, {2024: frame}, column_mapping={}, missing_columns=[], year_summary=[])

    with pytest.raises(ValueError, match="不能映射"):
        store.apply_account_classification_decisions(
            pid,
            [{"account_code": "611101", "decision": "map", "category": "收入"}],
        )
