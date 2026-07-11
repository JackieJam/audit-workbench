"""LLM profile & model configuration API."""

from __future__ import annotations

from typing import Any

from audit_engine import secret_store
from audit_engine.llm_config import (
    PROVIDER_PRESETS,
    forget_key,
    has_remembered_key,
    key_account,
    remember_key,
    resolve_profile_key,
)
from audit_engine.llm_models import fetch_models, ping
from audit_engine.llm_profiles import (
    delete_profile,
    get_profile,
    list_profiles,
    save_profile,
    set_default_profile,
)
from audit_engine.llm_runtime import resolve_llm_runtime
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/llm", tags=["llm-config"])


class ProfileBody(BaseModel):
    profile_id: str = ""
    profile_name: str = Field(..., min_length=1, max_length=80)
    base_url: str = Field(..., min_length=4, max_length=256)
    model: str = Field(..., min_length=1, max_length=128)
    keychain_account: str = ""
    set_default: bool = False


class KeyBody(BaseModel):
    api_key: str = Field(..., min_length=8, max_length=512)


class ModelsRequest(BaseModel):
    profile_id: str = ""
    base_url: str = ""
    api_key: str = ""


class PingRequest(BaseModel):
    profile_id: str = ""
    base_url: str = ""
    model: str = ""
    api_key: str = ""


def _profile_public(profile: dict[str, Any]) -> dict[str, Any]:
    account = key_account(profile)
    return {
        **profile,
        "key_configured": has_remembered_key(account),
        "secret_backend": secret_store.backend_name(),
    }


@router.get("/presets")
def llm_presets() -> dict:
    return {"presets": PROVIDER_PRESETS, "secret_backend": secret_store.backend_name()}


@router.get("/profiles")
def llm_list_profiles() -> dict:
    profiles = [_profile_public(p) for p in list_profiles()]
    return {"profiles": profiles, "secret_backend": secret_store.backend_name()}


@router.post("/profiles")
def llm_save_profile(body: ProfileBody) -> dict:
    try:
        saved = save_profile(body.model_dump(), set_default=body.set_default)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"profile": _profile_public(saved)}


@router.delete("/profiles/{profile_id}")
def llm_delete_profile(profile_id: str) -> dict:
    if not delete_profile(profile_id):
        raise HTTPException(status_code=404, detail="方案不存在")
    return {"ok": True}


@router.post("/profiles/{profile_id}/default")
def llm_set_default(profile_id: str) -> dict:
    saved = set_default_profile(profile_id)
    if not saved:
        raise HTTPException(status_code=404, detail="方案不存在")
    return {"profile": _profile_public(saved)}


@router.post("/profiles/{profile_id}/key")
def llm_save_key(profile_id: str, body: KeyBody) -> dict:
    profile = get_profile(profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="方案不存在")
    account = key_account(profile)
    if not remember_key(account, body.api_key.strip()):
        raise HTTPException(status_code=500, detail="密钥保存失败")
    return {"ok": True, "key_configured": True, "keychain_account": account}


@router.delete("/profiles/{profile_id}/key")
def llm_forget_key(profile_id: str) -> dict:
    profile = get_profile(profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail="方案不存在")
    forget_key(key_account(profile))
    return {"ok": True, "key_configured": False}


@router.post("/models")
def llm_fetch_models(
    body: ModelsRequest,
    x_llm_api_key: str | None = Header(default=None, alias="X-LLM-Api-Key"),
) -> dict:
    profile = get_profile(body.profile_id) if body.profile_id else None
    manual = (body.api_key or x_llm_api_key or "").strip()
    if profile:
        api_key, _ = resolve_profile_key(profile, manual=manual)
        base_url = (body.base_url or profile.get("base_url") or "").strip()
    else:
        runtime = resolve_llm_runtime(profile_id=body.profile_id or None, manual_key=manual or None)
        api_key = manual or runtime.api_key
        base_url = (body.base_url or runtime.base_url).strip()
    try:
        return fetch_models(api_key=api_key.strip(), base_url=base_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _resolve_ping_credentials(body: PingRequest, header_key: str | None) -> tuple[str, str, str, str]:
    profile = get_profile(body.profile_id) if body.profile_id else None
    if profile is None and body.profile_id:
        raise HTTPException(status_code=404, detail="方案不存在")
    manual = (body.api_key or header_key or "").strip()
    if profile:
        api_key, key_source = resolve_profile_key(profile, manual=manual)
        norm = profile
        base_url = (body.base_url or norm.get("base_url") or "").strip()
        model = (body.model or norm.get("model") or "").strip()
        return api_key, key_source, base_url, model
    runtime = resolve_llm_runtime(manual_key=manual or None)
    base_url = (body.base_url or runtime.base_url).strip()
    model = (body.model or runtime.model).strip()
    api_key = manual or runtime.api_key
    return api_key.strip(), runtime.key_source, base_url, model


@router.post("/ping")
def llm_ping(
    body: PingRequest,
    x_llm_api_key: str | None = Header(default=None, alias="X-LLM-Api-Key"),
) -> dict:
    api_key, key_source, base_url, model = _resolve_ping_credentials(body, x_llm_api_key)
    if not api_key:
        raise HTTPException(status_code=400, detail="未配置 LLM API Key。请填入 Key 或保存到本机存储。")
    try:
        result = ping(api_key=api_key, base_url=base_url, model=model)
        result["key_source"] = key_source
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"连接失败：{exc}") from exc


@router.get("/active")
def llm_active(
    profile_id: str = "",
    x_llm_api_key: str | None = Header(default=None, alias="X-LLM-Api-Key"),
) -> dict:
    runtime = resolve_llm_runtime(profile_id=profile_id or None, manual_key=x_llm_api_key)
    return {
        "profile_id": runtime.profile_id,
        "profile_name": runtime.profile_name,
        "model": runtime.model,
        "base_url": runtime.base_url,
        "key_configured": bool(runtime.api_key),
        "key_source": runtime.key_source,
    }
