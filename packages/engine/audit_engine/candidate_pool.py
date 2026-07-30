"""疑点库数据层 — 保存可疑样本群体，非最终审计结论。"""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections import Counter
from datetime import datetime
from typing import Any

import pandas as pd

from audit_engine.data_columns import (
    VOUCHER_KEY_COLUMN,
    audit_input_quality,
    ensure_analysis_columns,
    ensure_voucher_identity,
)

DEFAULT_STATUS = "候选"
MANUAL_FINAL_STATUS = "人工直入最终样本"
EXCLUDED_STATUS = "排除"


def candidate_id_for(
    *,
    source_view: str,
    selector: dict[str, Any],
    voucher_ids: list[str],
    voucher_keys: list[str] | None = None,
) -> str:
    raw = json.dumps(
        {
            "source_view": source_view,
            "selector": selector,
            "voucher_ids": sorted(set(str(v) for v in voucher_ids)),
            "voucher_keys": sorted(set(str(v) for v in voucher_keys or [])),
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return "cand_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def build_candidate_group(
    *,
    title: str,
    source_module: str,
    source_view: str,
    detail: pd.DataFrame,
    tags: list[str] | None = None,
    reason: str = "",
    selector: dict[str, Any] | None = None,
    status: str = DEFAULT_STATUS,
    created_by: str = "manual",
) -> dict[str, Any]:
    selector = selector or {}
    identity_detail = detail.copy()
    has_year_source = any(
        column in identity_detail.columns
        for column in ("会计年度", "_year", "过账日期")
    )
    if not has_year_source and selector.get("year") not in {None, ""}:
        identity_detail["_year"] = int(selector["year"])
    detail = ensure_voucher_identity(identity_detail)
    voucher_ids = _voucher_ids(detail)
    voucher_keys = _voucher_keys(detail)
    group_id = candidate_id_for(
        source_view=source_view,
        selector=selector,
        voucher_ids=voucher_ids,
        voucher_keys=voucher_keys,
    )
    return {
        "group_id": group_id,
        "title": title,
        "source_module": source_module,
        "source_view": source_view,
        "selector": selector,
        "tags": sorted({str(t).strip() for t in tags or [] if str(t).strip()}),
        "reason": reason.strip(),
        "status": status,
        "created_by": created_by,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "voucher_ids": voucher_ids,
        "voucher_keys": voucher_keys,
        "row_count": int(len(detail)),
        "voucher_count": len(voucher_keys) if voucher_keys else len(voucher_ids),
        "amount_total": _amount_total(detail),
    }


def add_candidate_group(pool: list[dict[str, Any]] | None, group: dict[str, Any]) -> list[dict[str, Any]]:
    groups = list(pool or [])
    existing_idx = next(
        (idx for idx, item in enumerate(groups) if item.get("group_id") == group.get("group_id")),
        None,
    )
    if existing_idx is None:
        groups.append(group)
    else:
        groups[existing_idx] = {**groups[existing_idx], **group}
    return groups


def remove_candidate_group(pool: list[dict[str, Any]] | None, group_id: str) -> list[dict[str, Any]]:
    return [group for group in pool or [] if group.get("group_id") != group_id]


def update_candidate_status(
    pool: list[dict[str, Any]] | None,
    group_id: str,
    status: str,
) -> list[dict[str, Any]]:
    return update_candidate_fields(pool, group_id, status=status)


def update_candidate_fields(
    pool: list[dict[str, Any]] | None,
    group_id: str,
    *,
    status: str | None = None,
    reason: str | None = None,
    tags: list[str] | None = None,
    title: str | None = None,
) -> list[dict[str, Any]]:
    """更新疑点组可变字段；未传入的字段保持原值。"""
    groups = list(pool or [])
    for group in groups:
        if group.get("group_id") != group_id:
            continue
        if status is not None:
            group["status"] = status
        if reason is not None:
            group["reason"] = reason
        if tags is not None:
            group["tags"] = [str(t).strip() for t in tags if str(t).strip()]
        if title is not None and str(title).strip():
            group["title"] = str(title).strip()
        break
    return groups


def active_candidate_voucher_ids(pool: list[dict[str, Any]] | None) -> set[str]:
    statuses = {DEFAULT_STATUS, MANUAL_FINAL_STATUS}
    return {
        str(vid)
        for group in pool or []
        if group.get("status", DEFAULT_STATUS) in statuses
        for vid in group.get("voucher_ids", [])
    }


def active_candidate_voucher_keys(
    pool: list[dict[str, Any]] | None,
    df: pd.DataFrame | None = None,
) -> set[str]:
    """Return stable candidate keys; resolve legacy groups without merging same-number years."""
    groups = [
        group
        for group in pool or []
        if group.get("status", DEFAULT_STATUS) in {DEFAULT_STATUS, MANUAL_FINAL_STATUS}
    ]
    keys = {
        str(key)
        for group in groups
        for key in group.get("voucher_keys", [])
        if str(key).strip()
    }
    legacy_groups = [group for group in groups if not group.get("voucher_keys")]
    if not legacy_groups or df is None or df.empty:
        return keys

    work = ensure_voucher_identity(df)
    for group in legacy_groups:
        voucher_ids = {str(value) for value in group.get("voucher_ids", []) if str(value).strip()}
        matched = work[work["凭证编号"].astype(str).isin(voucher_ids)]
        year = (group.get("selector") or {}).get("year")
        if year is not None:
            year_values = pd.Series(pd.NA, index=matched.index, dtype="Int64")
            for column in ("会计年度", "_year"):
                if column in matched.columns:
                    year_values = year_values.fillna(
                        pd.to_numeric(
                            matched[column], errors="coerce"
                        ).astype("Int64")
                    )
            if "过账日期" in matched.columns:
                year_values = year_values.fillna(
                    pd.to_datetime(
                        matched["过账日期"], errors="coerce"
                    ).dt.year.astype("Int64")
                )
            matched = matched[year_values.eq(int(year))]
        keys.update(matched[VOUCHER_KEY_COLUMN].dropna().astype(str))
    return keys


def pool_stats(pool: list[dict[str, Any]] | None) -> dict[str, int | float]:
    groups = list(pool or [])
    active_vouchers = {
        str(key)
        for group in groups
        if group.get("status", DEFAULT_STATUS) != EXCLUDED_STATUS
        for key in (group.get("voucher_keys") or group.get("voucher_ids", []))
    }
    return {
        "groups": len(groups),
        "active_groups": sum(1 for group in groups if group.get("status", DEFAULT_STATUS) != EXCLUDED_STATUS),
        "active_vouchers": len(active_vouchers),
        "amount_total": sum(float(group.get("amount_total", 0) or 0) for group in groups),
    }


def _voucher_ids(detail: pd.DataFrame) -> list[str]:
    if detail.empty or "凭证编号" not in detail.columns:
        return []
    return sorted({str(v) for v in detail["凭证编号"].dropna().astype(str).tolist()})


def _voucher_keys(detail: pd.DataFrame) -> list[str]:
    if detail.empty:
        return []
    work = ensure_voucher_identity(detail)
    return sorted({
        str(value)
        for value in work[VOUCHER_KEY_COLUMN].dropna().astype(str)
        if str(value).strip()
    })


def _amount_total(detail: pd.DataFrame) -> float:
    priority_cols = [
        "_amount_abs",
        "收入影响",
        "成本发生额",
        "毛利影响",
        "费用发生额",
        "收入S影响",
        "成本H影响",
        "公司代码货币价值",
        "凭证货币价值",
    ]
    for col in priority_cols:
        if col in detail.columns:
            return float(pd.to_numeric(detail[col], errors="coerce").fillna(0).abs().sum())
    return 0.0

# ── 科目分类 ──
# 把基础类别和审计语义对齐：
# - "收入/成本/费用/往来/税金"直接复用 account_classifier 的自动分类结果；
# - "营业外/资产相关"是审计抽样权重需要保留的兜底分类，按编号前缀走。

ACCOUNT_CATEGORY_RULES: dict[str, tuple[tuple[str, ...], str]] = {
    "营业外": (("6301", "6711", "6111"), "营业外收支+投资收益"),
    "资产相关": (("1403", "1405", "1602", "5001", "8142", "8143"), "存货/折旧/生产/制造分摊"),
}


# 把 account_classifier 类别名映射到候选池权重表里的简化标签。
_CLASSIFIER_TO_WEIGHT: dict[str, str] = {
    "收入": "收入",
    "成本": "成本",
    "费用": "费用",
    "研发费用": "费用",
    "财务费用": "费用",
    "税金及附加": "税金",
    "应收": "往来",
    "其他应收": "往来",
    "应付": "往来",
    "应付暂估": "往来",
    "其他应付": "往来",
}

DEFAULT_ACCOUNT_WEIGHTS: dict[str, float] = {
    "收入": 0.25,
    "成本": 0.25,
    "费用": 0.20,
    "往来": 0.15,
    "其他": 0.15,
}


def _classify_account(acct_code: str, acct_name: str = "") -> str:
    """根据科目名称（优先）+ 编号前缀（兜底）归类。

    名称命中 account_classifier 自动分类的 11 大类时，按 _CLASSIFIER_TO_WEIGHT
    映射到候选池权重表的简化标签；
    名称没命中（或为空）时，才回退到 ACCOUNT_CATEGORY_RULES 中的"营业外"/"资产相关"
    这两类（这两类基本只能通过编号识别）。
    """
    from audit_engine.account_classifier import CAT_UNCATEGORIZED, auto_classify

    name_cat = auto_classify(acct_name) if acct_name else CAT_UNCATEGORIZED
    if name_cat != CAT_UNCATEGORIZED:
        mapped = _CLASSIFIER_TO_WEIGHT.get(name_cat)
        if mapped:
            return mapped

    # 兜底：营业外 / 资产相关 按编号前缀识别
    acct = str(acct_code).strip()
    for cat, (prefixes, _) in ACCOUNT_CATEGORY_RULES.items():
        if any(acct.startswith(p) for p in prefixes):
            return cat
    return "其他"


def _build_voucher_amount_map(df: pd.DataFrame) -> dict[str, float]:
    """构建稳定凭证键 → 凭证金额的映射，避免把借贷两边重复相加。"""
    if df.empty or "凭证编号" not in df.columns:
        return {}
    work = ensure_voucher_identity(df)
    if "_amount_abs" not in work.columns or "_dc" not in work.columns:
        try:
            work = ensure_analysis_columns(work)
        except (KeyError, TypeError, ValueError):
            amount_col = next(
                (
                    column
                    for column in ("公司代码货币价值", "凭证货币价值")
                    if column in work.columns
                ),
                None,
            )
            if amount_col is None:
                return {}
            work = work.copy()
            work["_amount_abs"] = pd.to_numeric(
                work[amount_col], errors="coerce"
            ).abs()
            work["_dc"] = work.get(
                "借/贷标识", pd.Series("", index=work.index)
            ).astype(str)

    tmp = work[[VOUCHER_KEY_COLUMN, "_amount_abs", "_dc"]].copy()
    tmp["_amount_abs"] = pd.to_numeric(tmp["_amount_abs"], errors="coerce").fillna(0)
    tmp["_dc"] = tmp["_dc"].astype(str).str.strip()
    by_side = (
        tmp.groupby([VOUCHER_KEY_COLUMN, "_dc"], sort=False)["_amount_abs"]
        .sum()
        .unstack(fill_value=0)
    )
    debit = by_side["S"] if "S" in by_side.columns else pd.Series(0.0, index=by_side.index)
    credit = by_side["H"] if "H" in by_side.columns else pd.Series(0.0, index=by_side.index)
    recognized = debit + credit
    total = tmp.groupby(VOUCHER_KEY_COLUMN, sort=False)["_amount_abs"].sum()
    voucher_amount = pd.concat([debit, credit], axis=1).max(axis=1)
    voucher_amount = voucher_amount.where(recognized.gt(0), total)
    return {str(key): float(value) for key, value in voucher_amount.items()}


def _object_value(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _resolve_legacy_voucher_keys(
    work: pd.DataFrame | None,
    voucher_id: Any,
    *,
    year: Any = None,
) -> list[str]:
    display_id = str(voucher_id or "").strip()
    if not display_id:
        return []
    if work is None or work.empty or "凭证编号" not in work.columns:
        return [display_id]

    matched = work[work["凭证编号"].astype(str).eq(display_id)]
    if year not in {None, ""} and not matched.empty:
        year_series = pd.Series(pd.NA, index=matched.index, dtype="Int64")
        if "会计年度" in matched.columns:
            year_series = pd.to_numeric(
                matched["会计年度"], errors="coerce"
            ).astype("Int64")
        if "_year" in matched.columns:
            derived = pd.to_numeric(matched["_year"], errors="coerce").astype("Int64")
            year_series = year_series.fillna(derived)
        if "过账日期" in matched.columns:
            posting = pd.to_datetime(
                matched["过账日期"], errors="coerce"
            ).dt.year.astype("Int64")
            year_series = year_series.fillna(posting)
        matched = matched[year_series.eq(int(year))]
    if matched.empty:
        return []
    return sorted(set(matched[VOUCHER_KEY_COLUMN].dropna().astype(str)))


def _voucher_selection_metadata(df: pd.DataFrame) -> dict[str, dict[str, Any]]:
    if df.empty:
        return {}
    work = ensure_voucher_identity(df)
    amount_map = _build_voucher_amount_map(work)
    if "_amount_abs" not in work.columns:
        try:
            work = ensure_analysis_columns(work)
        except (KeyError, TypeError, ValueError):
            work = work.copy()
            work["_amount_abs"] = 0.0

    metadata: dict[str, dict[str, Any]] = {}
    for key, rows in work.groupby(VOUCHER_KEY_COLUMN, sort=False):
        key_text = str(key)
        amount_values = pd.to_numeric(
            rows.get("_amount_abs", pd.Series(0.0, index=rows.index)),
            errors="coerce",
        ).fillna(0)
        representative = rows.loc[amount_values.idxmax()] if not rows.empty else pd.Series(dtype=object)
        date = pd.to_datetime(representative.get("过账日期"), errors="coerce")
        month = int(date.month) if pd.notna(date) else None
        if "_month" in rows.columns:
            month_values = pd.to_numeric(rows["_month"], errors="coerce").dropna()
            if not month_values.empty:
                month = int(month_values.iloc[0])
        parties = []
        for column in ("_customer_display", "_vendor_display"):
            if column not in rows.columns:
                continue
            parties.extend(
                value
                for value in rows[column].dropna().astype(str).str.strip().tolist()
                if value and value != "未维护"
            )
        metadata[key_text] = {
            "voucher_id": _voucher_ids(rows)[0] if _voucher_ids(rows) else "",
            "amount_abs": amount_map.get(key_text, 0.0),
            "month": month,
            "account_category": str(representative.get("_acct_category", "") or ""),
            "counterparty": parties[0] if parties else "",
            "amount_basis": str(representative.get("_amount_basis", "") or ""),
            "amount_currency": str(representative.get("_amount_currency", "") or ""),
        }
    return metadata


def rank_rule_result_vouchers(
    rule_results: list[Any],
    df: pd.DataFrame | None = None,
) -> list[dict[str, Any]]:
    """把完整规则命中折叠为可解释的凭证级风险排序，不在此处截断命中。"""
    work = ensure_voucher_identity(df) if df is not None and not df.empty else None
    metadata = _voucher_selection_metadata(work) if work is not None else {}
    records: dict[str, dict[str, Any]] = {}
    first_seen = 0

    for result in rule_results or []:
        rule_name = str(_object_value(result, "rule_name", "") or "")
        for hit in _object_value(result, "hits", []) or []:
            if not bool(_object_value(hit, "sample_eligible", True)):
                continue
            priority = max(1, min(5, int(_object_value(hit, "priority", 1) or 1)))
            year = _object_value(hit, "year")
            rule_type = str(_object_value(hit, "rule_type", rule_name) or rule_name)
            hit_risk_factors = tuple(_object_value(hit, "risk_factors", ()) or ())
            identity_specs = [(
                _object_value(hit, "voucher_key"),
                _object_value(hit, "voucher_id"),
                False,
            )]
            related_ids = list(_object_value(hit, "related_voucher_ids", ()) or ())
            related_keys = list(_object_value(hit, "related_voucher_keys", ()) or ())
            for index, related_id in enumerate(related_ids):
                related_key = related_keys[index] if index < len(related_keys) else None
                identity_specs.append((related_key, related_id, True))

            for raw_key, raw_id, is_related in identity_specs:
                stable_key = str(raw_key or "").strip()
                resolved_keys = (
                    [stable_key]
                    if stable_key
                    else _resolve_legacy_voucher_keys(work, raw_id, year=year)
                )
                for key in resolved_keys:
                    if not key:
                        continue
                    if key not in records:
                        records[key] = {
                            "voucher_key": key,
                            "voucher_id": str(
                                metadata.get(key, {}).get("voucher_id")
                                or raw_id
                                or key
                            ),
                            "priority": priority,
                            "hit_count": 0,
                            "rule_names": [],
                            "rule_types": [],
                            "related": False,
                            "legacy_identity_expanded": not bool(stable_key),
                            "first_seen": first_seen,
                            "hit_risk_factors": [],
                            **metadata.get(key, {}),
                        }
                        first_seen += 1
                    record = records[key]
                    record["priority"] = max(int(record["priority"]), priority)
                    record["hit_count"] = int(record["hit_count"]) + 1
                    if rule_name and rule_name not in record["rule_names"]:
                        record["rule_names"].append(rule_name)
                    if rule_type and rule_type not in record["rule_types"]:
                        record["rule_types"].append(rule_type)
                    record["related"] = bool(record["related"] or is_related)
                    for factor in hit_risk_factors:
                        factor_text = str(factor).strip()
                        if factor_text and factor_text not in record["hit_risk_factors"]:
                            record["hit_risk_factors"].append(factor_text)

    if not records:
        return []

    amounts = pd.Series(
        {key: float(record.get("amount_abs", 0.0) or 0.0) for key, record in records.items()}
    )
    percentiles = amounts.rank(method="average", pct=True) if amounts.gt(0).any() else amounts * 0
    for key, record in records.items():
        priority_component = int(record["priority"]) / 5 * 55
        materiality_percentile = float(percentiles.get(key, 0.0) or 0.0)
        materiality_component = materiality_percentile * 20
        signal_count = len(record["rule_types"])
        multi_rule_component = min(15.0, max(0, signal_count - 1) * 5.0)
        relation_component = 5.0 if record["related"] else 0.0
        rule_text = " ".join(record["rule_types"])
        year_end_component = 5.0 if (
            record.get("month") in {12, 13}
            or any(term in rule_text for term in ("跨年", "年末", "截止"))
        ) else 0.0
        score = min(
            100.0,
            priority_component
            + materiality_component
            + multi_rule_component
            + relation_component
            + year_end_component,
        )
        factors = [
            f"规则优先级{int(record['priority'])}/5={priority_component:.1f}",
            f"命中{signal_count}类风险信号={multi_rule_component:.1f}",
        ]
        if amounts.gt(0).any():
            factors.append(
                f"候选金额分位{materiality_percentile:.1%}={materiality_component:.1f}"
            )
        if relation_component:
            factors.append("作为关联凭证=5.0")
        if year_end_component:
            factors.append("年末/跨年风险=5.0")
        if record["legacy_identity_expanded"]:
            factors.append("旧命中记录按年度/主体拆分恢复")
        factors.extend(record.pop("hit_risk_factors"))
        record["signal_count"] = signal_count
        record["materiality_percentile"] = materiality_percentile
        record["risk_score"] = round(score, 2)
        record["risk_factors"] = factors
        record["primary_signal"] = (
            record["rule_types"][0] if record["rule_types"] else "未分类风险"
        )

    return sorted(
        records.values(),
        key=lambda record: (
            -float(record["risk_score"]),
            -int(record["priority"]),
            -float(record.get("amount_abs", 0.0) or 0.0),
            int(record["first_seen"]),
            str(record["voucher_key"]),
        ),
    )


def _select_ranked_vouchers(
    ranked: list[dict[str, Any]],
    size: int | None,
    *,
    max_same_signal_ratio: float | None = 0.5,
) -> list[dict[str, Any]]:
    ratio = 0.5 if max_same_signal_ratio is None else float(max_same_signal_ratio)
    if not 0 < ratio <= 1:
        raise ValueError("单一风险信号最高占比须在 (0, 1] 之间")
    if not size or size <= 0 or size >= len(ranked):
        return [
            {**record, "selection_reason": "完整风险命中总体"}
            for record in ranked
        ]

    limit = min(size, len(ranked))
    selected: list[dict[str, Any]] = []
    selected_keys: set[str] = set()
    signal_counts: Counter[str] = Counter()
    month_counts: Counter[str] = Counter()
    account_counts: Counter[str] = Counter()
    counterparty_counts: Counter[str] = Counter()

    def add(record: dict[str, Any], reason: str) -> None:
        selected.append({**record, "selection_reason": reason})
        selected_keys.add(str(record["voucher_key"]))
        signal_counts[str(record.get("primary_signal") or "未分类风险")] += 1
        if record.get("month") not in {None, ""}:
            month_counts[str(record["month"])] += 1
        if record.get("account_category"):
            account_counts[str(record["account_category"])] += 1
        if record.get("counterparty"):
            counterparty_counts[str(record["counterparty"])] += 1

    # 先用至多三成名额覆盖不同风险信号，再按风险分数和集中度约束补齐。
    coverage_budget = min(limit, max(1, math.ceil(limit * 0.3)))
    covered_signals: set[str] = set()
    for record in ranked:
        signal = str(record.get("primary_signal") or "未分类风险")
        if signal in covered_signals:
            continue
        add(record, f"覆盖风险信号：{signal}")
        covered_signals.add(signal)
        if len(selected) >= coverage_budget:
            break

    signal_cap = max(1, math.ceil(limit * ratio))
    dimension_cap = max(2, math.ceil(limit * 0.6))
    for record in ranked:
        key = str(record["voucher_key"])
        if key in selected_keys:
            continue
        signal = str(record.get("primary_signal") or "未分类风险")
        month = str(record.get("month")) if record.get("month") not in {None, ""} else ""
        account = str(record.get("account_category") or "")
        party = str(record.get("counterparty") or "")
        if signal_counts[signal] >= signal_cap:
            continue
        if month and month_counts[month] >= dimension_cap:
            continue
        if account and account_counts[account] >= dimension_cap:
            continue
        if party and counterparty_counts[party] >= signal_cap:
            continue
        add(record, "风险评分优先，并应用信号/月度/科目/主体集中度控制")
        if len(selected) >= limit:
            return selected

    # 总体确实集中时不能少抽；第二遍按分数补足，并明确披露放宽覆盖约束。
    for record in ranked:
        key = str(record["voucher_key"])
        if key in selected_keys:
            continue
        add(record, "风险评分补足样本量（总体集中，已放宽覆盖约束）")
        if len(selected) >= limit:
            break
    return selected


def voucher_ids_from_rule_results(
    rule_results: list[Any],
    size: int | None = None,
    *,
    df: pd.DataFrame | None = None,
    max_same_signal_ratio: float | None = 0.5,
) -> list[str]:
    """返回风险定向选中的稳定凭证键；旧命中会按年度/主体拆开恢复。"""
    ranked = rank_rule_result_vouchers(rule_results, df=df)
    selected = _select_ranked_vouchers(
        ranked,
        size,
        max_same_signal_ratio=max_same_signal_ratio,
    )
    return [str(record["voucher_key"]) for record in selected]


def _sample_amount(row: pd.Series) -> float:
    normalized = pd.to_numeric(row.get("_amount_raw"), errors="coerce")
    if pd.notna(normalized):
        return float(normalized)
    for col in ("公司代码货币价值", "凭证货币价值"):
        value = row.get(col)
        amount = pd.to_numeric(value, errors="coerce")
        if pd.notna(amount):
            return float(amount)
    return 0.0


def _resolve_selection_tokens(
    tokens: set[str],
    work: pd.DataFrame,
) -> set[str]:
    available_keys = set(work[VOUCHER_KEY_COLUMN].dropna().astype(str))
    selected_keys = {token for token in tokens if token in available_keys}
    legacy_ids = tokens - selected_keys
    if legacy_ids and "凭证编号" in work.columns:
        selected_keys.update(
            work.loc[
                work["凭证编号"].astype(str).isin(legacy_ids),
                VOUCHER_KEY_COLUMN,
            ].dropna().astype(str)
        )
    return selected_keys


def samples_for_voucher_ids(
    voucher_ids: set[str] | list[str] | tuple[str, ...],
    df: pd.DataFrame,
    pool: list[dict[str, Any]] | None = None,
    rules_config: dict | None = None,
    selection_metadata: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """按稳定凭证键展开样本；旧凭证号会拆成各年度/主体的独立凭证。"""
    from audit_engine.routine_filter import (
        filter_export_voucher_rows,
        pure_routine_voucher_ids,
    )

    if "凭证编号" not in df.columns:
        return []

    tokens = {str(value).strip() for value in voucher_ids if str(value).strip()}
    if not tokens:
        return []
    work = ensure_analysis_columns(ensure_voucher_identity(df))
    selected_voucher_keys = _resolve_selection_tokens(tokens, work)
    if not selected_voucher_keys:
        return []

    # 剔除纯常规机械凭证，避免疑点库全量抽样时灌入社保/结转噪声
    if rules_config:
        selected_rows = work[
            work[VOUCHER_KEY_COLUMN].astype(str).isin(selected_voucher_keys)
        ]
        pure_keys = pure_routine_voucher_ids(
            selected_rows,
            rules_config,
            purpose="candidates",
            stable=True,
        )
        selected_voucher_keys -= pure_keys

    groups = list(pool or [])
    manual_groups = [
        group for group in groups if group.get("status") == MANUAL_FINAL_STATUS
    ]
    manual_keys = active_candidate_voucher_keys(manual_groups, work)

    result_samples = []
    matched_df = work[
        work[VOUCHER_KEY_COLUMN].astype(str).isin(selected_voucher_keys)
    ].copy()
    if matched_df.empty:
        return []

    matched_df["_sort_amount"] = pd.to_numeric(
        matched_df["_amount_abs"], errors="coerce"
    ).fillna(0)
    selection_metadata = selection_metadata or {}

    for voucher_key, grp in matched_df.groupby(VOUCHER_KEY_COLUMN, sort=False):
        key = str(voucher_key)
        display_ids = _voucher_ids(grp)
        display_id = display_ids[0] if display_ids else key
        rows = filter_export_voucher_rows(grp, rules_config, has_rule_hit=False) if rules_config else grp
        if rows.empty:
            continue
        rows = rows.sort_values("_sort_amount", ascending=False) if "_sort_amount" in rows.columns else rows
        is_manual = key in manual_keys
        risk = selection_metadata.get(key, {})
        rule_types = list(risk.get("rule_types") or [])
        factors = list(risk.get("risk_factors") or [])
        for _, row in rows.iterrows():
            amount = _sample_amount(row)
            dc = str(row.get("借/贷标识", ""))
            year = pd.to_numeric(
                row.get("会计年度", row.get("_year")), errors="coerce"
            )
            result_samples.append({
                "_voucher_key": key,
                "凭证唯一键": key,
                "凭证编号": display_id,
                "会计年度": int(year) if pd.notna(year) else None,
                "公司代码": str(row.get("公司代码", "") or ""),
                "过账日期": str(row.get("过账日期", ""))[:10] if pd.notna(row.get("过账日期")) else "",
                "凭证类型": str(row.get("凭证类型", "")),
                "文本": str(row.get("文本", ""))[:60],
                "总账科目": str(row.get("总账科目", "")),
                "科目名称": str(row.get("总账科目：长文本", "")),
                "借方金额": amount if dc == "S" else 0,
                "贷方金额": -amount if dc == "H" else 0,
                "币种": str(row.get("_amount_currency", "") or ""),
                "金额口径": str(row.get("_amount_basis", "") or ""),
                "_month": int(row["_month"]) if pd.notna(row.get("_month")) else None,
                "_acct_category": str(row.get("_acct_category", "") or ""),
                "风险评分": risk.get("risk_score"),
                "主风险信号": str(risk.get("primary_signal") or ""),
                "规则类型": " | ".join(rule_types),
                "风险信号": " | ".join(rule_types),
                "入样理由": str(risk.get("selection_reason") or ""),
                "评分构成": "；".join(str(value) for value in factors),
                "来源模块": _find_group_source(groups, key, display_id),
                "是否为人工直入": is_manual,
                "_source_asset_id": str(row.get("_source_asset_id", "") or ""),
                "_source_file_hash": str(row.get("_source_file_hash", "") or ""),
                "_source_file": str(row.get("_source_file", "") or ""),
                "_source_sheet": str(row.get("_source_sheet", "") or ""),
                "_source_row": (
                    int(row["_source_row"])
                    if pd.notna(row.get("_source_row"))
                    else None
                ),
                "_line_key": str(row.get("_line_key", "") or ""),
            })

    return result_samples


def sample_from_rule_results(
    rule_results: list[Any],
    df: pd.DataFrame,
    size: int | None = None,
    pool: list[dict[str, Any]] | None = None,
    rules_config: dict | None = None,
    max_same_signal_ratio: float | None = 0.5,
) -> list[dict[str, Any]]:
    """直接从已执行的规则结果生成最终样本，避免二次运行规则导致口径漂移。"""
    ranked = rank_rule_result_vouchers(rule_results, df=df)
    selected = _select_ranked_vouchers(
        ranked,
        size,
        max_same_signal_ratio=max_same_signal_ratio,
    )
    metadata = {
        str(record["voucher_key"]): record
        for record in selected
    }
    return samples_for_voucher_ids(
        set(metadata),
        df,
        pool=pool,
        rules_config=rules_config,
        selection_metadata=metadata,
    )


def sample_from_pool(
    pool: list[dict[str, Any]] | None,
    df: pd.DataFrame,
    method: str = "by_rule",
    size: int | None = None,
    rules_config: dict | None = None,
    seed: int = 42,
    account_weights: dict[str, float] | None = None,
    stratify_by: str | None = None,
    stratify_mode: str = "proportional",
    max_same_signal_ratio: float | None = 0.5,
) -> list[dict[str, Any]]:
    """
    从候选池中按指定方式和规则抽取最终样本。

    Parameters
    ----------
    pool : 候选群体列表
    df : 全量序时账 DataFrame
    method : 抽样方式，可选 "by_rule" | "random" | "all"
        - "by_rule": 按规则引擎命中结果抽取（需提供 rules_config）
        - "random": 随机抽样
        - "all": 取候选池中所有凭证（不抽样）
    size : 样本量上限（凭证数），仅 method="random" 时生效
    rules_config : 规则配置，仅 method="by_rule" 时需要
    seed : 随机种子

    Returns
    -------
    list[dict] : 每个元素包含凭证编号、过账日期、金额、来源模块、风险等级等
    """
    from audit_engine.rule_engine import run_all_rules

    groups = list(pool or [])
    if not groups and method != "by_rule":
        return []
    work = ensure_analysis_columns(ensure_voucher_identity(df))
    selection_metadata: dict[str, dict[str, Any]] = {}

    # 收集候选池中所有活动凭证
    candidate_voucher_ids = active_candidate_voucher_keys(groups, work)

    if method == "all":
        # 全量：直接取所有候选凭证
        selected_voucher_ids = candidate_voucher_ids
    elif method == "random":
        # 随机抽样
        if not candidate_voucher_ids:
            return []
        ids_list = sorted(candidate_voucher_ids)
        rng = random.Random(seed)
        if size and size < len(ids_list):
            selected_voucher_ids = set(rng.sample(ids_list, size))
        else:
            selected_voucher_ids = ids_list
    elif method == "by_rule":
        # 规则引擎筛选
        if not rules_config:
            rules_config = {}
        if candidate_voucher_ids:
            results = run_all_rules(
                work, rules_config,
                candidate_voucher_ids=candidate_voucher_ids,
            )
        else:
            results = run_all_rules(work, rules_config)

        ranked = rank_rule_result_vouchers(results, df=work)
        selected_records = _select_ranked_vouchers(
            ranked,
            size,
            max_same_signal_ratio=max_same_signal_ratio,
        )
        selection_metadata = {
            str(record["voucher_key"]): record
            for record in selected_records
        }
        selected_voucher_ids = set(selection_metadata)
    elif method == "by_account_weight":
        # ── 科目权重抽样 ──
        quality = audit_input_quality(work)
        if any(
            issue.get("code") == "mixed_document_currencies"
            for issue in quality.get("issues", [])
        ):
            raise ValueError("多种凭证币且无本位币金额，不能进行金额加权抽样")
        weights = account_weights or DEFAULT_ACCOUNT_WEIGHTS
        # 归一化权重
        total_w = sum(weights.values()) or 1.0
        weights = {k: v / total_w for k, v in weights.items()}

        amt_map = _build_voucher_amount_map(work)
        candidate_list = sorted(candidate_voucher_ids)

        # 按科目分类凭证
        voucher_acct_map: dict[str, str] = {}
        if "总账科目" in work.columns:
            name_col = next(
                (c for c in ("总账科目:长文本", "总账科目:短文本", "总账科目：长文本", "总账科目：短文本")
                 if c in work.columns),
                None,
            )
            cols = [VOUCHER_KEY_COLUMN, "总账科目"] + ([name_col] if name_col else [])
            voucher_meta = (
                work[cols]
                .drop_duplicates(subset=VOUCHER_KEY_COLUMN)
                .assign(_vid=lambda d: d[VOUCHER_KEY_COLUMN].astype(str))
            )
            if name_col:
                voucher_acct_map = {
                    vid: _classify_account(str(acct), str(name) if pd.notna(name) else "")
                    for vid, acct, name in zip(
                        voucher_meta["_vid"], voucher_meta["总账科目"], voucher_meta[name_col],
                        strict=False,
                    )
                    if vid in candidate_voucher_ids
                }
            else:
                voucher_acct_map = {
                    vid: _classify_account(str(acct))
                    for vid, acct in zip(voucher_meta["_vid"], voucher_meta["总账科目"], strict=False)
                    if vid in candidate_voucher_ids
                }

        # 按科目分组
        by_cat: dict[str, list[str]] = {}
        for vid in candidate_list:
            cat = voucher_acct_map.get(vid, "其他")
            by_cat.setdefault(cat, []).append(vid)

        # 每类内按金额降序
        for cat in by_cat:
            by_cat[cat].sort(key=lambda v: amt_map.get(v, 0), reverse=True)

        # 按权重分配名额
        selected_voucher_ids: set[str] = set()
        if size:
            for cat, weight in weights.items():
                cat_vids = by_cat.get(cat, [])
                quota = max(1, int(round(size * weight)))
                selected_voucher_ids.update(cat_vids[:quota])

            # 如果还不足 size，从剩余中补齐（按金额优先）
            if len(selected_voucher_ids) < size:
                remaining = [v for v in candidate_list if v not in selected_voucher_ids]
                remaining.sort(key=lambda v: amt_map.get(v, 0), reverse=True)
                selected_voucher_ids.update(remaining[:size - len(selected_voucher_ids)])

            # 截断到 size
            if len(selected_voucher_ids) > size:
                sorted_selected = sorted(selected_voucher_ids, key=lambda v: amt_map.get(v, 0), reverse=True)
                selected_voucher_ids = set(sorted_selected[:size])
        else:
            selected_voucher_ids = candidate_voucher_ids

    elif method == "monetary_unit":
        # ── 货币单元抽样 (MUS) ──
        quality = audit_input_quality(work)
        if any(
            issue.get("code") == "mixed_document_currencies"
            for issue in quality.get("issues", [])
        ):
            raise ValueError("多种凭证币且无本位币金额，不能进行货币单元抽样")
        amt_map = _build_voucher_amount_map(work)
        candidate_list = [v for v in sorted(candidate_voucher_ids) if amt_map.get(v, 0) > 0]
        if not candidate_list:
            selected_voucher_ids = candidate_voucher_ids
        elif not size or size <= 0:
            selected_voucher_ids = set(candidate_list)
        else:
            rng = random.Random(seed)
            # 构建累积金额
            cumulative = 0.0
            intervals: list[tuple[str, float, float]] = []  # (vid, start, end)
            for vid in candidate_list:
                amt = amt_map.get(vid, 0)
                if amt <= 0:
                    continue
                intervals.append((vid, cumulative, cumulative + amt))
                cumulative += amt

            total_amount = cumulative
            if total_amount <= 0:
                selected_voucher_ids = set(candidate_list[:size]) if candidate_list else set()
            else:
                interval = total_amount / size
                start = rng.uniform(0, interval)
                selected_voucher_ids = set()
                for i in range(size):
                    point = start + i * interval
                    if point >= total_amount:
                        point -= total_amount  # wrap around
                    # 二分查找
                    for vid, lo, hi in intervals:
                        if lo <= point < hi:
                            selected_voucher_ids.add(vid)
                            break

    elif method == "stratified":
        # ── 分层抽样 ──
        strata_attr = {
            "voucher_type": "凭证类型",
            "account_category": "_acct_category",
            "month": "_month",
        }.get(stratify_by or "", stratify_by or "凭证类型")
        candidate_list = sorted(candidate_voucher_ids)
        if not size:
            selected_voucher_ids = set(candidate_list)
        else:
            # 构建凭证属性映射
            vid_attr: dict[str, str] = {}
            if strata_attr in work.columns:
                attr_meta = (
                    work[[VOUCHER_KEY_COLUMN, strata_attr]]
                    .drop_duplicates(subset=VOUCHER_KEY_COLUMN)
                    .assign(_vid=lambda d: d[VOUCHER_KEY_COLUMN].astype(str))
                )
                vid_attr = {
                    vid: str(attr)[:30]
                    for vid, attr in zip(attr_meta["_vid"], attr_meta[strata_attr], strict=False)
                    if vid in candidate_voucher_ids
                }
            elif strata_attr == "科目大类":
                if "总账科目" in work.columns:
                    name_col = next(
                        (c for c in ("总账科目:长文本", "总账科目:短文本", "总账科目：长文本", "总账科目：短文本")
                         if c in work.columns),
                        None,
                    )
                    cols = [VOUCHER_KEY_COLUMN, "总账科目"] + ([name_col] if name_col else [])
                    acct_meta = (
                        work[cols]
                        .drop_duplicates(subset=VOUCHER_KEY_COLUMN)
                        .assign(_vid=lambda d: d[VOUCHER_KEY_COLUMN].astype(str))
                    )
                    if name_col:
                        vid_attr = {
                            vid: _classify_account(str(acct), str(name) if pd.notna(name) else "")
                            for vid, acct, name in zip(
                                acct_meta["_vid"], acct_meta["总账科目"], acct_meta[name_col],
                                strict=False,
                            )
                            if vid in candidate_voucher_ids
                        }
                    else:
                        vid_attr = {
                            vid: _classify_account(str(acct))
                            for vid, acct in zip(acct_meta["_vid"], acct_meta["总账科目"], strict=False)
                            if vid in candidate_voucher_ids
                        }
            elif strata_attr == "月份":
                if "过账日期" in work.columns:
                    month_meta = (
                        work[[VOUCHER_KEY_COLUMN, "过账日期"]]
                        .drop_duplicates(subset=VOUCHER_KEY_COLUMN)
                        .assign(_vid=lambda d: d[VOUCHER_KEY_COLUMN].astype(str))
                    )
                    vid_attr = {}
                    for vid, raw_date in zip(month_meta["_vid"], month_meta["过账日期"], strict=False):
                        if vid not in candidate_voucher_ids:
                            continue
                        try:
                            vid_attr[vid] = f"{pd.to_datetime(raw_date).month}月"
                        except Exception:
                            vid_attr[vid] = "未知"

            # 按属性分组
            by_stratum: dict[str, list[str]] = {}
            for vid in candidate_list:
                attr = vid_attr.get(vid, "未分类")
                by_stratum.setdefault(attr, []).append(vid)

            rng = random.Random(seed)
            selected_voucher_ids = set()
            strata = list(by_stratum.keys())

            if stratify_mode == "equal":
                # 每层等量
                per_stratum = max(1, size // max(len(strata), 1))
                for st in strata:
                    vids = by_stratum[st]
                    n = min(per_stratum, len(vids))
                    selected_voucher_ids.update(rng.sample(vids, n))
            else:
                # 按层规模比例分配
                total_candidates = len(candidate_list)
                for st in strata:
                    vids = by_stratum[st]
                    quota = max(1, int(round(size * len(vids) / total_candidates)))
                    n = min(quota, len(vids))
                    selected_voucher_ids.update(rng.sample(vids, n))

            # 截断到 size
            if len(selected_voucher_ids) > size:
                selected_voucher_ids = set(
                    rng.sample(sorted(selected_voucher_ids), size)
                )

    else:
        raise ValueError(f"不支持的抽样方式: {method}")

    if not selection_metadata:
        reason_labels = {
            "all": "疑点库特定项目全查",
            "random": f"随机抽样（固定种子 {seed}）",
            "by_account_weight": "按科目权重与凭证金额选择",
            "monetary_unit": f"货币单元抽样（固定种子 {seed}）",
            "stratified": f"分层抽样（{stratify_by or '凭证类型'}）",
        }
        selection_metadata = {
            str(key): {"selection_reason": reason_labels.get(method, method)}
            for key in selected_voucher_ids
        }
    return samples_for_voucher_ids(
        selected_voucher_ids,
        work,
        pool=groups,
        rules_config=rules_config,
        selection_metadata=selection_metadata,
    )


def _find_group_source(
    groups: list[dict],
    voucher_key: str,
    voucher_id: str = "",
) -> str:
    """查找凭证所属的候选群体来源模块。"""
    for group in groups:
        if (
            voucher_key in {str(value) for value in group.get("voucher_keys", [])}
            or voucher_id in {str(value) for value in group.get("voucher_ids", [])}
        ):
            return group.get("source_module", "未知")
    return "其他"


def sample_to_table(samples: list[dict[str, Any]]) -> pd.DataFrame:
    """将抽样结果列表转换为 DataFrame 用于展示。"""
    if not samples:
        return pd.DataFrame()
    return pd.DataFrame(samples)


def candidate_pool_summary_text(pool: list[dict[str, Any]] | None, limit: int = 20) -> str:
    groups = [group for group in pool or [] if group.get("status", DEFAULT_STATUS) != EXCLUDED_STATUS]
    if not groups:
        return "当前尚未建立疑点库，规则仍按全量序时账执行。"
    lines = [
        f"当前疑点库共有 {len(groups)} 个群体，涉及 {len(active_candidate_voucher_ids(groups))} 个凭证。",
    ]
    for group in groups[:limit]:
        lines.append(
            "- "
            f"[{group.get('status', DEFAULT_STATUS)}] {group.get('title', '')}；"
            f"来源={group.get('source_module', '')}/{group.get('source_view', '')}；"
            f"凭证={group.get('voucher_count', 0)}；"
            f"标签={ '、'.join(group.get('tags', [])) or '未标注' }；"
            f"理由={group.get('reason', '') or '未填写'}"
        )
    if len(groups) > limit:
        lines.append(f"...其余 {len(groups) - limit} 个群体未展开。")
    return "\n".join(lines)
