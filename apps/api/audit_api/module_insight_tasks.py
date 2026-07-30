"""模块 AI 风险分析后台任务编排。"""

from __future__ import annotations

from typing import Any

from audit_engine.agent.audit_questions import load_module_questions
from audit_engine.agent.orchestrator import update_agent_module_insight_job
from audit_engine.module_insight import run_module_insight_pipeline
from audit_engine.module_insight_jobs import queue_job
from audit_engine.store import ProjectStore
from fastapi import BackgroundTasks


def _run_module_insight_job(
    store: ProjectStore,
    project_id: str,
    module_key: str,
    *,
    job_id: str,
    api_key: str,
    model: str,
    base_url: str,
) -> None:
    try:
        insight = run_module_insight_pipeline(
            store,
            project_id,
            module_key,
            risk_questions=load_module_questions(module_key),
            api_key=api_key,
            model=model,
            base_url=base_url,
        )
    except Exception as exc:
        update_agent_module_insight_job(
            store,
            project_id,
            job_id=job_id,
            status="error",
            error=str(exc),
        )
        return
    update_agent_module_insight_job(
        store,
        project_id,
        job_id=job_id,
        status="done",
        insight=insight,
    )


def enqueue_module_insight(
    background_tasks: BackgroundTasks,
    store: ProjectStore,
    project_id: str,
    module_key: str,
    *,
    api_key: str,
    model: str,
    base_url: str,
) -> dict[str, Any]:
    questions = load_module_questions(module_key)
    if not questions:
        raise ValueError(f"模块 {module_key} 无风险问题配置")
    job, created = queue_job(store, project_id, module_key)
    public_job = {**job, "reused": not created}
    if created:
        background_tasks.add_task(
            _run_module_insight_job,
            store,
            project_id,
            module_key,
            job_id=str(job["job_id"]),
            api_key=api_key,
            model=model,
            base_url=base_url,
        )
    return public_job
