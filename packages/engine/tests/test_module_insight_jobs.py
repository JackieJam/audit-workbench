from datetime import datetime, timedelta

from audit_engine.module_insight_jobs import (
    STAGE_BY_ID,
    list_jobs,
    queue_job,
    reconcile_jobs,
    set_job_stage,
)
from audit_engine.store import ProjectStore


def test_stage_percent(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_WORKBENCH_DATA_ROOT", str(tmp_path))
    store = ProjectStore(root=tmp_path)
    manifest = store.create_project("t", project_id="p1")
    job = set_job_stage(store, manifest.project_id, "收入成本", "llm")
    assert job["percent"] == STAGE_BY_ID["llm"]["percent"]
    snap = list_jobs(store, manifest.project_id)
    assert snap["running_count"] == 1
    assert snap["overall_percent"] == job["percent"]


def test_queue_job_reuses_active_module(tmp_path):
    store = ProjectStore(root=tmp_path)
    pid = store.create_project("t", project_id="p1").project_id

    first, created = queue_job(store, pid, "收入成本")
    second, created_again = queue_job(store, pid, "收入成本")

    assert created is True
    assert created_again is False
    assert first["job_id"] == second["job_id"]
    assert second["status"] == "queued"
    assert list_jobs(store, pid)["running_count"] == 1


def test_reconcile_marks_interrupted_job_and_agent_event_error(tmp_path):
    store = ProjectStore(root=tmp_path)
    pid = store.create_project("t", project_id="p1").project_id
    job, _ = queue_job(store, pid, "收入成本")
    old = (datetime.now() - timedelta(minutes=10)).isoformat(timespec="seconds")

    def seed(state):
        state["module_insight_jobs"]["收入成本"]["updated_at"] = old
        state["agent_thread"] = {
            "messages": [
                {
                    "role": "assistant",
                    "content": "已创建任务",
                    "tool_calls": [
                        {
                            "tool": "run_module_insight",
                            "args": {"module": "income"},
                            "result": {"job_id": job["job_id"], "status": "queued"},
                        }
                    ],
                }
            ]
        }

    store.update_state(pid, seed)
    reconcile_jobs(store, pid, stale_after_sec=1, retain_finished_sec=30)

    state = store.load_state(pid)
    failed = state["module_insight_jobs"]["收入成本"]
    tool_result = state["agent_thread"]["messages"][0]["tool_calls"][0]["result"]
    assert failed["status"] == "error"
    assert "进程中断" in failed["error"]
    assert tool_result["status"] == "error"
