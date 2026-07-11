from __future__ import annotations

from typing import Any

import pandas as pd
from audit_engine.store import ProjectStore
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from audit_api.deps import get_store

router = APIRouter(prefix="/projects", tags=["projects"])


class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ProjectSummary(BaseModel):
    project_id: str
    project_name: str
    years: list[int]
    total_rows: int
    updated_at: str


@router.get("", response_model=list[ProjectSummary])
def list_projects(store: ProjectStore = Depends(get_store)) -> list[ProjectSummary]:
    return [
        ProjectSummary(
            project_id=m.project_id,
            project_name=m.project_name,
            years=m.years,
            total_rows=m.total_rows,
            updated_at=m.updated_at,
        )
        for m in store.list_projects()
    ]


@router.post("", response_model=ProjectSummary)
def create_project(
    body: CreateProjectRequest,
    store: ProjectStore = Depends(get_store),
) -> ProjectSummary:
    manifest = store.create_project(body.name)
    return ProjectSummary(
        project_id=manifest.project_id,
        project_name=manifest.project_name,
        years=manifest.years,
        total_rows=manifest.total_rows,
        updated_at=manifest.updated_at,
    )


@router.get("/{project_id}")
def get_project(project_id: str, store: ProjectStore = Depends(get_store)) -> dict[str, Any]:
    try:
        return store.load_manifest(project_id).to_dict()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/{project_id}", status_code=204)
def delete_project(project_id: str, store: ProjectStore = Depends(get_store)) -> None:
    try:
        store.load_manifest(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    store.delete_project(project_id)


class JournalPage(BaseModel):
    year: int
    rows: list[dict[str, Any]]
    total: int
    limit: int
    offset: int


@router.get("/{project_id}/journal/{year}", response_model=JournalPage)
def get_journal_page(
    project_id: str,
    year: int,
    limit: int = 100,
    offset: int = 0,
    store: ProjectStore = Depends(get_store),
) -> JournalPage:
    try:
        store.load_manifest(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    df, total = store.query_journal(project_id, year, limit=limit, offset=offset)
    rows = _dataframe_to_records(df)
    return JournalPage(year=year, rows=rows, total=total, limit=limit, offset=offset)


def _dataframe_to_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df.empty:
        return []
    out = df.copy()
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            out[col] = out[col].dt.strftime("%Y-%m-%d")
    return out.to_dict(orient="records")
