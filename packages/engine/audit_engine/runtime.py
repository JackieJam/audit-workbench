"""Local storage root — no Streamlit, no web framework."""

from __future__ import annotations

import os
import re
from pathlib import Path

_SANITIZE_RE = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff._-]+")


def _sanitize_namespace(value: str) -> str:
    cleaned = _SANITIZE_RE.sub("_", value.strip())
    cleaned = cleaned.strip("._-")[:64]
    return cleaned or "shared"


def storage_namespace() -> str | None:
    explicit = os.environ.get("AUDIT_WORKBENCH_NAMESPACE", "").strip()
    if explicit:
        return _sanitize_namespace(explicit)
    explicit = os.environ.get("AUDIT_WORKBENCH_USER_ID", "").strip()
    if explicit:
        return _sanitize_namespace(explicit)
    return None


def storage_root() -> Path:
    env_root = os.environ.get("AUDIT_WORKBENCH_DATA_ROOT", "").strip()
    if env_root:
        return Path(env_root)
    base = Path.home() / ".audit_tool"
    ns = storage_namespace()
    if not ns:
        return base
    return base / "users" / ns


def workbench_version() -> str:
    return os.environ.get("AUDIT_WORKBENCH_VERSION", "0.1.0")
