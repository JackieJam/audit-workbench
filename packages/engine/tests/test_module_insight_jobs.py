from audit_engine.module_insight_jobs import STAGE_BY_ID, list_jobs, set_job_stage
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
