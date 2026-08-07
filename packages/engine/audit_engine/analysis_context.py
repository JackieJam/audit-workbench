"""Canonical Analysis Context — 金额分析入口的统一门禁。

所有参与金额合计 / 阈值比较 / 画像 / 跨年 / 规则的路径，都应通过
``assert_amount_analysis_ready`` 或 ``resolve_analysis_frames``，
而不是各自记住检查币种。

``resolve_analysis_frames`` 必须接 **Raw Analysis Frame**（未按币种过滤），
自身完成：validate complete currency population → resolve scope → filter。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Literal

import pandas as pd

from audit_engine.data_columns import collect_analysis_currencies, ensure_analysis_columns


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
    """Fail-closed：多币种未选报告币，或「已知币 + 未知币」混在时阻断。

    必须对 **完整币种总体** 调用（过滤前），否则 selected scope 会静默丢掉未知币行。
    """
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

    # 已知币种与未知币种并存 → 不得把未知行静默并入合计（含 selected scope）
    if currencies and unknown_rows > 0:
        raise ValueError(
            f"金额分析被阻断：存在已知币种 {sorted(currencies)}，"
            f"另有 {unknown_rows} 行金额可用但币种未知/未维护，禁止直接合计。"
        )
    return currencies


def _filter_frames_by_currency(
    year_frames: dict[int, pd.DataFrame],
    selected_currency: str | None,
) -> dict[int, pd.DataFrame]:
    selected = str(selected_currency or "").strip().upper() or None
    if not selected:
        return year_frames
    filtered: dict[int, pd.DataFrame] = {}
    for year, df in year_frames.items():
        if df is None or getattr(df, "empty", True) or "_amount_currency" not in df.columns:
            filtered[year] = df if df is not None else pd.DataFrame()
            continue
        normalized = df["_amount_currency"].fillna("").astype(str).str.strip().str.upper()
        filtered[year] = df.loc[normalized.eq(selected)].copy()
    return filtered


def resolve_analysis_frames(
    year_frames: dict[int, pd.DataFrame],
    *,
    selected_currency: str | None,
    data_version: str = "",
    classification_revision: str = "",
    engine_revision: str = "",
) -> tuple[AnalysisContext, dict[int, pd.DataFrame]]:
    """接 Raw Frame：validate → scope → filter，返回上下文 + 已过滤年度帧。

    调用方不得预先按币种过滤；否则门禁看不到未知币行。
    """
    prepared: dict[int, pd.DataFrame] = {}
    for year, df in year_frames.items():
        if df is None or getattr(df, "empty", True):
            prepared[year] = df if df is not None else pd.DataFrame()
            continue
        prepared[year] = ensure_analysis_columns(df)

    currencies = assert_amount_analysis_ready(
        prepared.values(),
        selected_currency=selected_currency,
    )
    scoped = _filter_frames_by_currency(prepared, selected_currency)

    bases = {
        str(df["_currency_basis"].iat[0])
        for df in prepared.values()
        if df is not None and not df.empty and "_currency_basis" in df.columns
    }
    amount_basis = next(iter(bases), "") if len(bases) == 1 else ("mixed" if bases else "")
    ctx = AnalysisContext(
        data_version=str(data_version or ""),
        years=tuple(sorted(prepared.keys())),
        currency_scope=str(selected_currency or "").strip().upper() or None,
        amount_basis=amount_basis,
        classification_revision=str(classification_revision or ""),
        currencies_present=tuple(sorted(currencies)),
        engine_revision=str(engine_revision or ""),
    )
    return ctx, scoped


def verification_freshness_for_current_sample(
    *,
    verification_context: dict[str, Any] | None,
    sampling_plan: dict[str, Any] | None,
) -> dict[str, Any]:
    """CurrentSampleVerification：fresh iff scope==current_sample AND selection_id 匹配。"""
    context = verification_context if isinstance(verification_context, dict) else {}
    plan = sampling_plan if isinstance(sampling_plan, dict) else {}
    selection_trace = plan.get("selection_trace") or {}
    current_selection = str(selection_trace.get("selection_id") or "").strip()
    verified_selection = str(context.get("selection_id") or "").strip()
    scope = str(context.get("verification_scope") or "").strip()
    has_verification = bool(context.get("verification_run_id") or context.get("verified_at"))
    base = {
        "purpose": "current_sample",
        "current_selection_id": current_selection or None,
        "verification_selection_id": verified_selection or None,
        "verification_scope": scope or None,
        "verification_run_id": context.get("verification_run_id"),
    }
    if not has_verification:
        return {**base, "status": "absent", "fresh": False}
    if scope == "risk_signals":
        return {
            **base,
            "status": "stale",
            "fresh": False,
            "reason": "核验 scope 为 risk_signals，不得冒充当前样本核验",
        }
    if scope and scope != "current_sample":
        return {
            **base,
            "status": "stale",
            "fresh": False,
            "reason": f"核验 scope={scope}，不是 current_sample",
        }
    if not current_selection:
        return {
            **base,
            "status": "stale",
            "fresh": False,
            "reason": "当前无抽样选择，历史核验不得冒充当前样本核验",
        }
    if verified_selection and verified_selection == current_selection:
        return {**base, "status": "fresh", "fresh": True}
    return {
        **base,
        "status": "stale",
        "fresh": False,
        "reason": (
            f"核验绑定 selection {verified_selection or '(空)'}，"
            f"当前抽样为 {current_selection}；不得显示为当前样本已核验"
        ),
    }


def verification_freshness_for_risk_signals(
    *,
    verification_context: dict[str, Any] | None,
    rule_run_context: dict[str, Any] | None = None,
    data_version: str | None = None,
    currency_scope: str | None = None,
    classification_revision: str | None = None,
) -> dict[str, Any]:
    """RiskSignalVerification：fresh iff scope/rule_run/data_version（及币种口径）匹配。"""
    context = verification_context if isinstance(verification_context, dict) else {}
    rule_ctx = rule_run_context if isinstance(rule_run_context, dict) else {}
    scope = str(context.get("verification_scope") or "").strip()
    has_verification = bool(context.get("verification_run_id") or context.get("verified_at"))
    current_rule_run = str(rule_ctx.get("rule_run_id") or "").strip()
    verified_rule_run = str(context.get("rule_run_id") or "").strip()
    current_dv = str(data_version or "").strip()
    verified_dv = str(context.get("data_version") or "").strip()
    base = {
        "purpose": "risk_signals",
        "verification_scope": scope or None,
        "verification_run_id": context.get("verification_run_id"),
        "current_rule_run_id": current_rule_run or None,
        "verification_rule_run_id": verified_rule_run or None,
        "current_data_version": current_dv or None,
        "verification_data_version": verified_dv or None,
    }
    if not has_verification:
        return {**base, "status": "absent", "fresh": False}
    if scope != "risk_signals":
        return {
            **base,
            "status": "stale",
            "fresh": False,
            "reason": f"核验 scope={scope or '(空)'}，不是 risk_signals",
        }
    if current_rule_run and verified_rule_run != current_rule_run:
        return {
            **base,
            "status": "stale",
            "fresh": False,
            "reason": (
                f"核验绑定 rule_run {verified_rule_run or '(空)'}，"
                f"当前为 {current_rule_run}"
            ),
        }
    if current_dv and verified_dv != current_dv:
        return {
            **base,
            "status": "stale",
            "fresh": False,
            "reason": (
                f"核验绑定 data_version {verified_dv or '(空)'}，"
                f"当前为 {current_dv}"
            ),
        }
    verified_currency = context.get("currency_scope")
    if currency_scope is not None and str(verified_currency or "") != str(currency_scope or ""):
        return {
            **base,
            "status": "stale",
            "fresh": False,
            "reason": "币种口径已变化，risk_signals 核验不得继续视为 fresh",
        }
    verified_class = context.get("classification_revision")
    if (
        classification_revision is not None
        and str(verified_class or "") != str(classification_revision or "")
    ):
        return {
            **base,
            "status": "stale",
            "fresh": False,
            "reason": "科目分类口径已变化，risk_signals 核验不得继续视为 fresh",
        }
    return {**base, "status": "fresh", "fresh": True}


def verification_freshness(
    *,
    verification_context: dict[str, Any] | None,
    sampling_plan: dict[str, Any] | None,
    purpose: Literal["current_sample", "risk_signals"] = "current_sample",
    rule_run_context: dict[str, Any] | None = None,
    data_version: str | None = None,
    currency_scope: str | None = None,
    classification_revision: str | None = None,
) -> dict[str, Any]:
    """按 purpose 分发 freshness 语义；默认评估「是否可作为当前样本核验」。"""
    if purpose == "risk_signals":
        return verification_freshness_for_risk_signals(
            verification_context=verification_context,
            rule_run_context=rule_run_context,
            data_version=data_version,
            currency_scope=currency_scope,
            classification_revision=classification_revision,
        )
    return verification_freshness_for_current_sample(
        verification_context=verification_context,
        sampling_plan=sampling_plan,
    )
