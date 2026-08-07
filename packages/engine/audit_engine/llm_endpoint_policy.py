"""LLM endpoint 白名单 — 持久化 + 环境变量引导，供 UI 与运行时共同使用。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from audit_engine.llm_config import PROVIDER_PRESETS
from audit_engine.locking import file_lock
from audit_engine.runtime import storage_root

_ENV_ALLOWLIST = "AUDIT_WORKBENCH_LLM_ENDPOINT_ALLOWLIST"


def _path() -> Path:
    return storage_root() / "llm_endpoint_allowlist.json"


def _lock_path() -> Path:
    return storage_root() / ".llm_endpoint_allowlist.lock"


def default_seed_hosts() -> list[str]:
    hosts: list[str] = []
    for preset in PROVIDER_PRESETS:
        url = str(preset.get("base_url") or "").strip()
        host = extract_host(url)
        if host and host not in hosts:
            hosts.append(host)
    for extra in ("localhost", "127.0.0.1"):
        if extra not in hosts:
            hosts.append(extra)
    return hosts


def extract_host(base_url: str) -> str:
    text = str(base_url or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "https://" + text
    return (urlparse(text).hostname or "").lower().strip()


def _normalize_hosts(hosts: list[str] | None) -> list[str]:
    out: list[str] = []
    for item in hosts or []:
        host = extract_host(str(item)) if "/" in str(item) or ":" in str(item) else str(item).strip().lower()
        host = host.lstrip(".")
        if host and host not in out:
            out.append(host)
    return out


def _env_allowlist() -> list[str] | None:
    """None=未配置；[]=显式放行全部（*）；非空=host 列表。"""
    raw = os.environ.get(_ENV_ALLOWLIST, "").strip()
    if not raw:
        return None
    if raw == "*":
        return []
    return _normalize_hosts(raw.split(","))


def _read_file() -> dict[str, Any] | None:
    path = _path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def load_endpoint_allowlist() -> dict[str, Any]:
    """返回白名单状态。

    优先级：环境变量（若设置）> 持久化文件 > 默认关闭强制（enabled=false，附带建议种子）。
    """
    env_hosts = _env_allowlist()
    if env_hosts is not None:
        return {
            "enabled": True,
            "hosts": env_hosts,
            "allow_all": env_hosts == [],
            "source": "env",
            "seed_hosts": default_seed_hosts(),
            "env_overrides_file": True,
        }

    data = _read_file()
    if data is None:
        return {
            "enabled": False,
            "hosts": [],
            "allow_all": False,
            "source": "default",
            "seed_hosts": default_seed_hosts(),
            "env_overrides_file": False,
        }

    hosts = _normalize_hosts(list(data.get("hosts") or []))
    allow_all = bool(data.get("allow_all", False))
    enabled = bool(data.get("enabled", True))
    if allow_all:
        hosts = []
    return {
        "enabled": enabled,
        "hosts": hosts,
        "allow_all": allow_all,
        "source": "file",
        "seed_hosts": default_seed_hosts(),
        "env_overrides_file": False,
        "updated_at": str(data.get("updated_at") or ""),
    }


def save_endpoint_allowlist(
    *,
    enabled: bool,
    hosts: list[str] | None = None,
    allow_all: bool = False,
) -> dict[str, Any]:
    if _env_allowlist() is not None:
        raise ValueError(
            f"当前由环境变量 {_ENV_ALLOWLIST} 接管白名单，无法通过 UI/API 修改。"
            "请先取消该环境变量后重试。"
        )
    cleaned = [] if allow_all else _normalize_hosts(hosts)
    from datetime import datetime, timezone

    payload = {
        "enabled": bool(enabled),
        "allow_all": bool(allow_all),
        "hosts": cleaned,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    path = _path()
    with file_lock(_lock_path(), exclusive=True):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    return load_endpoint_allowlist()


def assert_llm_endpoint_allowed(base_url: str) -> None:
    """若白名单启用，则拦截未授权 endpoint；非 loopback 必须 HTTPS。"""
    text = str(base_url or "").strip()
    if text:
        parsed = urlparse(text if "://" in text else "https://" + text)
        scheme = (parsed.scheme or "").lower()
        host = (parsed.hostname or "").lower()
        loopback = host in {"localhost", "127.0.0.1", "::1"}
        if not loopback and scheme and scheme != "https":
            raise ValueError(
                f"非本机 LLM endpoint 必须使用 HTTPS：{base_url}。"
                "localhost / 127.0.0.1 允许 HTTP。"
            )

    policy = load_endpoint_allowlist()
    if not policy.get("enabled"):
        return
    if policy.get("allow_all"):
        return
    host = extract_host(base_url)
    allowed = set(policy.get("hosts") or [])
    if not host or host not in allowed:
        raise ValueError(
            f"LLM endpoint 不在允许列表中：{base_url or '(空)'}。"
            f"请在「大模型 → Endpoint 白名单」中添加 host"
            f"（当前允许：{sorted(allowed) or '（空）'}）。"
        )


def endpoint_allowed(base_url: str) -> bool:
    try:
        assert_llm_endpoint_allowed(base_url)
        return True
    except ValueError:
        return False
