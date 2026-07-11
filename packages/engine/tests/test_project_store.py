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
