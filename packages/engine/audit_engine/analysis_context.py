"""Canonical Analysis Context — 金额分析入口的统一门禁。

所有参与金额合计 / 阈值比较 / 画像 / 跨年 / 规则的路径，都应通过
``assert_amount_analysis_ready`` 或 ``resolve_analysis_frames``，
而不是各自记住检查币种。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import pandas as pd

from audit_engine.data_columns import collect_analysis_currencies


@dataclass(frozen=True)
class AnalysisContext:
    """冻结本次金额分析口径（不含 DataFrame 本体）。"""

    data_version: str
    years: tuple[int, ...]
    currency_scope: str | None
    amount_basis: str
    classification_revision: str
    currencies_present: tuple[str, ...]
    engine_revision: str = ""


def assert_amount_analysis_ready(
    frames: Iterable[pd.DataFrame | None],
    *,
    selected_currency: str | None,
    allow_empty: bool = True,
) -> set[str]:
    """Fail-closed：多币种未选报告币，或「已知币 + 未知币」混在时阻断。"""
    frame_list = [f for f in frames if f is not None and not getattr(f, "empty", True)]
    if not frame_list:
        if allow_empty:
            return set()
        raise ValueError("无可用于金额分析的序时账数据")

    currencies = collect_analysis_currencies(frame_list)
    selected = str(selected_currency or "").strip().upper() or None
    if len(currencies) > 1 and not selected:
        raise ValueError(
            f"金额分析被阻断：检测到多种分析币种 {sorted(currencies)}。"
            "请先选择单一报告币种（不做汇率折算）。"
        )

    unknown_rows = 0
    for df in frame_list:
        if "_amount_available" not in df.columns or "_amount_currency" not in df.columns:
            continue
        available = df["_amount_available"].fillna(False).astype(bool)
        if not available.any():
            continue
        currency = (
            df.loc[available, "_amount_currency"]
            .astype("string")
            .fillna("")
            .str.strip()
        )
        unknown = currency.eq("") | currency.str.upper().isin(
            {"未维护", "NAN", "NONE", "(空)"}
        )
        unknown_rows += int(unknown.sum())

    # 已知币种与未知币种并存 → 不得把未知行静默并入合计
    if currencies and unknown_rows > 0:
        raise ValueError(
            f"金额分析被阻断：存在已知币种 {sorted(currencies)}，"
            f"另有 {unknown_rows} 行金额可用但币种未知/未维护，禁止直接合计。"
        )
    return currencies


def resolve_analysis_frames(
    year_frames: dict[int, pd.DataFrame],
    *,
    selected_currency: str | None,
    data_version: str = "",
    classification_revision: str = "",
    engine_revision: str = "",
) -> tuple[AnalysisContext, dict[int, pd.DataFrame]]:
    """校验币种门禁并返回上下文 +（已由调用方过滤的）年度帧。"""
    currencies = assert_amount_analysis_ready(
        year_frames.values(),
        selected_currency=selected_currency,
    )
    bases = {
        str(df["_currency_basis"].iat[0])
        for df in year_frames.values()
        if df is not None and not df.empty and "_currency_basis" in df.columns
    }
    amount_basis = next(iter(bases), "") if len(bases) == 1 else ("mixed" if bases else "")
    ctx = AnalysisContext(
        data_version=str(data_version or ""),
        years=tuple(sorted(year_frames.keys())),
        currency_scope=str(selected_currency or "").strip().upper() or None,
        amount_basis=amount_basis,
        classification_revision=str(classification_revision or ""),
        currencies_present=tuple(sorted(currencies)),
        engine_revision=str(engine_revision or ""),
    )
    return ctx, year_frames


def verification_freshness(
    *,
    verification_context: dict[str, Any] | None,
    sampling_plan: dict[str, Any] | None,
) -> dict[str, Any]:
    """判断当前 LLM 核验是否仍绑定当前抽样 selection。"""
    context = verification_context if isinstance(verification_context, dict) else {}
    plan = sampling_plan if isinstance(sampling_plan, dict) else {}
    selection_trace = plan.get("selection_trace") or {}
    current_selection = str(selection_trace.get("selection_id") or "").strip()
    verified_selection = str(context.get("selection_id") or "").strip()
    has_verification = bool(context.get("verification_run_id") or context.get("verified_at"))
    if not has_verification:
        return {
            "status": "absent",
            "fresh": False,
            "current_selection_id": current_selection or None,
            "verification_selection_id": verified_selection or None,
            "verification_run_id": context.get("verification_run_id"),
        }
    if not current_selection:
        return {
            "status": "stale",
            "fresh": False,
            "reason": "当前无抽样选择，历史核验不得冒充当前样本核验",
            "current_selection_id": None,
            "verification_selection_id": verified_selection or None,
            "verification_run_id": context.get("verification_run_id"),
        }
    if verified_selection and verified_selection == current_selection:
        return {
            "status": "fresh",
            "fresh": True,
            "current_selection_id": current_selection,
            "verification_selection_id": verified_selection,
            "verification_run_id": context.get("verification_run_id"),
        }
    return {
        "status": "stale",
        "fresh": False,
        "reason": (
            f"核验绑定 selection {verified_selection or '(空)'}，"
            f"当前抽样为 {current_selection}；不得显示为当前样本已核验"
        ),
        "current_selection_id": current_selection,
        "verification_selection_id": verified_selection or None,
        "verification_run_id": context.get("verification_run_id"),
    }
