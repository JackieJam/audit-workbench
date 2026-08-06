"""Resolve optional local real-case journal directory (never committed)."""

from __future__ import annotations

import os
from pathlib import Path


def real_case_dir() -> Path | None:
    """Return local real-case dir from env, or None if unset/missing.

    Set ``AUDIT_REAL_CASE_DIR`` on the developer machine. Do not hardcode
    Downloads paths or client filenames in the repo.
    """
    raw = (os.environ.get("AUDIT_REAL_CASE_DIR") or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_dir() else None


def real_case_file(*parts: str) -> Path | None:
    root = real_case_dir()
    if root is None:
        return None
    path = root.joinpath(*parts)
    return path if path.is_file() else None
