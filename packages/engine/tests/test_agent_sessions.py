"""Agent multi-session storage tests."""

from __future__ import annotations

from audit_engine.agent.orchestrator import (
    activate_agent_session,
    create_agent_session,
    delete_agent_session,
    get_agent_state,
)
from audit_engine.agent.sessions import ensure_agent_sessions, list_session_summaries
from audit_engine.store import ProjectStore


def _seed(store: ProjectStore) -> str:
    return store.create_project("session-test").project_id


def test_migrate_legacy_thread_messages_into_session(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_WORKBENCH_DATA_ROOT", str(tmp_path))
    store = ProjectStore(root=tmp_path)
    pid = _seed(store)

    def seed(state):
        state["agent_thread"] = {
            "messages": [
                {"role": "user", "content": "旧话题：费用异常"},
                {"role": "assistant", "content": "已分析"},
            ],
            "pinned_context": {"label": "费用"},
        }

    store.update_state(pid, seed)
    state = get_agent_state(store, pid)
    assert state["active_session_id"]
    assert state["messages"][0]["content"] == "旧话题：费用异常"
    assert state["sessions"][0]["title"] == "旧话题：费用异常"
    assert state["pinned_context"]["label"] == "费用"
    # 旧 messages 已迁出
    raw = store.load_state(pid)
    assert raw["agent_thread"].get("messages") in (None, [])


def test_create_session_keeps_history(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_WORKBENCH_DATA_ROOT", str(tmp_path))
    store = ProjectStore(root=tmp_path)
    pid = _seed(store)

    def seed(state):
        ensure_agent_sessions(state)
        from audit_engine.agent.sessions import set_active_messages

        set_active_messages(
            state,
            [
                {"role": "user", "content": "第一轮"},
                {"role": "assistant", "content": "答1"},
            ],
        )

    store.update_state(pid, seed)
    before = get_agent_state(store, pid)
    old_id = before["active_session_id"]

    after = create_agent_session(store, pid)
    assert after["active_session_id"] != old_id
    assert after["messages"] == []
    assert len(after["sessions"]) == 2
    assert any(s["id"] == old_id for s in after["sessions"])

    switched = activate_agent_session(store, pid, old_id)
    assert switched["active_session_id"] == old_id
    assert switched["messages"][0]["content"] == "第一轮"


def test_delete_active_session_switches_to_other(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_WORKBENCH_DATA_ROOT", str(tmp_path))
    store = ProjectStore(root=tmp_path)
    pid = _seed(store)

    def seed_msg(state):
        ensure_agent_sessions(state)
        from audit_engine.agent.sessions import set_active_messages

        set_active_messages(state, [{"role": "user", "content": "有内容的会话"}])

    store.update_state(pid, seed_msg)
    first = get_agent_state(store, pid)
    sid_a = first["active_session_id"]
    second = create_agent_session(store, pid)
    sid_b = second["active_session_id"]
    assert sid_a != sid_b

    after = delete_agent_session(store, pid, sid_b)
    assert after["active_session_id"] != sid_b
    assert all(s["id"] != sid_b for s in after["sessions"])
    assert any(s["id"] == after["active_session_id"] for s in after["sessions"])


def test_list_summaries_sorted_by_updated(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_WORKBENCH_DATA_ROOT", str(tmp_path))
    store = ProjectStore(root=tmp_path)
    pid = _seed(store)

    def seed_msg(state):
        ensure_agent_sessions(state)
        from audit_engine.agent.sessions import set_active_messages

        set_active_messages(state, [{"role": "user", "content": "会话A"}])

    store.update_state(pid, seed_msg)
    create_agent_session(store, pid)
    state = store.load_state(pid)
    rows = list_session_summaries(state)
    assert len(rows) >= 2
    assert rows[0]["updated_at"] >= rows[1]["updated_at"]


def test_create_on_blank_session_reuses(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_WORKBENCH_DATA_ROOT", str(tmp_path))
    store = ProjectStore(root=tmp_path)
    pid = _seed(store)
    first = create_agent_session(store, pid)
    second = create_agent_session(store, pid)
    assert first["active_session_id"] == second["active_session_id"]
    assert len(second["sessions"]) == 1
