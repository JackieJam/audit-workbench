"""审计 Agent 对话 API。"""

from __future__ import annotations

from typing import Any

from audit_engine.agent.orchestrator import (
    clear_agent_thread,
    get_agent_state,
    resolve_pending_action,
    run_agent_chat,
    set_pinned_context,
)
from audit_engine.store import ProjectStore
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from audit_api.deps import get_store
from audit_api.routers.analysis import _manifest_or_404

router = APIRouter(prefix="/projects", tags=["agent"])


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    pinned_context: dict[str, Any] | None = None
    profile_id: str = ""
    api_key: str = ""  # 可选；放 body 避免 header 非 ASCII 限制


class ContextRequest(BaseModel):
    pinned_context: dict[str, Any] | None = None


@router.get("/{project_id}/agent/state")
def agent_state(project_id: str, store: ProjectStore = Depends(get_store)) -> dict:
    _manifest_or_404(store, project_id)
    return get_agent_state(store, project_id)


@router.put("/{project_id}/agent/context")
def agent_context(project_id: str, body: ContextRequest, store: ProjectStore = Depends(get_store)) -> dict:
    _manifest_or_404(store, project_id)
    return set_pinned_context(store, project_id, body.pinned_context)


@router.post("/{project_id}/agent/chat")
def agent_chat(
    project_id: str,
    body: ChatRequest,
    store: ProjectStore = Depends(get_store),
    x_llm_api_key: str | None = Header(default=None, alias="X-LLM-Api-Key"),
    x_llm_profile_id: str | None = Header(default=None, alias="X-LLM-Profile-Id"),
) -> dict:
    _manifest_or_404(store, project_id)
    try:
        profile_id = (body.profile_id or x_llm_profile_id or "").strip() or None
        api_key = (body.api_key or x_llm_api_key or "").strip() or None
        return run_agent_chat(
            store,
            project_id,
            user_message=body.message.strip(),
            pinned_context=body.pinned_context,
            api_key=api_key,
            profile_id=profile_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/{project_id}/agent/thread")
def agent_clear(project_id: str, store: ProjectStore = Depends(get_store)) -> dict:
    _manifest_or_404(store, project_id)
    clear_agent_thread(store, project_id)
    return {"ok": True}


@router.post("/{project_id}/agent/actions/{action_id}/approve")
def agent_approve_action(
    project_id: str,
    action_id: str,
    store: ProjectStore = Depends(get_store),
) -> dict:
    _manifest_or_404(store, project_id)
    try:
        return resolve_pending_action(store, project_id, action_id, approve=True)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{project_id}/agent/actions/{action_id}/reject")
def agent_reject_action(
    project_id: str,
    action_id: str,
    store: ProjectStore = Depends(get_store),
) -> dict:
    _manifest_or_404(store, project_id)
    try:
        return resolve_pending_action(store, project_id, action_id, approve=False)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
