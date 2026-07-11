"""Project manifest — metadata for a single engagement."""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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

    @classmethod
    def create(cls, project_name: str, project_id: str | None = None) -> ProjectManifest:
        now = datetime.now(timezone.utc).isoformat()
        pid = project_id or _slug(project_name)
        return cls(
            project_id=pid,
            project_name=project_name.strip(),
            created_at=now,
            updated_at=now,
        )

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc).isoformat()

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
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectManifest:
        sources = [
            SourceRecord(
                type=str(s.get("type", "journal")),
                years=[int(y) for y in s.get("years", [])],
                rows=int(s.get("rows", 0)),
                updated_at=str(s.get("updated_at", "")),
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
        )
