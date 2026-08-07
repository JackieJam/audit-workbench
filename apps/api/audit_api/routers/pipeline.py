"""Pipeline API — 画像 / 跨年 / 规则 / 抽样 / Excel 导出。"""

from __future__ import annotations

import json
from typing import Any, Literal
from urllib.parse import quote

from audit_engine.pipeline import AnalysisPipeline
from audit_engine.rules_config import default_rules_config
from audit_engine.store import ProjectStore
from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from audit_api.deps import get_pipeline, get_store
from audit_api.routers.analysis import _manifest_or_404

router = APIRouter(prefix="/projects", tags=["pipeline"])


class CoverageConstraintsRequest(BaseModel):
    min_per_month: int | None = Field(None, ge=1, le=500)
    min_per_account_category: int | None = Field(None, ge=1, le=500)
    max_same_risk_signal_ratio: float | None = Field(None, gt=0, le=1)


class SampleRequest(BaseModel):
    plan_name: str = Field("", max_length=200)
    population_scope: Literal["full_population", "risk_signals"] = "risk_signals"
    strategy: Literal[
        "risk_directed",
        "random",
        "monetary_unit",
        "stratified",
        "unpredictable",
    ] = "risk_directed"
    method: str = Field("by_rule", pattern="^(by_rule|random|all|by_account_weight|monetary_unit|stratified)$")
    size: int | None = Field(None, ge=1, le=500)
    seed: int = 42
    years: list[int] = Field(default_factory=list, max_length=100)
    coverage_constraints: CoverageConstraintsRequest = Field(default_factory=CoverageConstraintsRequest)
    stratify_by: Literal["account_category", "month", "voucher_type"] | None = None
    stratify_mode: Literal["proportional", "equal"] | None = None
    unpredictable: bool = False


class VerifyRequest(BaseModel):
    profile_id: str = ""
    api_key: str = ""
    max_verify: int = Field(50, ge=1, le=200)
    # none=原文外发；pseudonym=供应商/客户/用户名/凭证号伪名化（默认）
    redaction: str = Field("pseudonym", pattern="^(none|pseudonym)$")
    # 发送前数据边界知情确认；未确认时接口拒绝外发
    confirm_data_boundary: bool = False
    # current_sample=核验当前抽样；risk_signals=按规则高风险信号取 top N
    verification_scope: Literal["current_sample", "risk_signals"] = "current_sample"


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
    try:
        results = pipeline.run_rules(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    context = store.load_state(project_id).get("rule_run_context") or {}
    return _summarize_rule_results(results, context=context)


@router.get("/{project_id}/pipeline/rules/results")
def get_rule_results(project_id: str, store: ProjectStore = Depends(get_store)) -> dict:
    _manifest_or_404(store, project_id)
    state = store.load_state(project_id)
    results = state.get("rule_results", [])
    return _summarize_rule_results(
        results,
        context=state.get("rule_run_context") or {},
    )


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
    try:
        return pipeline.extract_samples(
            project_id,
            method=body.method,
            size=body.size,
            seed=body.seed,
            plan=body.model_dump(exclude_unset=True),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{project_id}/pipeline/samples")
def get_samples(project_id: str, store: ProjectStore = Depends(get_store)) -> dict:
    _manifest_or_404(store, project_id)
    state = store.load_state(project_id)
    samples = state.get("samples", [])
    voucher_count = len({
        str(s.get("_voucher_key") or s.get("凭证唯一键") or s.get("凭证编号"))
        for s in samples
        if s.get("_voucher_key") or s.get("凭证唯一键") or s.get("凭证编号")
    })
    plan_record = state.get("sampling_plan") or {}
    return {
        "sample_rows": len(samples),
        "voucher_count": voucher_count,
        "samples": samples,
        "requested_plan": plan_record.get("requested_plan"),
        "population_snapshot": plan_record.get("population_snapshot"),
        "selection_trace": plan_record.get("selection_trace"),
    }


@router.get("/{project_id}/pipeline/verify")
def get_verify_status(project_id: str, store: ProjectStore = Depends(get_store)) -> dict:
    from audit_engine.llm_verifier import judgments_from_state, summarize_judgments

    _manifest_or_404(store, project_id)
    judgments = judgments_from_state(store.load_state(project_id).get("llm_judgments"))
    return {"summary": summarize_judgments(judgments), "has_judgments": bool(judgments)}


@router.get("/{project_id}/pipeline/verify/boundary")
def get_verify_boundary(
    project_id: str,
    profile_id: str = "",
    redaction: str = "pseudonym",
    store: ProjectStore = Depends(get_store),
    x_llm_profile_id: str | None = Header(default=None, alias="X-LLM-Profile-Id"),
) -> dict:
    """发送前预览数据边界（不调用模型、不外发明细）。"""
    from audit_engine.llm_runtime import resolve_llm_runtime
    from audit_engine.llm_verifier import describe_llm_verify_boundary

    _manifest_or_404(store, project_id)
    resolved_profile = (profile_id or x_llm_profile_id or "").strip() or None
    runtime = resolve_llm_runtime(profile_id=resolved_profile, manual_key=None)
    mode = redaction if redaction in {"none", "pseudonym"} else "pseudonym"
    boundary = describe_llm_verify_boundary(
        runtime.base_url, runtime.model, redaction=mode,  # type: ignore[arg-type]
    )
    return {"data_boundary": boundary, "has_api_key": bool(runtime.api_key)}


@router.post("/{project_id}/pipeline/verify")
def run_verify(
    project_id: str,
    body: VerifyRequest | None = None,
    store: ProjectStore = Depends(get_store),
    pipeline: AnalysisPipeline = Depends(get_pipeline),
    x_llm_api_key: str | None = Header(default=None, alias="X-LLM-Api-Key"),
    x_llm_profile_id: str | None = Header(default=None, alias="X-LLM-Profile-Id"),
) -> dict:
    from audit_engine.llm_runtime import resolve_llm_runtime

    manifest = _manifest_or_404(store, project_id)
    if not manifest.years:
        raise HTTPException(status_code=400, detail="项目无序时账数据")

    req = body or VerifyRequest()
    if not req.confirm_data_boundary:
        raise HTTPException(
            status_code=400,
            detail="请先确认数据外发边界（confirm_data_boundary）。可先 GET /pipeline/verify/boundary 预览。",
        )
    profile_id = (req.profile_id or x_llm_profile_id or "").strip() or None
    api_key = (req.api_key or x_llm_api_key or "").strip() or None
    runtime = resolve_llm_runtime(profile_id=profile_id, manual_key=api_key)
    if not runtime.api_key:
        raise HTTPException(
            status_code=400,
            detail="未配置 LLM API Key。请在「大模型」页签保存方案并填入 Key，或设置 DEEPSEEK_API_KEY",
        )

    try:
        result = pipeline.verify_with_llm(
            project_id,
            api_key=runtime.api_key,
            model=runtime.model,
            base_url=runtime.base_url,
            max_verify=req.max_verify,
            redaction=req.redaction,
            verification_scope=req.verification_scope,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LLM 核验失败：{exc}") from exc
    return result


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
    encoded_filename = quote(filename, safe="")
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": (
                f'attachment; filename="audit_sample.xlsx"; filename*=UTF-8\'\'{encoded_filename}'
            ),
            "X-Report-Stats": json.dumps(stats, ensure_ascii=True),
        },
    )


def _summarize_rule_results(
    results: list[dict],
    *,
    context: dict[str, Any] | None = None,
) -> dict:
    summary = []
    previews = []
    total_hits = 0
    for block in results or []:
        hits = list(block.get("hits") or [])
        count = int(block.get("count", len(hits)))
        total_hits += count
        summary.append({
            "rule_name": block.get("rule_name", ""),
            "count": count,
        })
        previews.append({
            "rule_name": block.get("rule_name", ""),
            "count": count,
            "hits": hits[:20],
            "hits_truncated": len(hits) > 20 or count > len(hits[:20]),
        })
    return {
        "rules": summary,
        "total_hits": total_hits,
        "results": previews,
        "rule_run_context": context or {},
    }
