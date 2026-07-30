"""Versioned audit sampling-plan and selection-trace helpers."""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from audit_engine.data_columns import VOUCHER_KEY_COLUMN, ensure_analysis_columns

POPULATION_SCOPES = {"full_population", "risk_signals"}
STRATEGY_METHOD = {
    "risk_directed": "by_rule",
    "random": "random",
    "monetary_unit": "monetary_unit",
    "stratified": "stratified",
    "unpredictable": "random",
}
LEGACY_METHODS = {"by_rule", "random", "all", "by_account_weight", "monetary_unit", "stratified"}


@dataclass(frozen=True)
class CoverageConstraints:
    min_per_month: int | None = None
    min_per_account_category: int | None = None
    max_same_risk_signal_ratio: float | None = None


@dataclass(frozen=True)
class SamplingPlan:
    plan_name: str
    population_scope: str
    strategy: str
    method: str
    size: int
    seed: int
    years: tuple[int, ...] = ()
    coverage_constraints: CoverageConstraints = field(default_factory=CoverageConstraints)
    stratify_by: str | None = None
    stratify_mode: str | None = None
    unpredictable: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["years"] = list(self.years)
        return data


def normalize_sampling_plan(
    raw: dict[str, Any] | None,
    *,
    default_size: int = 50,
    default_seed: int = 42,
) -> SamplingPlan:
    payload = dict(raw or {})
    method = str(payload.get("method") or "").strip()
    strategy = str(payload.get("strategy") or "").strip()
    if not strategy:
        strategy = next((key for key, value in STRATEGY_METHOD.items() if value == method), "risk_directed")
    if strategy not in STRATEGY_METHOD:
        raise ValueError(f"不支持的抽样策略: {strategy}")
    expected_method = STRATEGY_METHOD[strategy]
    if method and method not in LEGACY_METHODS:
        raise ValueError(f"不支持的抽样方法: {method}")
    if not method:
        method = expected_method
    if strategy != "unpredictable" and method != expected_method:
        raise ValueError(f"策略 {strategy} 与方法 {method} 不一致")

    population_scope = str(payload.get("population_scope") or "risk_signals")
    if population_scope not in POPULATION_SCOPES:
        raise ValueError(f"不支持的总体范围: {population_scope}")
    size = int(payload.get("size") or default_size)
    if not 1 <= size <= 500:
        raise ValueError("样本量须在 1-500 之间")
    seed = int(payload.get("seed") if payload.get("seed") is not None else default_seed)

    raw_constraints = payload.get("coverage_constraints") or {}
    constraints = CoverageConstraints(
        min_per_month=_optional_positive_int(raw_constraints.get("min_per_month")),
        min_per_account_category=_optional_positive_int(
            raw_constraints.get("min_per_account_category")
        ),
        max_same_risk_signal_ratio=_optional_ratio(
            raw_constraints.get("max_same_risk_signal_ratio")
        ),
    )
    years = tuple(sorted({int(year) for year in payload.get("years") or []}))
    stratify_by = payload.get("stratify_by")
    if stratify_by not in {None, "account_category", "month", "voucher_type"}:
        raise ValueError(f"不支持的分层字段: {stratify_by}")
    stratify_mode = payload.get("stratify_mode")
    if stratify_mode not in {None, "proportional", "equal"}:
        raise ValueError(f"不支持的分层方式: {stratify_mode}")
    if strategy == "stratified":
        stratify_by = stratify_by or "voucher_type"
        stratify_mode = stratify_mode or "proportional"

    return SamplingPlan(
        plan_name=str(payload.get("plan_name") or "未命名抽样计划").strip() or "未命名抽样计划",
        population_scope=population_scope,
        strategy=strategy,
        method=method,
        size=size,
        seed=seed,
        years=years,
        coverage_constraints=constraints,
        stratify_by=stratify_by,
        stratify_mode=stratify_mode,
        unpredictable=bool(payload.get("unpredictable") or strategy == "unpredictable"),
    )


def rule_revision(rules: dict[str, Any]) -> str:
    raw = json.dumps(rules, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def build_population_snapshot(
    df: pd.DataFrame,
    *,
    project_id: str,
    plan: SamplingPlan,
    data_version: str,
    classification_revision: str,
    currency_scope: dict[str, Any] | None,
    rules: dict[str, Any],
    analysis_scope_revision: str = "",
    ingest_run_id: str = "",
    engine_revision: str = "",
    rule_run_id: str = "",
    rule_result_hash: str = "",
) -> dict[str, Any]:
    work = df
    if plan.years and "_year" in work.columns:
        work = work[work["_year"].isin(plan.years)]
    key_col = VOUCHER_KEY_COLUMN if VOUCHER_KEY_COLUMN in work.columns else "凭证编号"
    voucher_count = int(work[key_col].astype(str).nunique()) if key_col in work.columns else 0
    amount_col = "_amount_abs" if "_amount_abs" in work.columns else None
    amount_abs = _population_voucher_amount(work) if amount_col else None
    revision = rule_revision(rules)
    membership_keys = (
        sorted({
            str(value)
            for value in work[key_col].dropna().astype(str)
            if str(value).strip()
        })
        if key_col in work.columns
        else []
    )
    membership_revision = hashlib.sha256(
        json.dumps(membership_keys, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]
    scope_payload = {
        "project_id": project_id,
        "population_scope": plan.population_scope,
        "data_version": data_version,
        "ingest_run_id": ingest_run_id,
        "classification_revision": classification_revision,
        "analysis_scope_revision": analysis_scope_revision,
        "currency_scope": currency_scope or {},
        "rule_version": revision,
        "rule_run_id": rule_run_id,
        "rule_result_hash": rule_result_hash,
        "engine_revision": engine_revision,
        "population_membership_revision": membership_revision,
        "years": list(plan.years),
    }
    population_id = "pop_" + hashlib.sha256(
        json.dumps(scope_payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]
    return {
        "population_id": population_id,
        **scope_payload,
        "row_count": int(len(work)),
        "voucher_count": voucher_count,
        "amount_absolute": amount_abs,
        "dimension_counts": _population_dimension_counts(work),
        "amount_basis": (
            "凭证级金额（借贷两侧分别汇总后取较大值）"
            if amount_col
            else "未取得统一金额"
        ),
    }


def build_selection_trace(
    plan: SamplingPlan,
    population_snapshot: dict[str, Any],
    samples: list[dict[str, Any]],
) -> dict[str, Any]:
    voucher_rows = _voucher_rows(samples)
    coverage_checks = _coverage_checks(
        plan.coverage_constraints,
        voucher_rows,
        population_snapshot.get("dimension_counts") or {},
    )
    selected_keys = sorted(voucher_rows)
    identity_payload = {
        "plan": plan.to_dict(),
        "population_id": population_snapshot.get("population_id"),
        "selected_keys": selected_keys,
    }
    selected_keys_digest = hashlib.sha256(
        json.dumps(selected_keys, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    selection_id = "sel_" + hashlib.sha256(
        json.dumps(identity_payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]
    population_count = int(population_snapshot.get("voucher_count") or 0)
    selected_count = len(selected_keys)
    has_non_probability_constraint = any((
        plan.coverage_constraints.min_per_month is not None,
        plan.coverage_constraints.min_per_account_category is not None,
        plan.coverage_constraints.max_same_risk_signal_ratio is not None,
    ))
    probability_available = (
        plan.population_scope == "full_population"
        and plan.strategy in {"random", "unpredictable"}
        and population_count > 0
        and not has_non_probability_constraint
    )
    statistical = probability_available and all(
        check.get("status") == "met" for check in coverage_checks
    )
    projection_boundary = ""
    if plan.population_scope != "full_population":
        projection_boundary = "风险信号总体不是完整审计总体，不得据此推断总体错报率"
    elif plan.strategy == "monetary_unit":
        projection_boundary = "当前 MUS 仅用于选样，尚未保存货币单元重复命中与投影参数"
    elif plan.strategy == "stratified":
        projection_boundary = "当前分层抽样尚未逐层保存入样概率，不得进行统计投影"
    elif has_non_probability_constraint:
        projection_boundary = "覆盖或风险集中度约束改变了等概率选择，当前未计算调整后的入样概率"
    elif not probability_available:
        projection_boundary = "当前策略未保存可核验的入样概率"
    elif not statistical:
        projection_boundary = "抽样计划的覆盖约束未全部满足"
    return {
        "selection_id": selection_id,
        "strategy": plan.strategy,
        "seed": plan.seed,
        "generated_at": datetime.now(UTC).isoformat(),
        "reproducible": True,
        "inclusion_probability_available": probability_available,
        "uniform_inclusion_probability": (
            min(1.0, selected_count / population_count)
            if probability_available
            else None
        ),
        "statistical_projection_allowed": statistical,
        "projection_boundary": projection_boundary,
        "coverage_checks": coverage_checks,
        "selected_voucher_count": len(selected_keys),
        "selected_voucher_keys": selected_keys,
        "selected_voucher_keys_digest": selected_keys_digest,
    }


def apply_minimum_coverage(
    df: pd.DataFrame,
    selected_keys: list[str],
    *,
    size: int,
    constraints: CoverageConstraints,
    seed: int,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Deterministically make month/category minimums part of selection.

    The function never silently grows the requested sample.  If the requested
    size or source population cannot satisfy a constraint, the plan is rejected
    before any result is persisted.
    """
    if df.empty:
        return [], []
    work = ensure_analysis_columns(df)
    representatives = (
        work.sort_values(VOUCHER_KEY_COLUMN, kind="stable")
        .drop_duplicates(VOUCHER_KEY_COLUMN)
        .copy()
    )
    population_keys = [
        str(value)
        for value in representatives[VOUCHER_KEY_COLUMN].dropna().astype(str)
        if str(value).strip()
    ]
    population_set = set(population_keys)
    ordered_base = list(dict.fromkeys(
        key for key in (str(value) for value in selected_keys) if key in population_set
    ))
    if (
        constraints.min_per_month is None
        and constraints.min_per_account_category is None
    ):
        return ordered_base[:size], []
    disclosures: list[dict[str, Any]] = []
    mandatory: set[str] = set()
    requirements: dict[tuple[str, str], int] = {}
    memberships: dict[str, set[tuple[str, str]]] = {
        key: set() for key in population_keys
    }

    specs = (
        ("min_per_month", constraints.min_per_month, "_month", "月份"),
        (
            "min_per_account_category",
            constraints.min_per_account_category,
            "_acct_category",
            "科目类别",
        ),
    )
    for constraint_id, target, column, label in specs:
        if target is None:
            continue
        values = representatives[column].map(_dimension_text)
        valid_values = sorted({value for value in values if value})
        for value in valid_values:
            keys = sorted(
                representatives.loc[values.eq(value), VOUCHER_KEY_COLUMN]
                .dropna()
                .astype(str)
            )
            if len(keys) < target:
                raise ValueError(
                    f"覆盖约束不可满足：{label}“{value}”总体仅 {len(keys)} 个凭证，"
                    f"低于要求的 {target} 个"
                )
            requirement = (constraint_id, value)
            requirements[requirement] = target
            for key in keys:
                memberships.setdefault(key, set()).add(requirement)
        disclosures.append({
            "constraint": constraint_id,
            "target_per_value": target,
            "population_values": valid_values,
        })

    counts = {requirement: 0 for requirement in requirements}
    base_rank = {key: index for index, key in enumerate(ordered_base)}
    while any(counts[item] < target for item, target in requirements.items()):
        candidates: list[tuple[int, int, str, str]] = []
        for key in population_keys:
            if key in mandatory:
                continue
            gain = sum(
                1
                for requirement in memberships.get(key, set())
                if counts[requirement] < requirements[requirement]
            )
            if gain <= 0:
                continue
            tie = hashlib.sha256(f"{seed}|coverage|{key}".encode()).hexdigest()
            candidates.append((
                -gain,
                base_rank.get(key, len(base_rank) + 1),
                tie,
                key,
            ))
        if not candidates:
            raise ValueError("覆盖约束不可满足：无法找到可补入的总体凭证")
        chosen = min(candidates)[3]
        mandatory.add(chosen)
        for requirement in memberships.get(chosen, set()):
            counts[requirement] += 1

    if len(mandatory) > size:
        raise ValueError(
            f"覆盖约束至少需要 {len(mandatory)} 个凭证，超过计划样本量 {size}；"
            "请提高样本量或降低覆盖要求"
        )

    selected: list[str] = sorted(mandatory)
    for key in ordered_base:
        if key not in mandatory:
            selected.append(key)
        if len(selected) >= size:
            break
    if len(selected) < min(size, len(population_keys)):
        remaining = [key for key in population_keys if key not in set(selected)]
        chooser = random.Random(f"{seed}|coverage-fill")
        chooser.shuffle(remaining)
        selected.extend(remaining[: size - len(selected)])
    return selected[:size], disclosures


def _population_voucher_amount(df: pd.DataFrame) -> float:
    """Aggregate at voucher grain so balanced debit/credit lines count once."""
    if (
        df.empty
        or VOUCHER_KEY_COLUMN not in df.columns
        or "_amount_abs" not in df.columns
    ):
        return 0.0
    columns = [VOUCHER_KEY_COLUMN, "_amount_abs"]
    if "_dc" in df.columns:
        columns.append("_dc")
    work = df[columns].copy()
    work["_amount_abs"] = pd.to_numeric(
        work["_amount_abs"], errors="coerce"
    ).fillna(0)
    if "_dc" not in work.columns:
        return float(
            work.groupby(VOUCHER_KEY_COLUMN, sort=False)["_amount_abs"].sum().sum()
        )
    work["_dc"] = work["_dc"].astype(str).str.strip()
    by_side = (
        work.groupby([VOUCHER_KEY_COLUMN, "_dc"], sort=False)["_amount_abs"]
        .sum()
        .unstack(fill_value=0)
    )
    debit = (
        by_side["S"]
        if "S" in by_side.columns
        else pd.Series(0.0, index=by_side.index)
    )
    credit = (
        by_side["H"]
        if "H" in by_side.columns
        else pd.Series(0.0, index=by_side.index)
    )
    recognized = debit + credit
    fallback = work.groupby(VOUCHER_KEY_COLUMN, sort=False)["_amount_abs"].sum()
    voucher_amount = pd.concat([debit, credit], axis=1).max(axis=1)
    voucher_amount = voucher_amount.where(recognized.gt(0), fallback)
    return float(voucher_amount.sum())


def _voucher_rows(samples: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in samples:
        key = str(row.get("_voucher_key") or row.get("凭证键") or row.get("凭证编号") or "").strip()
        if key:
            groups.setdefault(key, []).append(row)
    return groups


def _coverage_checks(
    constraints: CoverageConstraints,
    voucher_rows: dict[str, list[dict[str, Any]]],
    population_dimensions: dict[str, dict[str, int]],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    representative = [rows[0] for rows in voucher_rows.values() if rows]

    if constraints.min_per_month is not None:
        values = _dimension_counts(representative, "_month", "月份")
        expected = set((population_dimensions.get("month") or {}).keys())
        missing = sorted(expected - set(values))
        actual = min((values.get(value, 0) for value in expected), default=None)
        checks.append(_check(
            "min_per_month",
            "总体内每个月份的最少样本数",
            constraints.min_per_month,
            actual,
            actual is not None and actual >= constraints.min_per_month and not missing,
            details={"missing_values": missing},
        ))
    if constraints.min_per_account_category is not None:
        values = _dimension_counts(representative, "_acct_category", "来源模块")
        expected = set((population_dimensions.get("account_category") or {}).keys())
        missing = sorted(expected - set(values))
        actual = min((values.get(value, 0) for value in expected), default=None)
        checks.append(_check(
            "min_per_account_category",
            "总体内每个科目类别的最少样本数",
            constraints.min_per_account_category,
            actual,
            actual is not None and actual >= constraints.min_per_account_category and not missing,
            details={"missing_values": missing},
        ))
    if constraints.max_same_risk_signal_ratio is not None:
        values = _dimension_counts(
            representative,
            "主风险信号",
            "规则类型",
            "风险信号",
        )
        actual = max(values.values()) / len(representative) if values and representative else None
        checks.append(_check(
            "max_same_risk_signal_ratio",
            "单一风险信号最高占比",
            constraints.max_same_risk_signal_ratio,
            actual,
            actual is not None and actual <= constraints.max_same_risk_signal_ratio,
        ))
    return checks


def _dimension_counts(rows: list[dict[str, Any]], *keys: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = next((row.get(key) for key in keys if row.get(key) not in {None, ""}), None)
        if value is not None:
            counts[str(value)] = counts.get(str(value), 0) + 1
    return counts


def _check(
    check_id: str,
    label: str,
    target: Any,
    actual: Any,
    met: bool,
    *,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = {
        "id": check_id,
        "label": label,
        "target": target,
        "actual": actual,
        "status": "met" if met else ("unknown" if actual is None else "unmet"),
    }
    if details:
        result["details"] = details
    return result


def _population_dimension_counts(df: pd.DataFrame) -> dict[str, dict[str, int]]:
    if df.empty or VOUCHER_KEY_COLUMN not in df.columns:
        return {"month": {}, "account_category": {}}
    representatives = df.drop_duplicates(VOUCHER_KEY_COLUMN)
    return {
        "month": _series_counts(representatives.get("_month")),
        "account_category": _series_counts(representatives.get("_acct_category")),
    }


def _series_counts(values: pd.Series | None) -> dict[str, int]:
    if values is None:
        return {}
    counts: dict[str, int] = {}
    for value in values:
        text = _dimension_text(value)
        if text:
            counts[text] = counts.get(text, 0) + 1
    return dict(sorted(counts.items()))


def _dimension_text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _optional_positive_int(value: Any) -> int | None:
    if value in {None, ""}:
        return None
    result = int(value)
    if result < 1:
        raise ValueError("覆盖数量约束须大于 0")
    return result


def _optional_ratio(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    result = float(value)
    if not 0 < result <= 1:
        raise ValueError("覆盖比例约束须在 (0, 1] 之间")
    return result
