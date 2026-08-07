"""ProjectStore — Parquet + DuckDB local persistence (M0)."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import tempfile
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from audit_engine.data_columns import add_analysis_columns
from audit_engine.locking import file_lock
from audit_engine.runtime import storage_root, workbench_version
from audit_engine.store.manifest import ProjectManifest, SourceRecord


def _parquet_safe(df: pd.DataFrame) -> pd.DataFrame:
    """object 列统一转字符串，避免混合 float/str 导致 Parquet 写入失败。"""
    out = df.copy()
    for col in out.columns:
        dtype = out[col].dtype
        if pd.api.types.is_object_dtype(dtype) or pd.api.types.is_string_dtype(dtype):
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

    def current_data_version(self, project_id: str) -> str:
        """返回可核验的数据版本；旧项目缺字段时按原始 Parquet 即时计算。"""
        cached = str(self.load_state(project_id).get("data_version") or "").strip()
        if cached:
            return cached
        manifest = self.load_manifest(project_id)
        return self._dataset_version(project_id, manifest.years) if manifest.years else ""

    def current_ingest_run_id(self, project_id: str) -> str:
        """返回当前原始数据对应的可回放导入批次。"""
        return self.load_manifest(project_id).current_ingest_run_id

    def persist_rule_run_results(
        self,
        project_id: str,
        rule_run_id: str,
        results: list[dict[str, Any]],
        *,
        expected_hash: str,
    ) -> str:
        """按运行 ID 保存不可变的完整规则命中，并返回项目内相对路径。"""
        run_id = str(rule_run_id).strip()
        suffix = run_id.removeprefix("rr_")
        if not run_id.startswith("rr_") or not suffix or not suffix.isalnum():
            raise ValueError("规则运行 ID 非法")
        payload = json.dumps(
            self._jsonable(results),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        actual_hash = hashlib.sha256(payload).hexdigest()
        if actual_hash != str(expected_hash):
            raise ValueError("规则结果哈希与待保存内容不一致")

        pdir = self.project_dir(project_id).resolve()
        relative_path = Path("aggregates") / "rule_runs" / f"{run_id}.json.gz"
        destination = (pdir / relative_path).resolve()
        if pdir not in destination.parents:
            raise ValueError("规则结果保存路径越界")
        if destination.exists():
            existing = gzip.decompress(destination.read_bytes())
            if hashlib.sha256(existing).hexdigest() != actual_hash:
                raise ValueError("相同规则运行 ID 已存在不同结果")
            return relative_path.as_posix()

        compressed = gzip.compress(payload, compresslevel=6, mtime=0)
        self._write_atomic_bytes(destination, compressed)
        return relative_path.as_posix()

    def load_rule_run_results(
        self,
        project_id: str,
        *,
        state: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """读取并核验当前规则运行的完整命中；兼容旧版内嵌结果。"""
        current_state = state if isinstance(state, dict) else self.load_state(project_id)
        context = current_state.get("rule_run_context") or {}
        if not isinstance(context, dict) or not context.get("result_artifact"):
            legacy = current_state.get("rule_results") or []
            return list(legacy) if isinstance(legacy, list) else []

        pdir = self.project_dir(project_id).resolve()
        source = (pdir / str(context["result_artifact"])).resolve()
        if pdir not in source.parents:
            raise ValueError("规则结果读取路径越界")
        if not source.exists():
            raise ValueError("规则结果事实文件缺失，不能继续抽样或形成规则证据")
        try:
            payload = gzip.decompress(source.read_bytes())
        except (OSError, EOFError) as exc:
            raise ValueError("规则结果事实文件损坏") from exc
        actual_hash = hashlib.sha256(payload).hexdigest()
        if actual_hash != str(context.get("result_hash") or ""):
            raise ValueError("规则结果事实文件哈希校验失败")
        decoded = json.loads(payload.decode("utf-8"))
        if not isinstance(decoded, list):
            raise ValueError("规则结果事实文件结构无效")
        return decoded

    def persist_verification_run(
        self,
        project_id: str,
        verification_run_id: str,
        payload: dict[str, Any],
        *,
        expected_hash: str,
    ) -> str:
        """按运行 ID 保存不可变 VerificationRun artifact，返回项目内相对路径。"""
        run_id = str(verification_run_id).strip()
        suffix = run_id.removeprefix("vr_")
        if not run_id.startswith("vr_") or not suffix or not all(c.isalnum() for c in suffix):
            raise ValueError("核验运行 ID 非法")
        encoded = json.dumps(
            self._jsonable(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        actual_hash = hashlib.sha256(encoded).hexdigest()
        if actual_hash != str(expected_hash):
            raise ValueError("核验结果哈希与待保存内容不一致")

        pdir = self.project_dir(project_id).resolve()
        relative_path = Path("aggregates") / "verification_runs" / f"{run_id}.json.gz"
        destination = (pdir / relative_path).resolve()
        if pdir not in destination.parents:
            raise ValueError("核验结果保存路径越界")
        if destination.exists():
            existing = gzip.decompress(destination.read_bytes())
            if hashlib.sha256(existing).hexdigest() != actual_hash:
                raise ValueError("相同核验运行 ID 已存在不同结果")
            return relative_path.as_posix()

        compressed = gzip.compress(encoded, compresslevel=6, mtime=0)
        self._write_atomic_bytes(destination, compressed)
        return relative_path.as_posix()

    def load_verification_run(
        self,
        project_id: str,
        *,
        state: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """读取并核验当前 VerificationRun 不可变产物。"""
        current_state = state if isinstance(state, dict) else self.load_state(project_id)
        context = current_state.get("verification_run_context") or {}
        if not isinstance(context, dict) or not context.get("result_artifact"):
            return None
        pdir = self.project_dir(project_id).resolve()
        source = (pdir / str(context["result_artifact"])).resolve()
        if pdir not in source.parents:
            raise ValueError("核验结果读取路径越界")
        if not source.exists():
            raise ValueError("核验结果事实文件缺失")
        try:
            payload = gzip.decompress(source.read_bytes())
        except (OSError, EOFError) as exc:
            raise ValueError("核验结果事实文件损坏") from exc
        actual_hash = hashlib.sha256(payload).hexdigest()
        if actual_hash != str(context.get("result_hash") or ""):
            raise ValueError("核验结果事实文件哈希校验失败")
        decoded = json.loads(payload.decode("utf-8"))
        if not isinstance(decoded, dict):
            raise ValueError("核验结果事实文件结构无效")
        return decoded

    def current_classification_revision(self, project_id: str) -> str:
        """返回分类口径版本；无人工决策也提供稳定基线指纹。"""
        state = self.load_state(project_id)
        cached = str(state.get("classification_revision") or "").strip()
        if cached:
            return cached
        payload = {
            "decisions": state.get("account_classification_decisions") or {},
            "overrides": state.get("account_category_overrides") or {},
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(encoded.encode()).hexdigest()[:16]

    def current_analysis_currency(self, project_id: str) -> str | None:
        value = str(self.load_state(project_id).get("analysis_currency_scope") or "").strip()
        return value or None

    def set_analysis_currency(
        self,
        project_id: str,
        currency: str | None,
        *,
        actor: str = "user",
    ) -> dict[str, Any]:
        """选择单一凭证币作为分析口径；不做任何汇率折算。"""
        manifest = self.load_manifest(project_id)
        requested = str(currency or "").strip().upper()
        available: set[str] = set()
        requires_scope = False
        for year in manifest.years:
            work = self.get_work_df(project_id, year)
            if work.empty or "_amount_currency" not in work.columns:
                continue
            year_currencies = {
                str(value).strip().upper()
                for value in work["_amount_currency"].dropna()
                if str(value).strip() and str(value).strip() != "未维护"
            }
            available.update(year_currencies)
            # 不变量：无论 document / company basis，分析币种 > 1 就必须选单一报告币
            requires_scope = requires_scope or len(year_currencies) > 1
        # 跨年度各自单币但币种不同，同样强制选择
        requires_scope = requires_scope or len(available) > 1
        if requested and requested not in available:
            raise ValueError(f"项目中不存在币种 {requested}；可选币种：{sorted(available)}")
        if not requested and requires_scope:
            raise ValueError("当前项目包含多种分析币种，必须选择一个币种后才能进行金额分析")

        now = datetime.now(UTC).isoformat()

        def update(state: dict[str, Any]) -> dict[str, Any]:
            previous = str(state.get("analysis_currency_scope") or "").strip() or None
            selected = requested or None
            state["analysis_currency_scope"] = selected
            revision_seed = f"{self.current_data_version(project_id)}|{selected or 'all'}"
            state["analysis_scope_revision"] = hashlib.sha256(revision_seed.encode()).hexdigest()[:16]
            if previous != selected:
                from audit_engine.audit_case import mark_case_evidence_stale_in_state

                mark_case_evidence_stale_in_state(
                    state,
                    reason=(
                        f"财务画像分析币种由 {previous or '全部'} 切换为 {selected or '全部'}；"
                        "原证据仍保留，但需在新币种口径下重新执行。"
                    ),
                    trigger="analysis_currency_changed",
                    at=now,
                )
                for key in (
                    "profiles",
                    "financials",
                    "cross_year_findings",
                    "rule_results",
                    "rule_run_context",
                    "samples",
                    "sampling_plan",
                    "llm_judgments",
                    "module_insights",
                    "module_insight_jobs",
                    "rule_tuning_suggestions",
                ):
                    state.pop(key, None)
                thread = dict(state.get("agent_thread") or {})
                thread["pinned_context"] = None
                from audit_engine.agent.sessions import append_active_notice

                append_active_notice(
                    state,
                    (
                        f"财务画像分析口径已切换为 {selected} 单币种；"
                        "旧画像、规则结果与模块分析已失效，请基于当前币种重新分析。"
                    ),
                    at=now,
                )
                thread["updated_at"] = now
                thread.pop("messages", None)
                state["agent_thread"] = thread
            events = list(state.get("analysis_currency_events") or [])
            events.append({
                "at": now,
                "actor": actor,
                "previous": previous,
                "selected": selected,
                "data_version": self.current_data_version(project_id),
            })
            state["analysis_currency_events"] = events[-200:]
            return state

        state = self.update_state(project_id, update)
        return {
            "selected_currency": state.get("analysis_currency_scope"),
            "available_currencies": sorted(available),
            "analysis_scope_revision": state.get("analysis_scope_revision"),
            "invalidated": [
                "profiles",
                "financials",
                "cross_year_findings",
                "rule_results",
                "samples",
                "module_insights",
            ],
        }

    def save_state(self, project_id: str, state: dict[str, Any]) -> None:
        pdir = self.project_dir(project_id)
        with file_lock(pdir / ".state.lock"):
            self._write_state(pdir, state)

    def update_state(
        self,
        project_id: str,
        updater: Callable[[dict[str, Any]], dict[str, Any] | None],
    ) -> dict[str, Any]:
        """在同一文件锁内完成 state 的读取、修改和原子写入。"""
        pdir = self.project_dir(project_id)
        path = pdir / "state.json"
        with file_lock(pdir / ".state.lock"):
            if path.exists():
                state = json.loads(path.read_text(encoding="utf-8"))
            else:
                state = {}
            updated = updater(state)
            next_state = updated if isinstance(updated, dict) else state
            self._write_state(pdir, next_state)
        return next_state

    def apply_account_classification_decisions(
        self,
        project_id: str,
        decisions: list[dict[str, Any]],
        *,
        actor: str = "user",
    ) -> dict[str, Any]:
        """批量保存科目分类决策，并一次性失效所有受影响分析结果。"""
        from audit_engine.account_classifier import (
            ALL_CATEGORIES,
            CAT_UNCATEGORIZED,
            apply_prefix_category,
            auto_classify,
            is_system_protected_category,
            uncategorized_reason,
        )

        if not decisions:
            raise ValueError("至少需要一项分类决策")
        now = datetime.now(UTC).isoformat()
        requested_codes = {
            str(item.get("account_code") or "").strip()
            for item in decisions
            if str(item.get("account_code") or "").strip()
        }
        actual_names: dict[str, str] = {}
        for year in self.load_manifest(project_id).years:
            work = self.get_work_df(project_id, year)
            if work.empty:
                continue
            matched = work.loc[work["_acct"].isin(requested_codes), ["_acct", "_account_name"]]
            for code, names in matched.groupby("_acct")["_account_name"]:
                actual_names[str(code)] = next(
                    (str(name).strip() for name in names if str(name).strip()),
                    "",
                )

        def update(state: dict[str, Any]) -> dict[str, Any]:
            stored = dict(state.get("account_classification_decisions") or {})
            overrides = dict(state.get("account_category_overrides") or {})
            applied: list[dict[str, Any]] = []
            for item in decisions:
                code = str(item.get("account_code") or "").strip()
                decision = str(item.get("decision") or "").strip()
                category = str(item.get("category") or "").strip()
                if not code:
                    raise ValueError("科目编号不能为空")
                if decision not in {"map", "exclude", "defer", "reset"}:
                    raise ValueError(f"不支持的决策类型：{decision}")
                automatic_category = apply_prefix_category(
                    code,
                    auto_classify(actual_names.get(code, "")),
                )
                if (
                    decision != "reset"
                    and is_system_protected_category(automatic_category)
                ):
                    raise ValueError(
                        f"科目 {code} 已由高置信度系统口径归入“{automatic_category}”，"
                        "无需人工分类；如认为源科目名称有误，请先修正源数据"
                    )
                if decision == "map":
                    if category not in ALL_CATEGORIES or category == CAT_UNCATEGORIZED:
                        raise ValueError(f"无效的目标分类：{category}")
                    if (
                        uncategorized_reason(code, actual_names.get(code, ""))
                        == "intentional_exclusion"
                    ):
                        raise ValueError(
                            f"科目 {code} 属于独立分析或系统排除口径，不能映射到通用分类"
                        )
                    overrides[code] = category
                elif decision == "exclude":
                    overrides[code] = CAT_UNCATEGORIZED
                else:
                    overrides.pop(code, None)

                if decision == "reset":
                    stored.pop(code, None)
                    applied.append({"account_code": code, "decision": decision})
                else:
                    record = {
                        "account_code": code,
                        "account_name": (
                            actual_names.get(code)
                            or str(item.get("account_name") or "").strip()
                        ),
                        "decision": decision,
                        "category": category if decision == "map" else None,
                        "rationale": str(item.get("rationale") or "").strip(),
                        "actor": actor,
                        "decided_at": now,
                    }
                    stored[code] = record
                    applied.append(record)

            state["account_category_overrides"] = overrides
            state["account_classification_decisions"] = stored
            self._invalidate_analysis_state(
                state,
                now,
                notification="科目分类口径已更新，旧分析结果已失效；系统将基于新口径重新计算。",
            )
            revision_seed = json.dumps(
                {"decisions": stored, "overrides": overrides},
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
            state["classification_revision"] = hashlib.sha256(
                revision_seed.encode()
            ).hexdigest()[:16]
            events = list(state.get("classification_decision_events") or [])
            events.append({
                "at": now,
                "actor": actor,
                "decisions": applied,
                "classification_revision": state["classification_revision"],
            })
            state["classification_decision_events"] = events[-200:]
            return state

        state = self.update_state(project_id, update)

        derived = self.project_dir(project_id) / "derived"
        for path in derived.glob("work_v*.parquet"):
            path.unlink(missing_ok=True)
        return {
            "applied_count": len(decisions),
            "classification_revision": state["classification_revision"],
            "invalidated": [
                "profiles",
                "financials",
                "cross_year_findings",
                "rule_results",
                "samples",
                "candidate_pool",
                "module_insights",
            ],
        }

    def load_candidate_pool(self, project_id: str) -> list[dict[str, Any]]:
        pool = self.load_state(project_id).get("candidate_pool")
        return list(pool) if isinstance(pool, list) else []

    def save_candidate_pool(self, project_id: str, pool: list[dict[str, Any]]) -> None:
        def update(state: dict[str, Any]) -> dict[str, Any]:
            state["candidate_pool"] = list(pool)
            return state

        self.update_state(project_id, update)

    def ingest_journal(
        self,
        project_id: str,
        year_map: dict[int, pd.DataFrame],
        *,
        column_mapping: dict[str, str],
        missing_columns: list[str],
        year_summary: list[dict[str, Any]],
        source_assets: list[dict[str, Any]] | None = None,
    ) -> ProjectManifest:
        """校验完整的新数据集后切换，并在同一 state 事务内失效旧分析。"""
        pdir = self.project_dir(project_id)
        if not pdir.exists():
            raise FileNotFoundError(f"项目不存在：{project_id}")
        with file_lock(pdir / ".ingest.lock"):
            stage, years, total, data_version = self._stage_journal_population(
                pdir,
                year_map,
                retain_existing=False,
            )
            now = datetime.now(UTC).isoformat()
            ingest_run_id = f"ing_{uuid.uuid4().hex}"
            mapping_snapshot = {
                str(key): str(value)
                for key, value in sorted(column_mapping.items())
            }
            mapping_payload = json.dumps(
                mapping_snapshot,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            asset_ids = sorted({
                str(asset.get("asset_id") or "")
                for asset in source_assets or []
                if isinstance(asset, dict) and str(asset.get("asset_id") or "")
            })
            source_sheets = sorted({
                str(value).strip()
                for frame in year_map.values()
                if "_source_sheet" in frame.columns
                for value in frame["_source_sheet"].dropna()
                if str(value).strip()
            })
            source_files = sorted({
                str(value).strip()
                for frame in year_map.values()
                if "_source_file" in frame.columns
                for value in frame["_source_file"].dropna()
                if str(value).strip()
            })
            ingest_run = {
                "ingest_run_id": ingest_run_id,
                "asset_ids": asset_ids,
                "source_files": source_files,
                "source_sheets": source_sheets,
                "source_coordinates_present": any(
                    "_source_row" in frame.columns for frame in year_map.values()
                ),
                "column_mapping": mapping_snapshot,
                "column_mapping_digest": hashlib.sha256(mapping_payload.encode()).hexdigest(),
                "parser_revision": "journal-xlsx-v1",
                "engine_revision": workbench_version(),
                "years": years,
                "row_count": total,
                "dataset": "journal",
                "data_version": data_version,
                "committed_at": now,
            }

            manifest = ProjectManifest.from_dict(self.load_manifest(project_id).to_dict())
            manifest.years = years
            manifest.total_rows = total
            manifest.updated_at = now
            journal_src = next((s for s in manifest.sources if s.type == "journal"), None)
            if journal_src is None:
                journal_src = SourceRecord(
                    type="journal",
                    years=years,
                    rows=total,
                    updated_at=now,
                )
                manifest.sources.append(journal_src)
            else:
                journal_src.years = years
                journal_src.rows = total
                journal_src.updated_at = now
            journal_src.activate_assets(
                source_assets or [],
                ingest_run_id=ingest_run_id,
                data_version=data_version,
                committed_at=now,
            )
            manifest.ingest_runs.append(ingest_run)
            manifest.current_ingest_run_id = ingest_run_id

            def update(state: dict[str, Any]) -> dict[str, Any]:
                self._invalidate_analysis_state(state, now)
                state["column_mapping"] = mapping_snapshot
                state["missing_columns"] = list(missing_columns)
                state["year_summary"] = list(year_summary)
                state["data_version"] = data_version
                state["current_ingest_run_id"] = ingest_run_id
                state["data_ingested_at"] = now
                state.pop("analysis_currency_scope", None)
                state.pop("analysis_scope_revision", None)
                return state

            self._commit_staged_journal_population(
                project_id,
                stage,
                manifest,
                update,
            )
            return manifest

    @staticmethod
    def _invalidate_analysis_state(
        state: dict[str, Any],
        now: str,
        *,
        notification: str = "项目数据已重新导入，旧分析结果与选中上下文已失效，请基于新数据重新分析。",
    ) -> dict[str, Any]:
        """Drop every result derived from journal contents while preserving user configuration."""
        from audit_engine.audit_case import mark_case_evidence_stale_in_state

        mark_case_evidence_stale_in_state(
            state,
            reason=notification,
            trigger="analysis_state_invalidated",
            at=now,
        )
        for key in (
            "profiles",
            "financials",
            "cross_year_findings",
            "rule_results",
            "rule_run_context",
            "samples",
            "sampling_plan",
            "llm_judgments",
            "candidate_pool",
            "module_insights",
            "module_insight_jobs",
            "rule_tuning_suggestions",
        ):
            state.pop(key, None)
        thread = dict(state.get("agent_thread") or {})
        thread["pinned_context"] = None
        from audit_engine.agent.sessions import append_active_notice

        append_active_notice(state, notification, at=now)
        thread["updated_at"] = now
        thread.pop("messages", None)
        state["agent_thread"] = thread
        return state

    def save_journal_year(self, project_id: str, year: int, df: pd.DataFrame) -> int:
        """原子替换一个年度，并保留其他年度与并发写入的案件状态。"""
        pdir = self.project_dir(project_id)
        if not pdir.exists():
            raise FileNotFoundError(f"项目不存在：{project_id}")
        if df.empty:
            raise ValueError("年度序时账不能为空")

        with file_lock(pdir / ".ingest.lock"):
            stage, years, total, data_version = self._stage_journal_population(
                pdir,
                {int(year): df},
                retain_existing=True,
            )
            now = datetime.now(UTC).isoformat()
            ingest_run_id = f"ing_{uuid.uuid4().hex}"
            current_state = self.load_state(project_id)
            mapping_snapshot = {
                str(key): str(value)
                for key, value in sorted(
                    dict(current_state.get("column_mapping") or {}).items()
                )
            }
            mapping_payload = json.dumps(
                mapping_snapshot,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            ingest_run = {
                "ingest_run_id": ingest_run_id,
                "asset_ids": [],
                "column_mapping": mapping_snapshot,
                "column_mapping_digest": hashlib.sha256(mapping_payload.encode()).hexdigest(),
                "parser_revision": "project-store-year-upsert-v1",
                "engine_revision": workbench_version(),
                "years": years,
                "row_count": total,
                "dataset": "journal",
                "data_version": data_version,
                "committed_at": now,
                "operation": "upsert_year",
                "updated_year": int(year),
            }

            manifest = ProjectManifest.from_dict(self.load_manifest(project_id).to_dict())
            manifest.years = years
            manifest.total_rows = total
            manifest.updated_at = now
            journal_src = next((s for s in manifest.sources if s.type == "journal"), None)
            if journal_src is None:
                journal_src = SourceRecord(
                    type="journal",
                    years=years,
                    rows=total,
                    updated_at=now,
                )
                manifest.sources.append(journal_src)
            else:
                journal_src.years = years
                journal_src.rows = total
                journal_src.updated_at = now
            manifest.ingest_runs.append(ingest_run)
            manifest.current_ingest_run_id = ingest_run_id

            def update(state: dict[str, Any]) -> dict[str, Any]:
                self._invalidate_analysis_state(state, now)
                state["data_version"] = data_version
                state["current_ingest_run_id"] = ingest_run_id
                state["data_ingested_at"] = now
                state.pop("analysis_currency_scope", None)
                state.pop("analysis_scope_revision", None)
                return state

            self._commit_staged_journal_population(
                project_id,
                stage,
                manifest,
                update,
            )
        return len(df)

    def get_work_df(self, project_id: str, year: int) -> pd.DataFrame:
        """Load journal year and build analysis-ready work frame (cached in derived/)."""
        # v7: 高置信度会计语义、特殊资产负债与成本差异独立分类
        work_version = 7
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

    def get_analysis_work_df(self, project_id: str, year: int) -> pd.DataFrame:
        """按用户选择的单币种口径返回分析数据，不进行跨币种金额合并。"""
        work = self.get_work_df(project_id, year)
        currency = self.current_analysis_currency(project_id)
        if work.empty or not currency or "_amount_currency" not in work.columns:
            return work
        normalized = work["_amount_currency"].fillna("").astype(str).str.strip().str.upper()
        return work.loc[normalized.eq(currency)].copy()

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

    def _stage_journal_population(
        self,
        pdir: Path,
        year_map: dict[int, pd.DataFrame],
        *,
        retain_existing: bool,
    ) -> tuple[Path, list[int], int, str]:
        """在项目同一文件系统内写完并校验一套候选原始数据。"""
        stage = Path(tempfile.mkdtemp(prefix=".ingest-", dir=pdir))
        stage_years = stage / "raw" / "years"
        stage_years.mkdir(parents=True)
        (stage / "derived").mkdir()
        try:
            replacement_years = {int(year) for year in year_map}
            if retain_existing:
                current_years = pdir / "raw" / "years"
                for source in sorted(current_years.glob("*.parquet")):
                    try:
                        source_year = int(source.stem)
                    except ValueError:
                        continue
                    if source_year in replacement_years:
                        continue
                    destination = stage_years / source.name
                    shutil.copy2(source, destination)
                    self._validate_parquet(
                        destination,
                        expected_rows=self._parquet_row_count(source),
                    )

            for year, frame in sorted(year_map.items()):
                if frame.empty:
                    continue
                if len(frame.columns) == 0:
                    raise ValueError(f"{year} 年序时账没有可写入字段")
                destination = stage_years / f"{int(year)}.parquet"
                _parquet_safe(frame).to_parquet(destination, index=False)
                self._validate_parquet(destination, expected_rows=len(frame))

            years: list[int] = []
            for path in sorted(stage_years.glob("*.parquet")):
                try:
                    years.append(int(path.stem))
                except ValueError:
                    continue
            years = sorted(set(years))
            if not years:
                raise ValueError("没有可提交的年度序时账数据")

            total = sum(
                self._parquet_row_count(stage_years / f"{year}.parquet")
                for year in years
            )
            unified = stage / "raw" / "unified.parquet"
            self._write_unified_from_years_dir(stage_years, years, unified)
            self._validate_parquet(unified, expected_rows=total)
            data_version = self._dataset_version_for_years_dir(stage_years, years)
            return stage, years, total, data_version
        except Exception:
            shutil.rmtree(stage, ignore_errors=True)
            raise

    def _commit_staged_journal_population(
        self,
        project_id: str,
        stage: Path,
        manifest: ProjectManifest,
        state_updater: Callable[[dict[str, Any]], dict[str, Any] | None],
    ) -> dict[str, Any]:
        """切换已校验的数据集；原始数据、manifest 与 state 同事务提交。"""
        pdir = self.project_dir(project_id)
        raw = pdir / "raw"
        raw.mkdir(parents=True, exist_ok=True)
        manifest_path = pdir / "manifest.json"
        old_manifest = manifest_path.read_bytes()
        swaps = [
            {
                "new": stage / "raw" / "years",
                "target": raw / "years",
                "backup": stage / "backup-years",
                "old_moved": False,
                "new_installed": False,
            },
            {
                "new": stage / "raw" / "unified.parquet",
                "target": raw / "unified.parquet",
                "backup": stage / "backup-unified.parquet",
                "old_moved": False,
                "new_installed": False,
            },
            {
                "new": stage / "derived",
                "target": pdir / "derived",
                "backup": stage / "backup-derived",
                "old_moved": False,
                "new_installed": False,
            },
        ]
        manifest_written = False
        state_path = pdir / "state.json"
        try:
            # 案件、Agent 与分析结果都使用同一 state 锁；整个切换期间禁止并发
            # 控制面写入，避免观察到“新 Parquet + 旧版本信息”的半提交状态。
            with file_lock(pdir / ".state.lock"):
                current_state = (
                    json.loads(state_path.read_text(encoding="utf-8"))
                    if state_path.exists()
                    else {}
                )
                updated = state_updater(current_state)
                next_state = updated if isinstance(updated, dict) else current_state
                try:
                    for swap in swaps:
                        target = Path(swap["target"])
                        if target.exists():
                            os.replace(target, Path(swap["backup"]))
                            swap["old_moved"] = True
                        os.replace(Path(swap["new"]), target)
                        swap["new_installed"] = True

                    self._write_manifest(manifest)
                    manifest_written = True
                    self._write_state(pdir, next_state)
                    return next_state
                except Exception as exc:
                    rollback_errors: list[Exception] = []
                    for swap in reversed(swaps):
                        try:
                            target = Path(swap["target"])
                            if bool(swap["new_installed"]):
                                self._remove_path(target)
                            if bool(swap["old_moved"]):
                                os.replace(Path(swap["backup"]), target)
                        except Exception as rollback_exc:  # pragma: no cover - filesystem failure
                            rollback_errors.append(rollback_exc)
                    if manifest_written:
                        try:
                            self._write_atomic_bytes(manifest_path, old_manifest)
                        except Exception as rollback_exc:  # pragma: no cover - filesystem failure
                            rollback_errors.append(rollback_exc)
                    if rollback_errors:
                        raise RuntimeError(
                            "导入失败，且旧数据回滚未能完整完成"
                        ) from rollback_errors[0]
                    raise exc
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    def _write_unified_from_years_dir(
        self,
        years_dir: Path,
        years: list[int],
        output: Path,
    ) -> None:
        frames: list[pd.DataFrame] = []
        for year in years:
            frame = pd.read_parquet(years_dir / f"{year}.parquet")
            if frame.empty:
                continue
            if "_year" not in frame.columns:
                frame = frame.copy()
                frame["_year"] = year
            frames.append(frame)
        if not frames:
            raise ValueError("统一序时账没有可写入数据")
        output.parent.mkdir(parents=True, exist_ok=True)
        _parquet_safe(pd.concat(frames, ignore_index=True)).to_parquet(output, index=False)

    def _validate_parquet(self, path: Path, *, expected_rows: int) -> None:
        actual_rows = self._parquet_row_count(path)
        if actual_rows != expected_rows:
            raise ValueError(
                f"Parquet 行数校验失败：预期 {expected_rows}，实际 {actual_rows}"
            )
        conn = duckdb.connect()
        try:
            conn.execute(
                "SELECT * FROM read_parquet(?) LIMIT 1",
                [str(path)],
            ).fetchone()
        finally:
            conn.close()

    @staticmethod
    def _remove_path(path: Path) -> None:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)

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

    def _dataset_version(self, project_id: str, years: list[int]) -> str:
        years_dir = self.project_dir(project_id) / "raw" / "years"
        return self._dataset_version_for_years_dir(years_dir, years)

    @staticmethod
    def _dataset_version_for_years_dir(years_dir: Path, years: list[int]) -> str:
        digest = hashlib.sha256()
        for year in years:
            path = years_dir / f"{year}.parquet"
            digest.update(str(year).encode())
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        return digest.hexdigest()[:16]

    def _write_manifest(self, manifest: ProjectManifest) -> None:
        path = self.project_dir(manifest.project_id) / "manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            manifest.to_dict(),
            ensure_ascii=False,
            indent=2,
        ).encode()
        self._write_atomic_bytes(path, payload)

    @staticmethod
    def _write_atomic_bytes(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        try:
            os.replace(temp_path, path)
        finally:
            temp_path.unlink(missing_ok=True)

    def _write_state(self, pdir: Path, state: dict[str, Any]) -> None:
        path = pdir / "state.json"
        payload = json.dumps(
            self._jsonable(state),
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        self._write_atomic_bytes(path, payload)

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
