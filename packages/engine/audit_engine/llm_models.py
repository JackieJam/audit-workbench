"""Fetch model list from OpenAI-compatible providers."""

from __future__ import annotations

from audit_engine.llm_client import make_openai_client
from audit_engine.llm_config import STATIC_MODEL_HINTS


def fetch_models(*, api_key: str, base_url: str) -> dict:
    if not api_key:
        raise ValueError("缺少 API Key，无法获取模型列表")
    if not base_url:
        raise ValueError("缺少 Base URL")

    client = make_openai_client(api_key=api_key, base_url=base_url, max_retries=1, timeout=25.0)
    try:
        resp = client.models.list(timeout=20.0)
        models = sorted({m.id for m in resp.data if getattr(m, "id", None)})
        return {"models": models, "source": "api", "count": len(models)}
    except Exception as exc:
        hints = STATIC_MODEL_HINTS.get(base_url.rstrip("/"), [])
        if not hints:
            for key, values in STATIC_MODEL_HINTS.items():
                if base_url.rstrip("/").startswith(key.rstrip("/")):
                    hints = values
                    break
        if hints:
            return {
                "models": hints,
                "source": "static",
                "count": len(hints),
                "warning": f"远程获取失败，已返回预设列表：{exc}",
            }
        raise ValueError(f"获取模型列表失败：{exc}") from exc


def ping(*, api_key: str, base_url: str, model: str) -> dict:
    if not api_key:
        raise ValueError("缺少 API Key")
    client = make_openai_client(api_key=api_key, base_url=base_url, max_retries=1, timeout=25.0)
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "ping"}],
        max_tokens=8,
        temperature=0,
        timeout=25.0,
    )
    content = (resp.choices[0].message.content or "").strip()
    return {"ok": True, "model": model, "reply_preview": content[:80]}
