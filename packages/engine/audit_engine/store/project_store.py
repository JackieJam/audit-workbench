"""ProjectStore — Parquet + DuckDB local persistence (M0)."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from audit_engine.data_columns import add_analysis_columns
from audit_engine.runtime import storage_root
from audit_engine.store.manifest import ProjectManifest, SourceRecord


def _parquet_safe(df: pd.DataFrame) -> pd.DataFrame:
    """object 列统一转字符串，避免混合 float/str 导致 Parquet 写入失败。"""
    out = df.copy()
    for col in out.select_dtypes(include=["object"]).columns:
        out[col] = out[col].map(lambda v: "" if pd.isna(v) else str(v))
    return out


class ProjectStore:
    """Read/write project data under ~/.audit_tool/projects/<id>/."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = root or storage_root()
        self._projects_dir = self._root / "projects"
        self._projects_dir.mkdir(parents=True, exist_ok=True)

    def projects_dir(self) -> Path:
        return self._projects_dir

    def project_dir(self, project_id: str) -> Path:
        return self._projects_dir / project_id

    def list_projects(self) -> list[ProjectManifest]:
        manifests: list[ProjectManifest] = []
        if not self._projects_dir.exists():
            return manifests
        for path in sorted(self._projects_dir.iterdir()):
            if not path.is_dir():
                continue
            manifest_path = path / "manifest.json"
            if manifest_path.exists():
                try:
                    manifests.append(self.load_manifest(path.name))
                except Exception:
                    continue
        return sorted(manifests, key=lambda m: m.updated_at, reverse=True)

    def create_project(self, name: str, project_id: str | None = None) -> ProjectManifest:
        manifest = ProjectManifest.create(name, project_id)
        pdir = self.project_dir(manifest.project_id)
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "raw" / "years").mkdir(parents=True, exist_ok=True)
        (pdir / "derived").mkdir(exist_ok=True)
        (pdir / "aggregates").mkdir(exist_ok=True)
        self._write_manifest(manifest)
        self._write_state(pdir, {})
        return manifest

    def load_manifest(self, project_id: str) -> ProjectManifest:
        path = self.project_dir(project_id) / "manifest.json"
        if not path.exists():
            raise FileNotFoundError(f"项目不存在：{project_id}")
        data = json.loads(path.read_text(encoding="utf-8"))
        return ProjectManifest.from_dict(data)

    def save_manifest(self, manifest: ProjectManifest) -> None:
        manifest.touch()
        self._write_manifest(manifest)

    def load_state(self, project_id: str) -> dict[str, Any]:
        path = self.project_dir(project_id) / "state.json"
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def save_state(self, project_id: str, state: dict[str, Any]) -> None:
        self._write_state(self.project_dir(project_id), state)

    def load_candidate_pool(self, project_id: str) -> list[dict[str, Any]]:
        pool = self.load_state(project_id).get("candidate_pool")
        return list(pool) if isinstance(pool, list) else []

    def save_candidate_pool(self, project_id: str, pool: list[dict[str, Any]]) -> None:
        state = self.load_state(project_id)
        state["candidate_pool"] = pool
        self.save_state(project_id, state)

    def ingest_journal(
        self,
        project_id: str,
        year_map: dict[int, pd.DataFrame],
        *,
        column_mapping: dict[str, str],
        missing_columns: list[str],
        year_summary: list[dict[str, Any]],
    ) -> ProjectManifest:
        """Write parsed journal years to parquet and update project state."""
        pdir = self.project_dir(project_id)
        if not pdir.exists():
            raise FileNotFoundError(f"项目不存在：{project_id}")

        raw_years = pdir / "raw" / "years"
        if raw_years.exists():
            shutil.rmtree(raw_years)
        raw_years.mkdir(parents=True, exist_ok=True)

        derived = pdir / "derived"
        if derived.exists():
            shutil.rmtree(derived)
        derived.mkdir(exist_ok=True)

        years: list[int] = []
        for year, df in sorted(year_map.items()):
            if df.empty:
                continue
            years.append(int(year))
            _parquet_safe(df).to_parquet(raw_years / f"{year}.parquet", index=False)

        total = sum(
            self._parquet_row_count(raw_years / f"{y}.parquet")
            for y in years
        )
        self._rebuild_unified_parquet(project_id, years)

        manifest = self.load_manifest(project_id)
        manifest.years = years
        manifest.total_rows = total
        now = datetime.now(timezone.utc).isoformat()
        journal_src = next((s for s in manifest.sources if s.type == "journal"), None)
        if journal_src is None:
            manifest.sources.append(
                SourceRecord(type="journal", years=years, rows=total, updated_at=now)
            )
        else:
            journal_src.years = years
            journal_src.rows = total
            journal_src.updated_at = now
        self.save_manifest(manifest)

        state = self.load_state(project_id)
        state["column_mapping"] = column_mapping
        state["missing_columns"] = missing_columns
        state["year_summary"] = year_summary
        self.save_state(project_id, state)
        return manifest

    def save_journal_year(self, project_id: str, year: int, df: pd.DataFrame) -> int:
        """Persist one year's journal lines to raw/years/{year}.parquet."""
        pdir = self.project_dir(project_id)
        if not pdir.exists():
            raise FileNotFoundError(f"项目不存在：{project_id}")

        out = pdir / "raw" / "years" / f"{year}.parquet"
        out.parent.mkdir(parents=True, exist_ok=True)
        _parquet_safe(df).to_parquet(out, index=False)

        manifest = self.load_manifest(project_id)
        years = sorted(set(manifest.years) | {year})
        manifest.years = years

        total = sum(self._parquet_row_count(pdir / "raw" / "years" / f"{y}.parquet") for y in years)

        manifest.total_rows = total
        journal_src = next((s for s in manifest.sources if s.type == "journal"), None)
        now = datetime.now(timezone.utc).isoformat()
        if journal_src is None:
            journal_src = SourceRecord(type="journal", years=years, rows=total, updated_at=now)
            manifest.sources.append(journal_src)
        else:
            journal_src.years = years
            journal_src.rows = total
            journal_src.updated_at = now

        self._rebuild_unified_parquet(project_id, years)
        self.save_manifest(manifest)
        return len(df)

    def get_work_df(self, project_id: str, year: int) -> pd.DataFrame:
        """Load journal year and build analysis-ready work frame (cached in derived/)."""
        # v3: 毛利成本排除生产成本；分类器变更需换文件名以失效旧缓存
        work_version = 3
        pdir = self.project_dir(project_id)
        derived = pdir / "derived" / f"work_v{work_version}_{year}.parquet"
        journal = pdir / "raw" / "years" / f"{year}.parquet"
        if derived.exists() and journal.exists() and derived.stat().st_mtime >= journal.stat().st_mtime:
            return pd.read_parquet(derived)

        df = self.load_journal_year(project_id, year)
        if df.empty:
            return df
        overrides = self.load_state(project_id).get("account_category_overrides") or {}
        if not isinstance(overrides, dict):
            overrides = {}
        work = add_analysis_columns(df, category_overrides=overrides)
        derived.parent.mkdir(parents=True, exist_ok=True)
        _parquet_safe(work).to_parquet(derived, index=False)
        return work

    def load_journal_year(self, project_id: str, year: int) -> pd.DataFrame:
        path = self.project_dir(project_id) / "raw" / "years" / f"{year}.parquet"
        if not path.exists():
            return pd.DataFrame()
        return pd.read_parquet(path)

    def query_journal(
        self,
        project_id: str,
        year: int,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[pd.DataFrame, int]:
        """Server-side pagination via DuckDB over parquet."""
        path = self.project_dir(project_id) / "raw" / "years" / f"{year}.parquet"
        if not path.exists():
            return pd.DataFrame(), 0

        conn = duckdb.connect()
        try:
            total = int(
                conn.execute(
                    "SELECT COUNT(*) FROM read_parquet(?)",
                    [str(path)],
                ).fetchone()[0]
            )
            df = conn.execute(
                "SELECT * FROM read_parquet(?) LIMIT ? OFFSET ?",
                [str(path), limit, offset],
            ).df()
            return df, total
        finally:
            conn.close()

    def delete_project(self, project_id: str) -> None:
        pdir = self.project_dir(project_id)
        if pdir.exists():
            shutil.rmtree(pdir)

    def _rebuild_unified_parquet(self, project_id: str, years: list[int]) -> None:
        frames: list[pd.DataFrame] = []
        for year in years:
            df = self.load_journal_year(project_id, year)
            if not df.empty:
                if "_year" not in df.columns:
                    df = df.copy()
                    df["_year"] = year
                frames.append(df)
        out = self.project_dir(project_id) / "raw" / "unified.parquet"
        if frames:
            _parquet_safe(pd.concat(frames, ignore_index=True)).to_parquet(out, index=False)
        elif out.exists():
            out.unlink()

    def _parquet_row_count(self, path: Path) -> int:
        if not path.exists():
            return 0
        conn = duckdb.connect()
        try:
            return int(conn.execute("SELECT COUNT(*) FROM read_parquet(?)", [str(path)]).fetchone()[0])
        finally:
            conn.close()

    def _write_manifest(self, manifest: ProjectManifest) -> None:
        path = self.project_dir(manifest.project_id) / "manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_state(self, pdir: Path, state: dict[str, Any]) -> None:
        path = pdir / "state.json"
        path.write_text(
            json.dumps(self._jsonable(state), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _jsonable(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {str(k): ProjectStore._jsonable(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [ProjectStore._jsonable(v) for v in obj]
        if hasattr(obj, "item"):
            return obj.item()
        if isinstance(obj, (str, int, float, bool)) or obj is None:
            return obj
        return str(obj)
