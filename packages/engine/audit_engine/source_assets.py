"""Immutable source-file assets used by traceable audit evidence."""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SourceAsset:
    asset_id: str
    original_name: str
    sha256: str
    size_bytes: int
    stored_path: str
    uploaded_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceAsset:
        return cls(
            asset_id=str(data["asset_id"]),
            original_name=str(data.get("original_name", "")),
            sha256=str(data["sha256"]),
            size_bytes=int(data.get("size_bytes", 0)),
            stored_path=str(data["stored_path"]),
            uploaded_at=str(data.get("uploaded_at", "")),
        )


def build_source_asset(
    original_name: str,
    data: bytes,
    *,
    uploaded_at: str | None = None,
) -> SourceAsset:
    if not data:
        raise ValueError("来源文件不能为空")
    digest = hashlib.sha256(data).hexdigest()
    suffix = Path(original_name).suffix.lower() or ".bin"
    relative_path = f"raw/source_assets/{digest[:2]}/{digest}{suffix}"
    return SourceAsset(
        asset_id=f"src_{digest[:20]}",
        original_name=Path(original_name).name,
        sha256=digest,
        size_bytes=len(data),
        stored_path=relative_path,
        uploaded_at=uploaded_at or datetime.now(UTC).isoformat(),
    )


def persist_source_asset(project_dir: str | Path, asset: SourceAsset, data: bytes) -> Path:
    """Persist bytes by digest without overwriting a different existing asset."""
    actual_hash = hashlib.sha256(data).hexdigest()
    if actual_hash != asset.sha256 or len(data) != asset.size_bytes:
        raise ValueError("来源文件内容与资产元数据不一致")

    root = Path(project_dir).resolve()
    destination = (root / asset.stored_path).resolve()
    if root not in destination.parents:
        raise ValueError("来源文件保存路径越界")
    destination.parent.mkdir(parents=True, exist_ok=True)

    if destination.exists():
        if hashlib.sha256(destination.read_bytes()).hexdigest() != asset.sha256:
            raise ValueError("已保存来源文件哈希不一致")
        return destination

    fd, temp_name = tempfile.mkstemp(prefix=".source-", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        Path(temp_name).replace(destination)
    finally:
        Path(temp_name).unlink(missing_ok=True)
    return destination


def read_source_asset(project_dir: str | Path, asset: SourceAsset) -> bytes:
    root = Path(project_dir).resolve()
    source_path = (root / asset.stored_path).resolve()
    if root not in source_path.parents:
        raise ValueError("来源文件读取路径越界")
    data = source_path.read_bytes()
    if len(data) != asset.size_bytes or hashlib.sha256(data).hexdigest() != asset.sha256:
        raise ValueError("来源文件完整性校验失败")
    return data
