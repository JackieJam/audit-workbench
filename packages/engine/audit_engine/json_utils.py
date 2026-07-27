"""统一 JSON 解析 — 处理 LLM 返回的 markdown 代码块。"""

from __future__ import annotations

import json
import re
from typing import Any


def extract_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        start = next((i + 1 for i, line in enumerate(lines) if line.startswith("```")), 1)
        end = next((i for i, line in enumerate(lines[start:], start) if line.startswith("```")), len(lines))
        text = "\n".join(lines[start:end]).strip()
    return text


def parse_json_dict(text: str) -> dict[str, Any]:
    text = extract_json(text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise ValueError(f"无法解析 JSON：{text[:200]}") from None
        data = json.loads(match.group())
    if not isinstance(data, dict):
        raise ValueError("LLM 返回不是 JSON 对象")
    return data


def parse_json_list(text: str) -> list[Any]:
    text = extract_json(text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            raise ValueError(f"无法解析 JSON 数组：{text[:200]}") from None
        data = json.loads(match.group())
    if not isinstance(data, list):
        raise ValueError("LLM 返回不是 JSON 数组")
    return data
