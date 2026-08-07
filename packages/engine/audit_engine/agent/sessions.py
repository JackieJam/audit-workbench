"""Agent 多会话（Codex 风格）存储。

消息按 session 隔离；pinned_context / pending_actions / audit_events 仍挂在
project 级 agent_thread 上（跨会话共享选中销与审批队列）。
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

DEFAULT_SESSION_TITLE = "新对话"
MAX_SESSIONS = 40
MAX_HISTORY = 40


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _new_session_id() -> str:
    return "s_" + hashlib.sha1(f"{_now()}|{datetime.now().timestamp()}".encode()).hexdigest()[:12]


def title_from_messages(messages: list[dict[str, Any]] | None) -> str:
    for message in messages or []:
        if message.get("role") != "user":
            continue
        text = str(message.get("content") or "").strip().replace("\n", " ")
        if not text:
            continue
        return text[:40] + ("…" if len(text) > 40 else "")
    return DEFAULT_SESSION_TITLE


def _empty_session(*, title: str = DEFAULT_SESSION_TITLE, messages: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    now = _now()
    msgs = list(messages or [])
    return {
        "id": _new_session_id(),
        "title": title if title != DEFAULT_SESSION_TITLE else title_from_messages(msgs),
        "created_at": now,
        "updated_at": now,
        "messages": msgs,
    }


def ensure_agent_sessions(state: dict[str, Any]) -> dict[str, Any]:
    """保证 agent_sessions 存在；把旧 agent_thread.messages 迁入首个会话。"""
    raw = state.get("agent_sessions")
    if isinstance(raw, dict) and isinstance(raw.get("items"), dict) and raw["items"]:
        items = {str(k): dict(v) for k, v in raw["items"].items() if isinstance(v, dict)}
        active_id = str(raw.get("active_id") or "")
        if active_id not in items:
            # 选最近更新的会话
            active_id = max(
                items.keys(),
                key=lambda sid: str(items[sid].get("updated_at") or ""),
            )
        sessions = {"active_id": active_id, "items": items}
        state["agent_sessions"] = sessions
        return sessions

    thread = dict(state.get("agent_thread") or {})
    legacy_messages = list(thread.get("messages") or [])
    session = _empty_session(messages=legacy_messages)
    if legacy_messages:
        session["title"] = title_from_messages(legacy_messages)
        if thread.get("updated_at"):
            session["updated_at"] = thread["updated_at"]
            session["created_at"] = thread.get("updated_at") or session["created_at"]
    # 避免双写：messages 只留在 sessions
    if "messages" in thread:
        thread["messages"] = []
        state["agent_thread"] = thread
    sessions = {"active_id": session["id"], "items": {session["id"]: session}}
    state["agent_sessions"] = sessions
    return sessions


def get_active_session(state: dict[str, Any]) -> dict[str, Any]:
    sessions = ensure_agent_sessions(state)
    return dict(sessions["items"][sessions["active_id"]])


def active_messages(state: dict[str, Any]) -> list[dict[str, Any]]:
    return list(get_active_session(state).get("messages") or [])


def set_active_messages(
    state: dict[str, Any],
    messages: list[dict[str, Any]],
    *,
    updated_at: str | None = None,
) -> dict[str, Any]:
    sessions = ensure_agent_sessions(state)
    sid = sessions["active_id"]
    sess = dict(sessions["items"][sid])
    trimmed = list(messages)[-MAX_HISTORY:]
    sess["messages"] = trimmed
    sess["updated_at"] = updated_at or _now()
    if sess.get("title") in (None, "", DEFAULT_SESSION_TITLE):
        sess["title"] = title_from_messages(trimmed)
    sessions["items"][sid] = sess
    state["agent_sessions"] = sessions
    return sess


def append_active_notice(state: dict[str, Any], content: str, *, at: str | None = None) -> None:
    """系统通知写入当前活动会话（导入失效等）。"""
    ensure_agent_sessions(state)
    now = at or _now()
    messages = active_messages(state)
    messages.append({"role": "assistant", "content": content, "at": now})
    set_active_messages(state, messages, updated_at=now)


def list_session_summaries(state: dict[str, Any]) -> list[dict[str, Any]]:
    sessions = ensure_agent_sessions(state)
    rows: list[dict[str, Any]] = []
    for sess in sessions["items"].values():
        rows.append(
            {
                "id": sess["id"],
                "title": sess.get("title") or DEFAULT_SESSION_TITLE,
                "created_at": sess.get("created_at"),
                "updated_at": sess.get("updated_at"),
                "message_count": len(sess.get("messages") or []),
                "active": sess["id"] == sessions["active_id"],
            }
        )
    rows.sort(key=lambda row: str(row.get("updated_at") or ""), reverse=True)
    return rows


def _prune_sessions(sessions: dict[str, Any]) -> None:
    items = sessions["items"]
    if len(items) <= MAX_SESSIONS:
        return
    ordered = sorted(
        items.values(),
        key=lambda s: str(s.get("updated_at") or ""),
        reverse=True,
    )
    keep_ids = {s["id"] for s in ordered[:MAX_SESSIONS]}
    keep_ids.add(sessions["active_id"])
    sessions["items"] = {sid: items[sid] for sid in keep_ids if sid in items}


def create_session(
    state: dict[str, Any],
    *,
    title: str = DEFAULT_SESSION_TITLE,
    activate: bool = True,
) -> dict[str, Any]:
    sessions = ensure_agent_sessions(state)
    active = dict(sessions["items"][sessions["active_id"]])
    # 当前已是空白「新对话」时复用，避免连点产生一堆空会话
    if (
        activate
        and not (active.get("messages") or [])
        and (active.get("title") in (None, "", DEFAULT_SESSION_TITLE))
        and title == DEFAULT_SESSION_TITLE
    ):
        return active
    sess = _empty_session(title=title)
    sessions["items"][sess["id"]] = sess
    if activate:
        sessions["active_id"] = sess["id"]
    _prune_sessions(sessions)
    state["agent_sessions"] = sessions
    return sess


def activate_session(state: dict[str, Any], session_id: str) -> dict[str, Any]:
    sessions = ensure_agent_sessions(state)
    if session_id not in sessions["items"]:
        raise ValueError("会话不存在")
    sessions["active_id"] = session_id
    state["agent_sessions"] = sessions
    return dict(sessions["items"][session_id])


def rename_session(state: dict[str, Any], session_id: str, title: str) -> dict[str, Any]:
    sessions = ensure_agent_sessions(state)
    if session_id not in sessions["items"]:
        raise ValueError("会话不存在")
    text = (title or "").strip() or DEFAULT_SESSION_TITLE
    sess = dict(sessions["items"][session_id])
    sess["title"] = text[:60]
    sess["updated_at"] = _now()
    sessions["items"][session_id] = sess
    state["agent_sessions"] = sessions
    return sess


def delete_session(state: dict[str, Any], session_id: str) -> dict[str, Any]:
    sessions = ensure_agent_sessions(state)
    if session_id not in sessions["items"]:
        raise ValueError("会话不存在")
    del sessions["items"][session_id]
    if not sessions["items"]:
        sess = _empty_session()
        sessions["items"] = {sess["id"]: sess}
        sessions["active_id"] = sess["id"]
    elif sessions["active_id"] == session_id:
        # 切到最近更新的剩余会话
        sessions["active_id"] = max(
            sessions["items"].keys(),
            key=lambda sid: str(sessions["items"][sid].get("updated_at") or ""),
        )
    state["agent_sessions"] = sessions
    return {"active_id": sessions["active_id"], "deleted_id": session_id}


def iter_session_messages(state: dict[str, Any]):
    """遍历所有会话消息（用于后台 job 回写）。"""
    sessions = ensure_agent_sessions(state)
    for sess in sessions["items"].values():
        for message in sess.get("messages") or []:
            yield sess, message


def touch_session_updated(state: dict[str, Any], session_id: str, *, at: str | None = None) -> None:
    sessions = ensure_agent_sessions(state)
    if session_id not in sessions["items"]:
        return
    sess = dict(sessions["items"][session_id])
    sess["updated_at"] = at or _now()
    sessions["items"][session_id] = sess
    state["agent_sessions"] = sessions
