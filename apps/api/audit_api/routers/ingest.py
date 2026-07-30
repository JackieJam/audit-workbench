"""Ingest API — Excel upload, column detect, parquet persist."""

from __future__ import annotations

import io
import json
from typing import Any

import pandas as pd
from audit_engine.experience import learned_column_aliases, record_column_mappings
from audit_engine.ingestion import (
    NO_COLUMN_SENTINEL,
    STANDARD_COLUMNS,
    DetectionResult,
    detect_columns,
    load_files,
    summarize_years,
)
from audit_engine.source_assets import SourceAsset, build_source_asset, persist_source_asset
from audit_engine.store import ProjectStore
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from audit_api.deps import get_store

router = APIRouter(prefix="/projects", tags=["ingest"])


class ColumnMatchOut(BaseModel):
    source: str
    score: float
    method: str


class DetectResponse(BaseModel):
    file_label: str
    source_columns: list[str]
    suggested_mapping: dict[str, str]
    mapping_matches: dict[str, ColumnMatchOut]
    standard_columns: list[dict[str, str]]
    sample_rows: list[dict[str, Any]] = Field(default_factory=list)


class YearSummaryOut(BaseModel):
    年份: int
    行数: int
    凭证数: int
    Period13行数: int = 0
    金额合计: float = 0
    日期范围: str = ""


class IngestResponse(BaseModel):
    project_id: str
    total_rows: int
    years: list[int]
    missing_columns: list[str]
    year_summary: list[YearSummaryOut]
    aliases_recorded: int


def _detection_to_response(det: DetectionResult) -> DetectResponse:
    sample = det.sample.head(5)
    for col in sample.columns:
        if pd.api.types.is_datetime64_any_dtype(sample[col]):
            sample = sample.copy()
            sample[col] = sample[col].dt.strftime("%Y-%m-%d")
    return DetectResponse(
        file_label=det.file_label,
        source_columns=det.source_columns,
        suggested_mapping=det.suggested_mapping,
        mapping_matches={
            std: ColumnMatchOut(source=m.source, score=m.score, method=m.method)
            for std, m in det.mapping_matches.items()
        },
        standard_columns=[
            {"name": c.name, "tier": c.tier, "description": c.description}
            for c in STANDARD_COLUMNS
        ],
        sample_rows=sample.to_dict(orient="records") if not sample.empty else [],
    )


async def _read_uploads(files: list[UploadFile]) -> list[io.BytesIO]:
    if not files:
        raise HTTPException(status_code=400, detail="请至少上传一个 Excel 文件")
    buffers: list[io.BytesIO] = []
    for f in files:
        name = (f.filename or "").lower()
        if not name.endswith(".xlsx"):
            raise HTTPException(status_code=400, detail=f"仅支持 .xlsx；请先将旧版 .xls 另存为 .xlsx：{f.filename}")
        data = await f.read()
        if not data:
            raise HTTPException(status_code=400, detail=f"文件为空：{f.filename}")
        buf = io.BytesIO(data)
        buf.name = f.filename or "upload.xlsx"  # type: ignore[attr-defined]
        buf._audit_source_asset = build_source_asset(buf.name, data).to_dict()  # type: ignore[attr-defined]
        buffers.append(buf)
    return buffers


def _persist_upload_assets(
    store: ProjectStore,
    project_id: str,
    buffers: list[io.BytesIO],
) -> list[dict[str, Any]]:
    assets: list[dict[str, Any]] = []
    project_dir = store.project_dir(project_id)
    for buffer in buffers:
        raw = getattr(buffer, "_audit_source_asset", None)
        if not isinstance(raw, dict):
            continue
        asset = SourceAsset.from_dict(raw)
        persist_source_asset(project_dir, asset, buffer.getvalue())
        assets.append(asset.to_dict())
    return assets


@router.post("/{project_id}/ingest/detect", response_model=DetectResponse)
async def detect_ingest_columns(
    project_id: str,
    files: list[UploadFile] = File(...),
    store: ProjectStore = Depends(get_store),
) -> DetectResponse:
    try:
        store.load_manifest(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    buffers = await _read_uploads(files)
    learned = learned_column_aliases()
    det = detect_columns(buffers, learned_aliases=learned)
    return _detection_to_response(det)


@router.post("/{project_id}/ingest/commit", response_model=IngestResponse)
async def commit_ingest(
    project_id: str,
    files: list[UploadFile] = File(...),
    column_mapping: str = Form("{}"),
    store: ProjectStore = Depends(get_store),
) -> IngestResponse:
    try:
        store.load_manifest(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    try:
        mapping: dict[str, str] = json.loads(column_mapping)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="column_mapping 不是合法 JSON") from exc

    buffers = await _read_uploads(files)
    if not mapping:
        det = detect_columns(buffers, learned_aliases=learned_column_aliases())
        mapping = det.suggested_mapping

    # 过滤无此列
    mapping = {
        k: v for k, v in mapping.items()
        if v and v != NO_COLUMN_SENTINEL
    }

    _df, year_map, missing = load_files(buffers, column_mapping=mapping)
    if not year_map:
        raise HTTPException(status_code=400, detail="未能从文件中识别出任何年度数据")

    year_summary_raw = summarize_years(_df)
    source_assets = _persist_upload_assets(store, project_id, buffers)

    manifest = store.ingest_journal(
        project_id,
        year_map,
        column_mapping=mapping,
        missing_columns=missing,
        year_summary=year_summary_raw,
        source_assets=source_assets,
    )
    # 只有项目数据提交成功后才学习映射，失败导入不得污染全局别名。
    try:
        aliases_recorded = record_column_mappings(mapping)
    except (OSError, json.JSONDecodeError):
        # 别名学习是辅助能力，不能让已成功提交的数据被误报为导入失败。
        aliases_recorded = 0

    return IngestResponse(
        project_id=manifest.project_id,
        total_rows=manifest.total_rows,
        years=manifest.years,
        missing_columns=missing,
        year_summary=[YearSummaryOut(**row) for row in year_summary_raw],
        aliases_recorded=aliases_recorded,
    )
