"""审计模块风险问题 — 与 config/audit_questions.json 对齐。"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
_QUESTIONS_PATH = _REPO_ROOT / "config" / "audit_questions.json"

MODULE_ID_TO_KEY: dict[str, str] = {
    "income": "收入成本",
    "expense": "费用",
    "working_capital": "暂估往来",
    "balance_sheet": "资产负债",
    "adjustment": "调账冲销",
}

MODULE_KEY_TO_ID: dict[str, str] = {v: k for k, v in MODULE_ID_TO_KEY.items()}


@lru_cache(maxsize=1)
def _load_all() -> dict:
    if not _QUESTIONS_PATH.exists():
        return {}
    data = json.loads(_QUESTIONS_PATH.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def resolve_module_key(module: str) -> str:
    """接受中文模块名或 finance module id，返回中文 module_key。"""
    raw = str(module or "").strip()
    if not raw:
        raise ValueError("module 不能为空")
    if raw in MODULE_ID_TO_KEY:
        return MODULE_ID_TO_KEY[raw]
    if raw in _load_all():
        return raw
    raise ValueError(f"未知模块: {module}")


def load_module_questions(module_key: str) -> list[dict]:
    data = _load_all()
    block = data.get(module_key) or {}
    return list(block.get("questions") or [])
