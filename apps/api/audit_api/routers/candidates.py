"""疑点库 API — 图表钻取结果入库与列表。"""

from __future__ import annotations

from typing import Any

import pandas as pd
from audit_engine.analysis.drilldown import resolve_drilldown
from audit_engine.candidate_pool import (
    add_candidate_group,
    build_candidate_group,
    pool_stats,
    remove_candidate_group,
    update_candidate_fields,
)
from audit_engine.store import ProjectStore
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from audit_api.deps import get_store
from audit_api.routers.analysis import _df_records, _manifest_or_404

router = APIRouter(prefix="/projects", tags=["candidates"])


class AddCandidateRequest(BaseModel):
    title: str
    source_module: str
    source_view: str
    selector: dict[str, Any]
    reason: str = ""
    tags: list[str] = Field(default_factory=list)
    voucher_ids: list[str] | None = None


class UpdateCandidateRequest(BaseModel):
    status: str | None = Field(None, pattern="^(候选|人工直入最终样本|排除)$")
    reason: str | None = None
    tags: list[str] | None = None
    title: str | None = None


def _find_group(pool: list[dict[str, Any]], group_id: str) -> dict[str, Any]:
    group = next((g for g in pool if g.get("group_id") == group_id), None)
    if group is None:
        raise HTTPException(status_code=404, detail=f"疑点组不存在: {group_id}")
    return group


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
    from audit_engine.routine_filter import prepare_candidate_detail
    from audit_engine.rules_config import default_rules_config, merge_rules_config

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

    rules_cfg = default_rules_config()
    state = store.load_state(project_id)
    if isinstance(state.get("rules_config"), dict):
        rules_cfg = merge_rules_config(rules_cfg, state.get("rules_config"))
    detail = prepare_candidate_detail(detail, rules_cfg, selector=selector)
    if detail.empty:
        raise HTTPException(status_code=400, detail="过滤常规机械分录后无剩余样本可入库")

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


@router.get("/{project_id}/candidates/{group_id}/entries")
def get_candidate_entries(
    project_id: str,
    group_id: str,
    limit: int = Query(200, ge=1, le=2000),
    store: ProjectStore = Depends(get_store),
) -> dict:
    """按疑点组已锁定的 voucher_ids 回看分录明细。"""
    manifest = _manifest_or_404(store, project_id)
    pool = store.load_candidate_pool(project_id)
    group = _find_group(pool, group_id)
    vids = {str(v) for v in group.get("voucher_ids") or [] if str(v)}
    if not vids:
        return {"group_id": group_id, "row_count": 0, "rows": [], "voucher_count": 0}

    selector = group.get("selector") or {}
    year = selector.get("year")
    years = [int(year)] if year is not None else list(manifest.years)
    frames: list[pd.DataFrame] = []
    for y in years:
        if y not in manifest.years:
            continue
        work = store.get_work_df(project_id, y)
        if work.empty or "凭证编号" not in work.columns:
            continue
        subset = work[work["凭证编号"].astype(str).isin(vids)].copy()
        if not subset.empty:
            frames.append(subset)

    if not frames:
        return {"group_id": group_id, "row_count": 0, "rows": [], "voucher_count": len(vids)}

    detail = pd.concat(frames, ignore_index=True)
    if len(detail) > limit:
        detail = detail.head(limit)
    return {
        "group_id": group_id,
        "row_count": len(detail),
        "voucher_count": len(vids),
        "rows": _df_records(detail),
    }


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
def patch_candidate(
    project_id: str,
    group_id: str,
    body: UpdateCandidateRequest,
    store: ProjectStore = Depends(get_store),
) -> dict:
    _manifest_or_404(store, project_id)
    pool = store.load_candidate_pool(project_id)
    _find_group(pool, group_id)
    if body.status is None and body.reason is None and body.tags is None and body.title is None:
        raise HTTPException(status_code=400, detail="至少提供 status / reason / tags / title 之一")
    pool = update_candidate_fields(
        pool,
        group_id,
        status=body.status,
        reason=body.reason,
        tags=body.tags,
        title=body.title,
    )
    store.save_candidate_pool(project_id, pool)
    group = next((g for g in pool if g.get("group_id") == group_id), None)
    return {"group": group, "stats": pool_stats(pool)}
