"""Project manifest — metadata for a single engagement."""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any


def _slug(name: str) -> str:
    base = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "_", name.strip())
    base = base.strip("_")[:40] or "project"
    digest = uuid.uuid5(uuid.NAMESPACE_DNS, name.strip()).hex[:8]
    return f"{base}_{digest}"


@dataclass
class SourceRecord:
    type: str  # journal | trial_balance | financial_statement | document
    years: list[int] = field(default_factory=list)
    rows: int = 0
    updated_at: str = ""
    assets: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def activate_assets(
        self,
        assets: list[dict[str, Any]],
        *,
        ingest_run_id: str,
        data_version: str,
        committed_at: str,
    ) -> None:
        """保留历史来源资产，并标记本次数据集实际使用的资产。"""
        incoming = {
            str(asset.get("asset_id") or ""): dict(asset)
            for asset in assets
            if isinstance(asset, dict) and str(asset.get("asset_id") or "")
        }
        existing = {
            str(asset.get("asset_id") or ""): asset
            for asset in self.assets
            if isinstance(asset, dict) and str(asset.get("asset_id") or "")
        }
        for asset_id, asset in existing.items():
            asset["active"] = asset_id in incoming
            if asset_id not in incoming:
                continue
            runs = [
                str(value)
                for value in asset.get("ingest_run_ids", [])
                if str(value)
            ]
            if ingest_run_id not in runs:
                runs.append(ingest_run_id)
            asset["ingest_run_ids"] = runs
            asset["last_ingested_at"] = committed_at
            asset["data_version"] = data_version

        for asset_id, raw in incoming.items():
            if asset_id in existing:
                continue
            raw.update({
                "active": True,
                "first_ingested_at": committed_at,
                "last_ingested_at": committed_at,
                "data_version": data_version,
                "ingest_run_ids": [ingest_run_id],
            })
            self.assets.append(raw)


@dataclass
class ProjectManifest:
    project_id: str
    project_name: str
    created_at: str
    updated_at: str
    storage_format: str = "parquet-v1"
    sources: list[SourceRecord] = field(default_factory=list)
    total_rows: int = 0
    years: list[int] = field(default_factory=list)
    ingest_runs: list[dict[str, Any]] = field(default_factory=list)
    current_ingest_run_id: str = ""

    @classmethod
    def create(cls, project_name: str, project_id: str | None = None) -> ProjectManifest:
        now = datetime.now(UTC).isoformat()
        pid = project_id or _slug(project_name)
        return cls(
            project_id=pid,
            project_name=project_name.strip(),
            created_at=now,
            updated_at=now,
        )

    def touch(self) -> None:
        self.updated_at = datetime.now(UTC).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "project_name": self.project_name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "storage_format": self.storage_format,
            "sources": [s.to_dict() for s in self.sources],
            "total_rows": self.total_rows,
            "years": self.years,
            "ingest_runs": self.ingest_runs,
            "current_ingest_run_id": self.current_ingest_run_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectManifest:
        ingest_runs = [
            dict(run)
            for run in data.get("ingest_runs", [])
            if isinstance(run, dict)
        ]
        current_ingest_run_id = str(data.get("current_ingest_run_id", ""))
        if not current_ingest_run_id and ingest_runs:
            current_ingest_run_id = str(ingest_runs[-1].get("ingest_run_id", ""))
        sources = [
            SourceRecord(
                type=str(s.get("type", "journal")),
                years=[int(y) for y in s.get("years", [])],
                rows=int(s.get("rows", 0)),
                updated_at=str(s.get("updated_at", "")),
                assets=[
                    dict(asset)
                    for asset in s.get("assets", [])
                    if isinstance(asset, dict)
                ],
            )
            for s in data.get("sources", [])
            if isinstance(s, dict)
        ]
        return cls(
            project_id=str(data["project_id"]),
            project_name=str(data["project_name"]),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
            storage_format=str(data.get("storage_format", "parquet-v1")),
            sources=sources,
            total_rows=int(data.get("total_rows", 0)),
            years=[int(y) for y in data.get("years", [])],
            ingest_runs=ingest_runs,
            current_ingest_run_id=current_ingest_run_id,
        )
