"""列名学习库 — ~/.audit_tool/column_aliases.json"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from audit_engine.runtime import storage_root


def _norm_col(text: str) -> str:
    return "".join(ch for ch in str(text).lower() if ch.isalnum())


def _aliases_path() -> Path:
    return storage_root() / "column_aliases.json"


def _load() -> dict[str, Any]:
    path = _aliases_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data: dict[str, Any]) -> None:
    path = _aliases_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def record_column_mappings(std_to_source: dict[str, str]) -> int:
    recorded = 0
    today = str(date.today())
    data = _load()
    for std_name, source in std_to_source.items():
        std_name = str(std_name).strip()
        source = str(source or "").strip()
        if not std_name or not source:
            continue
        norm = _norm_col(source)
        if not norm or norm == _norm_col(std_name):
            continue
        entry = data.get(norm)
        if not isinstance(entry, dict):
            entry = {"example": source, "candidates": {}}
            data[norm] = entry
        entry["example"] = source
        candidates = entry.setdefault("candidates", {})
        cand = candidates.get(std_name)
        if not isinstance(cand, dict):
            cand = {"count": 0, "last_used": today}
            candidates[std_name] = cand
        cand["count"] = int(cand.get("count", 0)) + 1
        cand["last_used"] = today
        recorded += 1
    if recorded:
        _save(data)
    return recorded


def learned_column_aliases() -> dict[str, str]:
    data = _load()
    out: dict[str, str] = {}
    for norm, entry in data.items():
        if not isinstance(entry, dict):
            continue
        candidates = entry.get("candidates", {})
        if not isinstance(candidates, dict) or not candidates:
            continue
        best = max(
            candidates.items(),
            key=lambda kv: (
                int(kv[1].get("count", 0)) if isinstance(kv[1], dict) else 0,
                kv[1].get("last_used", "") if isinstance(kv[1], dict) else "",
            ),
        )
        out[norm] = best[0]
    return out
