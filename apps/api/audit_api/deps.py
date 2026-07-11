from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from audit_engine.pipeline import AnalysisPipeline
from audit_engine.store import ProjectStore


@lru_cache
def get_store() -> ProjectStore:
    root = os.environ.get("AUDIT_WORKBENCH_DATA_ROOT")
    if root:
        return ProjectStore(root=Path(root))
    return ProjectStore()


@lru_cache
def get_pipeline() -> AnalysisPipeline:
    return AnalysisPipeline(get_store())


def api_port() -> int:
    return int(os.environ.get("AUDIT_API_PORT", "29180"))
