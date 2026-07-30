from __future__ import annotations

from pathlib import Path

import pytest
from audit_engine.source_assets import (
    SourceAsset,
    build_source_asset,
    persist_source_asset,
    read_source_asset,
)


def test_source_asset_is_content_addressed_and_roundtrips(tmp_path: Path) -> None:
    data = b"immutable workbook bytes"
    asset = build_source_asset("journal.xlsx", data, uploaded_at="2026-07-30T00:00:00+00:00")

    saved = persist_source_asset(tmp_path, asset, data)
    assert saved.exists()
    assert read_source_asset(tmp_path, asset) == data
    assert SourceAsset.from_dict(asset.to_dict()) == asset

    same = build_source_asset("renamed.xlsx", data)
    assert same.asset_id == asset.asset_id
    assert same.sha256 == asset.sha256


def test_source_asset_rejects_metadata_or_disk_tampering(tmp_path: Path) -> None:
    data = b"trusted"
    asset = build_source_asset("journal.xlsx", data)

    with pytest.raises(ValueError, match="元数据"):
        persist_source_asset(tmp_path, asset, b"different")

    saved = persist_source_asset(tmp_path, asset, data)
    saved.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="完整性"):
        read_source_asset(tmp_path, asset)
