"""Cross-platform file locking."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl  # type: ignore
except Exception:  # pragma: no cover
    fcntl = None


@contextmanager
def file_lock(lock_path: Path, exclusive: bool = True):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+", encoding="utf-8") as lock_file:
        if fcntl is not None:
            lock_file.seek(0)
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
