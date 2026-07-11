"""模块风险分析任务阶段 — 写入 project state 供前端轮询。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from audit_engine.store import ProjectStore

STAGES: list[dict[str, Any]] = [
    {"id": "load_data", "label": "加载数据", "percent": 8},
    {"id": "aggregate", "label": "聚合指标", "percent": 22},
    {"id": "prompt", "label": "组织提示", "percent": 32},
    {"id": "llm", "label": "大模型分析", "percent": 78},
    {"id": "parse", "label": "解析结果", "percent": 92},
    {"id": "save", "label": "写入缓存", "percent": 100},
]

STAGE_IDS = [s["id"] for s in STAGES]
STAGE_BY_ID = {s["id"]: s for s in STAGES}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _jobs(state: dict[str, Any]) -> dict[str, Any]:
    return dict(state.get("module_insight_jobs") or {})


def _save_jobs(store: ProjectStore, project_id: str, jobs: dict[str, Any]) -> None:
    state = store.load_state(project_id)
    state["module_insight_jobs"] = jobs
    store.save_state(project_id, state)


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
    state = store.load_state(project_id)
    jobs = _jobs(state)
    prev = dict(jobs.get(module_key) or {})
    jobs[module_key] = {
        "module_key": module_key,
        "stage": stage_id,
        "stage_label": stage["label"],
        "percent": int(stage["percent"]),
        "status": status,
        "started_at": prev.get("started_at") or _now(),
        "updated_at": _now(),
        "error": error,
        "stages": STAGES,
    }
    state["module_insight_jobs"] = jobs
    store.save_state(project_id, state)
    return jobs[module_key]


def finish_job(store: ProjectStore, project_id: str, module_key: str) -> None:
    set_job_stage(store, project_id, module_key, "save", status="done")


def fail_job(store: ProjectStore, project_id: str, module_key: str, error: str) -> None:
    state = store.load_state(project_id)
    jobs = _jobs(state)
    job = dict(jobs.get(module_key) or {})
    job.update({
        "module_key": module_key,
        "status": "error",
        "error": error,
        "updated_at": _now(),
        "stages": STAGES,
    })
    jobs[module_key] = job
    state["module_insight_jobs"] = jobs
    store.save_state(project_id, state)


def clear_job(store: ProjectStore, project_id: str, module_key: str, *, delay_done: bool = True) -> None:
    state = store.load_state(project_id)
    jobs = _jobs(state)
    job = jobs.get(module_key)
    if not job:
        return
    if delay_done and job.get("status") == "done":
        # 保留完成态供前端展示，由前端轮询后忽略或稍后清理
        return
    jobs.pop(module_key, None)
    state["module_insight_jobs"] = jobs
    store.save_state(project_id, state)


def remove_finished_jobs(store: ProjectStore, project_id: str, older_than_sec: int = 30) -> None:
    state = store.load_state(project_id)
    jobs = _jobs(state)
    now = datetime.now()
    kept: dict[str, Any] = {}
    for key, job in jobs.items():
        if job.get("status") not in ("done", "error"):
            kept[key] = job
            continue
        updated = job.get("updated_at", "")
        try:
            ts = datetime.fromisoformat(updated)
        except Exception:
            kept[key] = job
            continue
        if (now - ts).total_seconds() < older_than_sec:
            kept[key] = job
    state["module_insight_jobs"] = kept
    store.save_state(project_id, state)


def list_jobs(store: ProjectStore, project_id: str) -> dict[str, Any]:
    state = store.load_state(project_id)
    jobs = _jobs(state)
    running = [j for j in jobs.values() if j.get("status") == "running"]
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
