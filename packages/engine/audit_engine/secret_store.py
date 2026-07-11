"""Local secret storage — keychain on macOS, file fallback."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from audit_engine.locking import file_lock
from audit_engine.runtime import storage_root

SERVICE_NAME = "audit-workbench-llm-api-key"
SECRET_FILE = "secrets.json"


def _backend() -> str:
    explicit = os.environ.get("AUDIT_WORKBENCH_SECRET_BACKEND", "").strip()
    if explicit in {"keychain", "file"}:
        return explicit
    return "keychain" if sys.platform == "darwin" and shutil.which("security") else "file"


def _secret_path() -> Path:
    return storage_root() / SECRET_FILE


def _secret_lock_path() -> Path:
    return storage_root() / f".{SECRET_FILE}.lock"


def _load_file_secrets() -> dict[str, str]:
    path = _secret_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if str(k).strip() and str(v).strip()}


def _save_file_secrets(secrets: dict[str, str]) -> bool:
    path = _secret_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(json.dumps(secrets, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)
    try:
        path.chmod(0o600)
    except Exception:
        pass
    return True


def _keychain_get(account: str) -> str:
    result = subprocess.run(
        ["security", "find-generic-password", "-s", SERVICE_NAME, "-a", account, "-w"],
        capture_output=True,
        text=True,
        timeout=8,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _keychain_set(account: str, secret: str) -> bool:
    result = subprocess.run(
        ["security", "add-generic-password", "-U", "-s", SERVICE_NAME, "-a", account, "-w", secret],
        capture_output=True,
        text=True,
        timeout=8,
    )
    return result.returncode == 0


def _keychain_delete(account: str) -> bool:
    result = subprocess.run(
        ["security", "delete-generic-password", "-s", SERVICE_NAME, "-a", account],
        capture_output=True,
        text=True,
        timeout=8,
    )
    return result.returncode == 0


def get_secret(account: str) -> str:
    if not account:
        return ""
    if _backend() == "keychain":
        try:
            return _keychain_get(account)
        except Exception:
            return ""
    with file_lock(_secret_lock_path(), exclusive=False):
        return _load_file_secrets().get(account, "")


def set_secret(account: str, secret: str) -> bool:
    if not account or not secret:
        return False
    if _backend() == "keychain":
        try:
            return _keychain_set(account, secret)
        except Exception:
            return False
    with file_lock(_secret_lock_path(), exclusive=True):
        secrets = _load_file_secrets()
        secrets[account] = secret
        return _save_file_secrets(secrets)


def delete_secret(account: str) -> bool:
    if not account:
        return False
    if _backend() == "keychain":
        try:
            return _keychain_delete(account)
        except Exception:
            return False
    with file_lock(_secret_lock_path(), exclusive=True):
        secrets = _load_file_secrets()
        if account not in secrets:
            return False
        secrets.pop(account, None)
        return _save_file_secrets(secrets)


def backend_name() -> str:
    return _backend()


def has_secret(account: str) -> bool:
    return bool(get_secret(account))
