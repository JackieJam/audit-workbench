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


def require_storage_namespace() -> bool:
    """服务器多人部署时可设 AUDIT_WORKBENCH_REQUIRE_NAMESPACE=1 强制租户隔离。"""
    flag = os.environ.get("AUDIT_WORKBENCH_REQUIRE_NAMESPACE", "").strip().lower()
    return flag in {"1", "true", "yes", "on"}


def assert_storage_isolation() -> None:
    """未启用命名空间却要求隔离时，在 API 启动阶段失败，避免默认同目录共享。"""
    if require_storage_namespace() and not storage_namespace():
        raise RuntimeError(
            "已启用 AUDIT_WORKBENCH_REQUIRE_NAMESPACE，但未设置 "
            "AUDIT_WORKBENCH_NAMESPACE / AUDIT_WORKBENCH_USER_ID。"
            "多人部署前必须显式隔离存储命名空间。"
        )


def storage_root() -> Path:
    assert_storage_isolation()
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


def deployment_profile() -> dict[str, object]:
    ns = storage_namespace()
    isolation_mode = "namespaced" if ns else "shared"
    profile = {
        "isolation_mode": isolation_mode,
        "storage_namespace": ns,
        "require_namespace": require_storage_namespace(),
        "deployment_profile": "single_user_local" if isolation_mode == "shared" else "namespaced",
    }
    if isolation_mode == "shared":
        profile["warning"] = (
            "当前为共享存储根目录（未设置 AUDIT_WORKBENCH_NAMESPACE / "
            "AUDIT_WORKBENCH_USER_ID）。多人服务器部署前需启用租户/用户隔离，"
            "否则项目数据、经验库与密钥可能互相可见。"
        )
    return profile
