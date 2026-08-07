"""模块风险分析任务阶段 — 写入 project state 供前端轮询。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import uuid4

from audit_engine.store import ProjectStore

STAGES: list[dict[str, Any]] = [
    {"id": "queued", "label": "排队等待", "percent": 0},
    {"id": "load_data", "label": "加载数据", "percent": 8},
    {"id": "aggregate", "label": "聚合指标", "percent": 22},
    {"id": "prompt", "label": "组织提示", "percent": 32},
    {"id": "llm", "label": "大模型分析", "percent": 78},
    {"id": "parse", "label": "解析结果", "percent": 92},
    {"id": "save", "label": "写入缓存", "percent": 100},
]

STAGE_IDS = [s["id"] for s in STAGES]
STAGE_BY_ID = {s["id"]: s for s in STAGES}
ACTIVE_STATUSES = {"queued", "running"}
STALE_JOB_SECONDS = 300
FINISHED_RETENTION_SECONDS = 30


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _jobs(state: dict[str, Any]) -> dict[str, Any]:
    return dict(state.get("module_insight_jobs") or {})


def _age_seconds(timestamp: str | None) -> float | None:
    if not timestamp:
        return None
    try:
        return (datetime.now() - datetime.fromisoformat(timestamp)).total_seconds()
    except (TypeError, ValueError):
        return None


def _update_agent_job_result(
    state: dict[str, Any],
    *,
    job_id: str,
    status: str,
    error: str | None = None,
) -> None:
    from audit_engine.agent.sessions import ensure_agent_sessions, iter_session_messages, touch_session_updated

    ensure_agent_sessions(state)
    thread = dict(state.get("agent_thread") or {})
    changed = False
    touched: set[str] = set()
    for sess, message in iter_session_messages(state):
        for call in message.get("tool_calls") or []:
            result = call.get("result") or {}
            if str(result.get("job_id") or "") != job_id:
                continue
            result["status"] = status
            result["error"] = error
            changed = True
            touched.add(str(sess.get("id") or ""))
    if changed:
        for sid in touched:
            if sid:
                touch_session_updated(state, sid)
        thread["updated_at"] = _now()
        state["agent_thread"] = thread


def queue_job(
    store: ProjectStore,
    project_id: str,
    module_key: str,
) -> tuple[dict[str, Any], bool]:
    """创建模块后台任务；同一模块已有活跃任务时复用。"""
    result: dict[str, Any] = {}

    def update(state: dict[str, Any]) -> None:
        jobs = _jobs(state)
        existing = dict(jobs.get(module_key) or {})
        age = _age_seconds(existing.get("updated_at"))
        if existing.get("status") in ACTIVE_STATUSES and (age is None or age <= STALE_JOB_SECONDS):
            result["job"] = existing
            result["created"] = False
            return

        now = _now()
        job = {
            "job_id": "ins_" + uuid4().hex[:12],
            "module_key": module_key,
            "stage": "queued",
            "stage_label": STAGE_BY_ID["queued"]["label"],
            "percent": 0,
            "status": "queued",
            "created_at": now,
            "started_at": None,
            "updated_at": now,
            "completed_at": None,
            "error": None,
            "stages": STAGES,
        }
        jobs[module_key] = job
        state["module_insight_jobs"] = jobs
        result["job"] = job
        result["created"] = True

    store.update_state(project_id, update)
    return dict(result["job"]), bool(result["created"])


def set_job_stage(
    store: ProjectStore,
    project_id: str,
    module_key: str,
    stage_id: str,
    *,
    status: str = "running",
    error: str | None = None,
) -> dict[str, Any]:
    stage = STAGE_BY_ID.get(stage_id, STAGES[0])
    result: dict[str, Any] = {}

    def update(state: dict[str, Any]) -> None:
        jobs = _jobs(state)
        prev = dict(jobs.get(module_key) or {})
        now = _now()
        job = {
            "job_id": prev.get("job_id") or "ins_" + uuid4().hex[:12],
            "module_key": module_key,
            "stage": stage_id,
            "stage_label": stage["label"],
            "percent": int(stage["percent"]),
            "status": status,
            "created_at": prev.get("created_at") or now,
            "started_at": prev.get("started_at") or (now if status == "running" else None),
            "updated_at": now,
            "completed_at": now if status in {"done", "error"} else None,
            "error": error,
            "stages": STAGES,
        }
        jobs[module_key] = job
        state["module_insight_jobs"] = jobs
        result["job"] = job

    store.update_state(project_id, update)
    return dict(result["job"])


def finish_job(store: ProjectStore, project_id: str, module_key: str) -> None:
    set_job_stage(store, project_id, module_key, "save", status="done")


def fail_job(store: ProjectStore, project_id: str, module_key: str, error: str) -> None:
    state = store.load_state(project_id)
    job = dict(_jobs(state).get(module_key) or {})
    set_job_stage(
        store,
        project_id,
        module_key,
        str(job.get("stage") or "load_data"),
        status="error",
        error=error,
    )


def clear_job(store: ProjectStore, project_id: str, module_key: str, *, delay_done: bool = True) -> None:
    def update(state: dict[str, Any]) -> None:
        jobs = _jobs(state)
        job = jobs.get(module_key)
        if not job:
            return
        if delay_done and job.get("status") == "done":
            return
        jobs.pop(module_key, None)
        state["module_insight_jobs"] = jobs

    store.update_state(project_id, update)


def remove_finished_jobs(store: ProjectStore, project_id: str, older_than_sec: int = 30) -> None:
    def update(state: dict[str, Any]) -> None:
        jobs = _jobs(state)
        state["module_insight_jobs"] = {
            key: job
            for key, job in jobs.items()
            if job.get("status") not in {"done", "error"}
            or (_age_seconds(job.get("updated_at")) or 0) < older_than_sec
        }

    store.update_state(project_id, update)


def reconcile_jobs(
    store: ProjectStore,
    project_id: str,
    *,
    stale_after_sec: int = STALE_JOB_SECONDS,
    retain_finished_sec: int = FINISHED_RETENTION_SECONDS,
) -> None:
    """把中断的活跃任务标记为失败，并清理过期完成态。"""
    snapshot = _jobs(store.load_state(project_id))
    needs_update = any(
        (
            job.get("status") in ACTIVE_STATUSES
            and (_age_seconds(job.get("updated_at")) or 0) >= stale_after_sec
        )
        or (
            job.get("status") in {"done", "error"}
            and (_age_seconds(job.get("updated_at")) or 0) >= retain_finished_sec
        )
        for job in snapshot.values()
    )
    if not needs_update:
        return
    now = _now()

    def update(state: dict[str, Any]) -> None:
        jobs = _jobs(state)
        kept: dict[str, Any] = {}
        for key, raw_job in jobs.items():
            job = dict(raw_job)
            age = _age_seconds(job.get("updated_at"))
            if job.get("status") in ACTIVE_STATUSES and age is not None and age >= stale_after_sec:
                job.update(
                    {
                        "status": "error",
                        "error": "分析任务长时间无更新，可能因服务重启或进程中断，请重新运行",
                        "updated_at": now,
                        "completed_at": now,
                    }
                )
                job_id = str(job.get("job_id") or "")
                if job_id:
                    _update_agent_job_result(
                        state,
                        job_id=job_id,
                        status="error",
                        error=str(job["error"]),
                    )
                age = 0
            if job.get("status") in {"done", "error"} and age is not None and age >= retain_finished_sec:
                continue
            kept[key] = job
        state["module_insight_jobs"] = kept

    store.update_state(project_id, update)


def list_jobs(store: ProjectStore, project_id: str) -> dict[str, Any]:
    reconcile_jobs(store, project_id)
    state = store.load_state(project_id)
    jobs = _jobs(state)
    running = [j for j in jobs.values() if j.get("status") in ACTIVE_STATUSES]
    overall_percent = 0
    if running:
        overall_percent = round(sum(int(j.get("percent", 0)) for j in running) / len(running))
    return {
        "stages": STAGES,
        "jobs": list(jobs.values()),
        "running_count": len(running),
        "overall_percent": overall_percent,
    }


def make_progress_reporter(store: ProjectStore, project_id: str, module_key: str) -> Callable[[str], None]:
    def report(stage_id: str) -> None:
        set_job_stage(store, project_id, module_key, stage_id, status="running")

    return report
