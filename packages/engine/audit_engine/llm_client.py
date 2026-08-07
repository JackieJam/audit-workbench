"""OpenAI-compatible client — always bypass system HTTP/SOCKS proxy."""

from __future__ import annotations

import httpx
from openai import OpenAI

from audit_engine.llm_endpoint_policy import assert_llm_endpoint_allowed


def make_openai_client(
    *,
    api_key: str,
    base_url: str,
    max_retries: int = 2,
    timeout: float = 90.0,
) -> OpenAI:
    """Create an OpenAI SDK client that ignores HTTP_PROXY / ALL_PROXY env vars."""
    assert_llm_endpoint_allowed(base_url)
    http_client = httpx.Client(trust_env=False, timeout=timeout)
    return OpenAI(
        api_key=api_key,
        base_url=base_url,
        max_retries=max_retries,
        http_client=http_client,
    )
