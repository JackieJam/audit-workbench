"""模块 LLM 风险分析 API。"""

from __future__ import annotations

import json
from pathlib import Path

from audit_engine.llm_runtime import resolve_llm_runtime
from audit_engine.module_insight import (
    apply_recommendations,
    run_module_insight_pipeline,
)
from audit_engine.module_insight_jobs import list_jobs
from audit_engine.store import ProjectStore
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from audit_api.deps import get_store
from audit_api.module_insight_tasks import enqueue_module_insight
from audit_api.routers.analysis import _manifest_or_404

router = APIRouter(prefix="/projects", tags=["llm-insight"])

_REPO_ROOT = Path(__file__).resolve().parents[4]
_AUDIT_QUESTIONS_PATH = _REPO_ROOT / "config" / "audit_questions.json"


def _load_questions(module: str) -> list[dict[str, str]]:
    if not _AUDIT_QUESTIONS_PATH.exists():
        return []
    data = json.loads(_AUDIT_QUESTIONS_PATH.read_text(encoding="utf-8"))
    return list(data.get(module, {}).get("questions", []))


class InsightCreateRequest(BaseModel):
    profile_id: str = ""
    api_key: str = ""


class ApplyInsightRequest(BaseModel):
    indices: list[int] | None = Field(default=None, description="要应用的建议下标；空则全部")


@router.get("/{project_id}/analysis/modules/insight/jobs")
def list_module_insight_jobs(project_id: str, store: ProjectStore = Depends(get_store)) -> dict:
    _manifest_or_404(store, project_id)
    return list_jobs(store, project_id)


@router.get("/{project_id}/analysis/modules/{module_key}/insight")
def get_module_insight(project_id: str, module_key: str, store: ProjectStore = Depends(get_store)) -> dict:
    _manifest_or_404(store, project_id)
    cached = (store.load_state(project_id).get("module_insights") or {}).get(module_key)
    return {"module": module_key, "cached": cached is not None, "insight": cached}


@router.post("/{project_id}/analysis/modules/{module_key}/insight/jobs")
def create_module_insight_job(
    project_id: str,
    module_key: str,
    body: InsightCreateRequest,
    background_tasks: BackgroundTasks,
    store: ProjectStore = Depends(get_store),
    x_llm_api_key: str | None = Header(default=None, alias="X-LLM-Api-Key"),
    x_llm_profile_id: str | None = Header(default=None, alias="X-LLM-Profile-Id"),
) -> dict:
    _manifest_or_404(store, project_id)
    questions = _load_questions(module_key)
    if not questions:
        raise HTTPException(status_code=404, detail=f"未知模块：{module_key}")
    profile_id = (body.profile_id or x_llm_profile_id or "").strip() or None
    api_key = (body.api_key or x_llm_api_key or "").strip() or None
    runtime = resolve_llm_runtime(profile_id=profile_id, manual_key=api_key)
    if not runtime.api_key:
        raise HTTPException(
            status_code=400,
            detail="未配置 LLM API Key。请在「大模型」页签保存方案并填入 Key，或设置 DEEPSEEK_API_KEY",
        )
    try:
        job = enqueue_module_insight(
            background_tasks,
            store,
            project_id,
            module_key,
            api_key=runtime.api_key,
            model=runtime.model,
            base_url=runtime.base_url,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"module": module_key, "job": job}


@router.post("/{project_id}/analysis/modules/{module_key}/insight")
def create_module_insight(
    project_id: str,
    module_key: str,
    body: InsightCreateRequest | None = None,
    store: ProjectStore = Depends(get_store),
    x_llm_api_key: str | None = Header(default=None, alias="X-LLM-Api-Key"),
    x_llm_profile_id: str | None = Header(default=None, alias="X-LLM-Profile-Id"),
) -> dict:
    _manifest_or_404(store, project_id)
    questions = _load_questions(module_key)
    if not questions:
        raise HTTPException(status_code=404, detail=f"未知模块：{module_key}")

    req = body or InsightCreateRequest()
    profile_id = (req.profile_id or x_llm_profile_id or "").strip() or None
    api_key = (req.api_key or x_llm_api_key or "").strip() or None
    runtime = resolve_llm_runtime(profile_id=profile_id, manual_key=api_key)
    if not runtime.api_key:
        raise HTTPException(
            status_code=400,
            detail="未配置 LLM API Key。请在「大模型」页签保存方案并填入 Key，或设置 DEEPSEEK_API_KEY",
        )

    try:
        insight = run_module_insight_pipeline(
            store,
            project_id,
            module_key,
            risk_questions=questions,
            api_key=runtime.api_key,
            model=runtime.model,
            base_url=runtime.base_url,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LLM 调用失败：{exc}") from exc

    return {"module": module_key, "insight": insight}


@router.post("/{project_id}/analysis/modules/{module_key}/insight/apply")
def apply_module_insight(
    project_id: str,
    module_key: str,
    body: ApplyInsightRequest,
    store: ProjectStore = Depends(get_store),
) -> dict:
    _manifest_or_404(store, project_id)
    insight = (store.load_state(project_id).get("module_insights") or {}).get(module_key)
    if not insight:
        raise HTTPException(status_code=400, detail="请先生成模块风险分析")

    recs = insight.get("recommendations") or []
    if not recs:
        return {"added": 0, "skipped": 0, "errors": ["无抽样建议"]}

    result = apply_recommendations(
        store,
        project_id,
        recs,
        module_key=module_key,
        indices=body.indices,
    )
    return result
