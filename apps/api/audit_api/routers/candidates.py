"""疑点库 API — 图表钻取结果入库与列表。"""

from __future__ import annotations

from typing import Any

from audit_engine.analysis.drilldown import resolve_drilldown
from audit_engine.candidate_pool import (
    add_candidate_group,
    build_candidate_group,
    pool_stats,
    remove_candidate_group,
    update_candidate_status,
)
from audit_engine.store import ProjectStore
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from audit_api.deps import get_store
from audit_api.routers.analysis import _manifest_or_404

router = APIRouter(prefix="/projects", tags=["candidates"])


class AddCandidateRequest(BaseModel):
    title: str
    source_module: str
    source_view: str
    selector: dict[str, Any]
    reason: str = ""
    tags: list[str] = Field(default_factory=list)
    voucher_ids: list[str] | None = None


class UpdateCandidateStatusRequest(BaseModel):
    status: str = Field(..., pattern="^(候选|人工直入最终样本|排除)$")


@router.get("/{project_id}/candidates")
def list_candidates(project_id: str, store: ProjectStore = Depends(get_store)) -> dict:
    _manifest_or_404(store, project_id)
    pool = store.load_candidate_pool(project_id)
    return {"groups": pool, "stats": pool_stats(pool)}


@router.post("/{project_id}/candidates")
def add_candidate(
    project_id: str,
    body: AddCandidateRequest,
    store: ProjectStore = Depends(get_store),
) -> dict:
    manifest = _manifest_or_404(store, project_id)
    selector = body.selector
    year = selector.get("year")
    if year is None:
        raise HTTPException(status_code=400, detail="selector 缺少 year")
    if int(year) not in manifest.years:
        raise HTTPException(status_code=404, detail=f"项目中无 {year} 年序时账数据")

    work = store.get_work_df(project_id, int(year))
    detail = resolve_drilldown(work, selector)
    if detail.empty:
        raise HTTPException(status_code=400, detail="当前选择条件下无匹配分录")

    if body.voucher_ids:
        vids = {str(v).strip() for v in body.voucher_ids if str(v).strip()}
        if not vids:
            raise HTTPException(status_code=400, detail="voucher_ids 为空")
        detail = detail[detail["凭证编号"].astype(str).isin(vids)].copy()
        if detail.empty:
            raise HTTPException(status_code=400, detail="所选凭证在当前条件下无匹配分录")

    group = build_candidate_group(
        title=body.title,
        source_module=body.source_module,
        source_view=body.source_view,
        detail=detail,
        tags=body.tags,
        reason=body.reason,
        selector=selector,
    )
    pool = add_candidate_group(store.load_candidate_pool(project_id), group)
    store.save_candidate_pool(project_id, pool)
    return {"group": group, "stats": pool_stats(pool)}


@router.delete("/{project_id}/candidates/{group_id}")
def delete_candidate(
    project_id: str,
    group_id: str,
    store: ProjectStore = Depends(get_store),
) -> dict:
    _manifest_or_404(store, project_id)
    pool = remove_candidate_group(store.load_candidate_pool(project_id), group_id)
    store.save_candidate_pool(project_id, pool)
    return {"stats": pool_stats(pool)}


@router.patch("/{project_id}/candidates/{group_id}")
def patch_candidate_status(
    project_id: str,
    group_id: str,
    body: UpdateCandidateStatusRequest,
    store: ProjectStore = Depends(get_store),
) -> dict:
    _manifest_or_404(store, project_id)
    pool = update_candidate_status(store.load_candidate_pool(project_id), group_id, body.status)
    store.save_candidate_pool(project_id, pool)
    group = next((g for g in pool if g.get("group_id") == group_id), None)
    return {"group": group, "stats": pool_stats(pool)}
