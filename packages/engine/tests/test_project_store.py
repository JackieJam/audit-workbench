from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from audit_engine.store import ProjectStore
from audit_engine.store.manifest import ProjectManifest


@pytest.fixture
def store(tmp_path: Path) -> ProjectStore:
    return ProjectStore(root=tmp_path)


def test_create_and_list_project(store: ProjectStore) -> None:
    manifest = store.create_project("演示公司_2024")
    assert manifest.project_id
    listed = store.list_projects()
    assert len(listed) == 1
    assert listed[0].project_name == "演示公司_2024"


def test_legacy_manifest_defaults_ingest_history() -> None:
    manifest = ProjectManifest.from_dict({
        "project_id": "legacy",
        "project_name": "旧项目",
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
        "sources": [{"type": "journal", "years": [2024], "rows": 1}],
        "total_rows": 1,
        "years": [2024],
    })

    assert manifest.ingest_runs == []
    assert manifest.current_ingest_run_id == ""
    assert manifest.sources[0].assets == []


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


def test_reingest_preserves_assets_and_appends_replay_runs(store: ProjectStore) -> None:
    pid = store.create_project("来源与回放").project_id
    frame = pd.DataFrame({
        "凭证编号": ["1", "1"],
        "过账日期": pd.to_datetime(["2024-01-01", "2024-01-01"]),
        "借/贷标识": ["S", "H"],
        "凭证货币价值": [100.0, -100.0],
    })
    first_asset = {
        "asset_id": "src_first",
        "original_name": "first.xlsx",
        "sha256": "a" * 64,
        "size_bytes": 10,
        "stored_path": "raw/source_assets/aa/first.xlsx",
        "uploaded_at": "2024-01-01T00:00:00+00:00",
    }
    first_manifest = store.ingest_journal(
        pid,
        {2024: frame},
        column_mapping={"凭证编号": "凭证编号"},
        missing_columns=[],
        year_summary=[],
        source_assets=[first_asset],
    )
    first_run = dict(first_manifest.ingest_runs[0])

    second = frame.copy()
    second["凭证货币价值"] = [250.0, -250.0]
    second_asset = {
        "asset_id": "src_second",
        "original_name": "second.xlsx",
        "sha256": "b" * 64,
        "size_bytes": 12,
        "stored_path": "raw/source_assets/bb/second.xlsx",
        "uploaded_at": "2024-02-01T00:00:00+00:00",
    }
    manifest = store.ingest_journal(
        pid,
        {2024: second},
        column_mapping={"凭证编号": "凭证编号", "金额": "凭证货币价值"},
        missing_columns=["公司代码"],
        year_summary=[{"年份": 2024, "行数": 2}],
        source_assets=[second_asset],
    )

    source = next(item for item in manifest.sources if item.type == "journal")
    assert [asset["asset_id"] for asset in source.assets] == [
        "src_first",
        "src_second",
    ]
    assert source.assets[0]["active"] is False
    assert source.assets[1]["active"] is True
    assert len(manifest.ingest_runs) == 2
    assert manifest.ingest_runs[0] == first_run
    assert manifest.ingest_runs[1]["asset_ids"] == ["src_second"]
    assert manifest.ingest_runs[1]["row_count"] == 2
    assert manifest.ingest_runs[1]["dataset"] == "journal"
    assert manifest.ingest_runs[1]["data_version"] == store.current_data_version(pid)
    assert manifest.ingest_runs[1]["column_mapping_digest"]
    assert manifest.current_ingest_run_id == manifest.ingest_runs[1]["ingest_run_id"]
    assert store.current_ingest_run_id(pid) == manifest.current_ingest_run_id
    assert store.load_state(pid)["current_ingest_run_id"] == manifest.current_ingest_run_id


def test_ingest_commit_failure_restores_previous_population_and_manifest(
    store: ProjectStore,
    monkeypatch,
) -> None:
    pid = store.create_project("导入回滚").project_id
    first = pd.DataFrame({
        "凭证编号": ["1"],
        "过账日期": pd.to_datetime(["2024-01-01"]),
        "借/贷标识": ["S"],
        "凭证货币价值": [100.0],
    })
    store.ingest_journal(
        pid,
        {2024: first},
        column_mapping={},
        missing_columns=[],
        year_summary=[],
    )
    before_manifest = store.load_manifest(pid).to_dict()
    before_state = store.load_state(pid)
    marker = store.project_dir(pid) / "derived" / "keep.txt"
    marker.write_text("old-cache", encoding="utf-8")

    def fail_state_write(*_args, **_kwargs):
        raise RuntimeError("simulated state failure")

    monkeypatch.setattr(store, "_write_state", fail_state_write)
    replacement = first.copy()
    replacement["凭证货币价值"] = [999.0]
    with pytest.raises(RuntimeError, match="simulated state failure"):
        store.ingest_journal(
            pid,
            {2024: replacement},
            column_mapping={},
            missing_columns=[],
            year_summary=[],
        )

    assert store.load_manifest(pid).to_dict() == before_manifest
    assert store.load_state(pid) == before_state
    assert store.load_journal_year(pid, 2024)["凭证货币价值"].tolist() == [100.0]
    assert marker.read_text(encoding="utf-8") == "old-cache"


def test_reingest_state_updater_merges_concurrent_fields(
    store: ProjectStore,
    monkeypatch,
) -> None:
    pid = store.create_project("并发状态合并").project_id
    frame = pd.DataFrame({
        "凭证编号": ["1"],
        "过账日期": pd.to_datetime(["2024-01-01"]),
        "借/贷标识": ["S"],
        "凭证货币价值": [100.0],
    })
    store.ingest_journal(
        pid,
        {2024: frame},
        column_mapping={},
        missing_columns=[],
        year_summary=[],
    )
    original_commit = store._commit_staged_journal_population

    def commit_after_concurrent_marker(project_id, stage, manifest, updater):
        def add_marker(state):
            state["concurrent_case_marker"] = {"case_id": "case-latest"}
            return state

        store.update_state(project_id, add_marker)
        return original_commit(project_id, stage, manifest, updater)

    monkeypatch.setattr(
        store,
        "_commit_staged_journal_population",
        commit_after_concurrent_marker,
    )
    replacement = frame.copy()
    replacement["凭证货币价值"] = [200.0]
    store.ingest_journal(
        pid,
        {2024: replacement},
        column_mapping={},
        missing_columns=[],
        year_summary=[],
    )

    assert store.load_state(pid)["concurrent_case_marker"] == {
        "case_id": "case-latest"
    }


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
