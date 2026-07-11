"""Pipeline API — 画像 / 跨年 / 规则 / 抽样 / Excel 导出。"""

from __future__ import annotations

import json
from typing import Any

from audit_engine.pipeline import AnalysisPipeline
from audit_engine.rules_config import default_rules_config
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from audit_api.deps import get_pipeline, get_store
from audit_api.routers.analysis import _manifest_or_404
from audit_engine.store import ProjectStore

router = APIRouter(prefix="/projects", tags=["pipeline"])


class SampleRequest(BaseModel):
    method: str = Field("by_rule", pattern="^(by_rule|random|all|by_account_weight|monetary_unit|stratified)$")
    size: int | None = Field(None, ge=1, le=500)
    seed: int = 42


@router.get("/{project_id}/rules")
def get_rules(
    project_id: str,
    store: ProjectStore = Depends(get_store),
    pipeline: AnalysisPipeline = Depends(get_pipeline),
) -> dict:
    _manifest_or_404(store, project_id)
    return pipeline.load_rules(project_id)


@router.put("/{project_id}/rules")
def put_rules(
    project_id: str,
    body: dict[str, Any],
    store: ProjectStore = Depends(get_store),
    pipeline: AnalysisPipeline = Depends(get_pipeline),
) -> dict:
    _manifest_or_404(store, project_id)
    return pipeline.save_rules(project_id, body)


@router.get("/{project_id}/rules/defaults")
def rules_defaults() -> dict:
    return default_rules_config()


@router.post("/{project_id}/pipeline/profiles")
def build_profiles(
    project_id: str,
    store: ProjectStore = Depends(get_store),
    pipeline: AnalysisPipeline = Depends(get_pipeline),
) -> dict:
    manifest = _manifest_or_404(store, project_id)
    if not manifest.years:
        raise HTTPException(status_code=400, detail="项目无序时账数据")
    profiles = pipeline.build_profiles(project_id)
    state = store.load_state(project_id)
    return {
        "years": sorted(profiles.keys()),
        "profiles": profiles,
        "financials": state.get("financials", {}),
    }


@router.get("/{project_id}/pipeline/profiles")
def get_profiles(project_id: str, store: ProjectStore = Depends(get_store)) -> dict:
    _manifest_or_404(store, project_id)
    state = store.load_state(project_id)
    return {
        "profiles": state.get("profiles", {}),
        "financials": state.get("financials", {}),
    }


@router.post("/{project_id}/pipeline/cross-year")
def run_cross_year(
    project_id: str,
    store: ProjectStore = Depends(get_store),
    pipeline: AnalysisPipeline = Depends(get_pipeline),
) -> dict:
    manifest = _manifest_or_404(store, project_id)
    if len(manifest.years) < 2:
        raise HTTPException(status_code=400, detail="跨年稽核需要至少两个年度数据")
    findings = pipeline.run_cross_year(project_id)
    return {"count": len(findings), "findings": findings}


@router.get("/{project_id}/pipeline/cross-year")
def get_cross_year(project_id: str, store: ProjectStore = Depends(get_store)) -> dict:
    _manifest_or_404(store, project_id)
    findings = store.load_state(project_id).get("cross_year_findings", [])
    return {"count": len(findings), "findings": findings}


@router.post("/{project_id}/pipeline/rules/run")
def run_rules(
    project_id: str,
    store: ProjectStore = Depends(get_store),
    pipeline: AnalysisPipeline = Depends(get_pipeline),
) -> dict:
    manifest = _manifest_or_404(store, project_id)
    if not manifest.years:
        raise HTTPException(status_code=400, detail="项目无序时账数据")
    results = pipeline.run_rules(project_id)
    return _summarize_rule_results(results)


@router.get("/{project_id}/pipeline/rules/results")
def get_rule_results(project_id: str, store: ProjectStore = Depends(get_store)) -> dict:
    _manifest_or_404(store, project_id)
    results = store.load_state(project_id).get("rule_results", [])
    return _summarize_rule_results(results)


@router.post("/{project_id}/pipeline/samples")
def extract_samples(
    project_id: str,
    body: SampleRequest,
    store: ProjectStore = Depends(get_store),
    pipeline: AnalysisPipeline = Depends(get_pipeline),
) -> dict:
    manifest = _manifest_or_404(store, project_id)
    if not manifest.years:
        raise HTTPException(status_code=400, detail="项目无序时账数据")
    return pipeline.extract_samples(
        project_id,
        method=body.method,
        size=body.size,
        seed=body.seed,
    )


@router.get("/{project_id}/pipeline/samples")
def get_samples(project_id: str, store: ProjectStore = Depends(get_store)) -> dict:
    _manifest_or_404(store, project_id)
    state = store.load_state(project_id)
    samples = state.get("samples", [])
    voucher_count = len({s.get("凭证编号") for s in samples if s.get("凭证编号")})
    return {
        "sample_rows": len(samples),
        "voucher_count": voucher_count,
        "samples": samples,
    }


@router.get("/{project_id}/pipeline/export")
def export_excel(
    project_id: str,
    store: ProjectStore = Depends(get_store),
    pipeline: AnalysisPipeline = Depends(get_pipeline),
) -> Response:
    manifest = _manifest_or_404(store, project_id)
    if not manifest.years:
        raise HTTPException(status_code=400, detail="项目无序时账数据")
    data, stats = pipeline.export_excel(project_id)
    filename = f"audit_sample_{project_id[:8]}.xlsx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Report-Stats": json.dumps(stats, ensure_ascii=True),
        },
    )


def _summarize_rule_results(results: list[dict]) -> dict:
    summary = []
    total_hits = 0
    for block in results or []:
        count = int(block.get("count", len(block.get("hits", []))))
        total_hits += count
        summary.append({
            "rule_name": block.get("rule_name", ""),
            "count": count,
        })
    return {
        "rules": summary,
        "total_hits": total_hits,
        "results": results,
    }
