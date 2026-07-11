from __future__ import annotations

from audit_engine.runtime import storage_root, workbench_version
from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "version": workbench_version(),
        "storage_root": str(storage_root()),
    }
