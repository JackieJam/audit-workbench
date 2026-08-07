"""分析流水线 — 画像 / 跨年 / 规则执行 / 抽样。"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pandas as pd

from audit_engine.candidate_pool import (
    DEFAULT_STATUS,
    MANUAL_FINAL_STATUS,
    active_candidate_voucher_keys,
    rank_rule_result_vouchers,
    sample_from_pool,
    sample_from_rule_results,
    samples_for_voucher_ids,
)
from audit_engine.cross_year import CrossYearFinding, run_cross_year_analysis
from audit_engine.data_columns import (
    VOUCHER_KEY_COLUMN,
    audit_input_quality,
    ensure_analysis_columns,
)
from audit_engine.profiler import build_financial_summary, build_profile
from audit_engine.reporter import generate_report_bytes
from audit_engine.rule_engine import RuleHit, RuleResult, run_all_rules
from audit_engine.rules_config import merge_rules_config
from audit_engine.runtime import workbench_version
from audit_engine.sampling_plan import (
    apply_minimum_coverage,
    build_population_snapshot,
    build_selection_trace,
    normalize_sampling_plan,
    rule_revision,
)
from audit_engine.store import ProjectStore


def _category_overrides(state: dict[str, Any]) -> dict[str, str]:
    raw = state.get("account_category_overrides") or {}
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    return {}


def _finding_to_dict(f: CrossYearFinding) -> dict[str, Any]:
    return {
        "category": f.category,
        "description": f.description,
        "years_involved": f.years_involved,
        "voucher_ids": f.voucher_ids,
        "voucher_keys": f.voucher_keys,
        "amount": round(float(f.amount), 2),
        "severity": f.severity,
        "evidence": f.evidence,
    }


def _rule_results_to_dict(results: list[RuleResult]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rr in results:
        out.append({
            "rule_name": rr.rule_name,
            "count": rr.count,
            "hits": [
                {
                    "voucher_id": h.voucher_id,
                    "rule_type": h.rule_type,
                    "evidence": h.evidence,
                    "line_indices": list(h.line_indices),
                    "priority": h.priority,
                    "year": h.year,
                    "group_id": h.group_id,
                    "related_voucher_ids": list(h.related_voucher_ids),
                    "relation_evidence": h.relation_evidence,
                    "voucher_key": h.voucher_key,
                    "related_voucher_keys": list(h.related_voucher_keys),
                    "sample_eligible": h.sample_eligible,
                    "risk_score": h.risk_score,
                    "risk_factors": list(h.risk_factors),
                }
                for h in rr.hits
            ],
        })
    return out


def _rule_results_from_dict(data: list[dict[str, Any]]) -> list[RuleResult]:
    results: list[RuleResult] = []
    for block in data or []:
        hits = [
            RuleHit(
                voucher_id=str(h["voucher_id"]),
                rule_type=str(h.get("rule_type", "")),
                evidence=str(h.get("evidence", "")),
                line_indices=tuple(h.get("line_indices", ())),
                priority=int(h.get("priority", 1)),
                year=h.get("year"),
                group_id=h.get("group_id"),
                related_voucher_ids=tuple(h.get("related_voucher_ids", ())),
                relation_evidence=str(h.get("relation_evidence", "")),
                voucher_key=h.get("voucher_key"),
                related_voucher_keys=tuple(h.get("related_voucher_keys", ())),
                sample_eligible=bool(h.get("sample_eligible", True)),
                risk_score=float(h["risk_score"]) if h.get("risk_score") is not None else None,
                risk_factors=tuple(h.get("risk_factors", ())),
            )
            for h in block.get("hits", [])
        ]
        results.append(RuleResult(rule_name=str(block.get("rule_name", "")), hits=hits))
    return results


def _cross_year_findings_from_state(state: dict[str, Any]) -> list[CrossYearFinding]:
    return [
        CrossYearFinding(
            category=str(f.get("category", "")),
            description=str(f.get("description", "")),
            years_involved=list(f.get("years_involved", [])),
            voucher_ids=[str(v) for v in f.get("voucher_ids", [])],
            voucher_keys=[str(v) for v in f.get("voucher_keys", [])],
            amount=float(f.get("amount", 0)),
            severity=str(f.get("severity", "中")),
            evidence=dict(f.get("evidence", {})),
        )
        for f in state.get("cross_year_findings") or []
    ]


def _candidate_signal_result(
    pool: list[dict[str, Any]],
    work: pd.DataFrame,
) -> RuleResult | None:
    """Expose user-marked candidates as explicit, explainable risk signals."""
    if work.empty:
        return None
    display = (
        work[[VOUCHER_KEY_COLUMN, "凭证编号"]]
        .drop_duplicates(VOUCHER_KEY_COLUMN)
        .set_index(VOUCHER_KEY_COLUMN)["凭证编号"]
        .astype(str)
        .to_dict()
    )
    year_values = (
        work[[VOUCHER_KEY_COLUMN, "_year"]]
        .drop_duplicates(VOUCHER_KEY_COLUMN)
        .set_index(VOUCHER_KEY_COLUMN)["_year"]
        .to_dict()
        if "_year" in work.columns
        else {}
    )
    hits: list[RuleHit] = []
    seen: set[tuple[str, str]] = set()
    for group in pool:
        if group.get("status", DEFAULT_STATUS) not in {DEFAULT_STATUS, MANUAL_FINAL_STATUS}:
            continue
        keys = active_candidate_voucher_keys([group], work)
        for key in sorted(keys):
            identity = (str(group.get("group_id") or ""), key)
            if identity in seen:
                continue
            seen.add(identity)
            raw_year = pd.to_numeric(year_values.get(key), errors="coerce")
            is_manual = group.get("status") == MANUAL_FINAL_STATUS
            hits.append(RuleHit(
                voucher_id=str(display.get(key) or key),
                voucher_key=key,
                rule_type="人工直入" if is_manual else "用户标记疑点",
                evidence=str(group.get("reason") or group.get("title") or "用户标记疑点"),
                line_indices=(),
                priority=5 if is_manual else 3,
                year=int(raw_year) if pd.notna(raw_year) else None,
                group_id=str(group.get("group_id") or "") or None,
                risk_factors=(
                    "人工明确要求纳入最终样本"
                    if is_manual
                    else "用户从财务画像或序时账标记为疑点",
                ),
            ))
    return RuleResult(rule_name="用户疑点标记", hits=hits) if hits else None


def _sampling_metadata(samples: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    metadata: dict[str, dict[str, Any]] = {}
    for row in samples:
        key = str(row.get(VOUCHER_KEY_COLUMN) or row.get("凭证唯一键") or "").strip()
        if not key or key in metadata:
            continue
        metadata[key] = {
            "risk_score": row.get("风险评分"),
            "primary_signal": str(row.get("主风险信号") or ""),
            "rule_types": [
                value.strip()
                for value in str(row.get("风险信号") or row.get("规则类型") or "").split("|")
                if value.strip()
            ],
            "risk_factors": [
                value.strip()
                for value in str(row.get("评分构成") or "").split("；")
                if value.strip()
            ],
            "selection_reason": str(row.get("入样理由") or ""),
        }
    return metadata


def _analysis_token(state: dict[str, Any]) -> tuple[str, str, str, str, str]:
    classification_revision = str(state.get("classification_revision") or "")
    if not classification_revision:
        classification_revision = rule_revision({
            "decisions": state.get("account_classification_decisions") or {},
            "overrides": state.get("account_category_overrides") or {},
        })
    return (
        str(state.get("data_version") or ""),
        classification_revision,
        str(state.get("analysis_scope_revision") or ""),
        str(state.get("analysis_currency_scope") or ""),
        rule_revision(merge_rules_config(None, state.get("rules_config"))),
    )


def _assert_analysis_token(
    state: dict[str, Any],
    expected: tuple[str, str, str, str, str],
) -> None:
    if _analysis_token(state) != expected:
        raise ValueError("计算期间项目数据或分析口径已变化，本次结果未写入；请重新执行")


# 改这些键会影响跨年检测结果，保存规则时需清空已缓存 findings。
_CROSS_YEAR_RULE_KEYS = frozenset({
    "cross_year_accrual",
    "cross_year_revenue",
    "cross_year_detection",
})


def _cross_year_config_changed(before: dict[str, Any], after: dict[str, Any]) -> bool:
    for key in _CROSS_YEAR_RULE_KEYS:
        if before.get(key) != after.get(key):
            return True
    return False


def _invalidate_rule_derived_state(state: dict[str, Any], *, clear_cross_year: bool) -> dict[str, Any]:
    """规则变更后清掉依赖规则配置的派生结果，避免界面参数与缓存结果不一致。"""
    state["rule_results"] = []
    state.pop("rule_run_context", None)
    state["samples"] = []
    state.pop("sampling_plan", None)
    state["llm_judgments"] = {}
    if clear_cross_year:
        state["cross_year_findings"] = []
    return state


class AnalysisPipeline:
    def __init__(self, store: ProjectStore) -> None:
        self._store = store

    def load_rules(self, project_id: str) -> dict:
        state = self._store.load_state(project_id)
        return merge_rules_config(None, state.get("rules_config"))

    def save_rules(self, project_id: str, rules: dict) -> dict:
        merged = merge_rules_config(None, rules)

        def update(state: dict[str, Any]) -> dict[str, Any]:
            from audit_engine.audit_case import mark_case_evidence_stale_in_state

            previous = merge_rules_config(None, state.get("rules_config"))
            state["rules_config"] = rules
            _invalidate_rule_derived_state(
                state,
                clear_cross_year=_cross_year_config_changed(previous, merged),
            )
            if rule_revision(previous) != rule_revision(merged):
                mark_case_evidence_stale_in_state(
                    state,
                    reason="抽样规则配置已变更；原规则命中证据仍保留，但需按新规则重新执行。",
                    trigger="rules_changed",
                    source_types={"rule_hit"},
                )
            return state

        self._store.update_state(project_id, update)
        return merged

    def build_profiles(self, project_id: str) -> dict[int, dict]:
        from audit_engine.analysis_context import assert_amount_analysis_ready
        from audit_engine.runtime import workbench_version

        manifest = self._store.load_manifest(project_id)
        state = self._store.load_state(project_id)
        expected_token = _analysis_token(state)
        year_map = {
            year: self._store.get_analysis_work_df(project_id, year)
            for year in manifest.years
        }
        assert_amount_analysis_ready(
            year_map.values(),
            selected_currency=self._store.current_analysis_currency(project_id),
        )
        profiles: dict[int, dict] = {}
        financials: dict[int, dict] = {}
        for year, work in year_map.items():
            if work.empty:
                continue
            profiles[year] = build_profile(work, year)
            financials[year] = build_financial_summary(work, year)
        def update(latest: dict[str, Any]) -> dict[str, Any]:
            _assert_analysis_token(latest, expected_token)
            latest["profiles"] = profiles
            latest["financials"] = financials
            latest["profile_context"] = {
                "data_version": self._store.current_data_version(project_id),
                "currency_scope": self._store.current_analysis_currency(project_id),
                "engine_revision": workbench_version(),
            }
            return latest

        self._store.update_state(project_id, update)
        return profiles

    def run_cross_year(self, project_id: str) -> list[dict]:
        from audit_engine.analysis_context import assert_amount_analysis_ready

        manifest = self._store.load_manifest(project_id)
        state = self._store.load_state(project_id)
        expected_token = _analysis_token(state)
        overrides = _category_overrides(state)
        rules = self.load_rules(project_id)
        year_map = {
            year: self._store.get_analysis_work_df(project_id, year)
            for year in manifest.years
        }
        assert_amount_analysis_ready(
            year_map.values(),
            selected_currency=self._store.current_analysis_currency(project_id),
        )
        findings = run_cross_year_analysis(year_map, rules, category_overrides=overrides)
        serialized = [_finding_to_dict(f) for f in findings]
        def update(latest: dict[str, Any]) -> dict[str, Any]:
            _assert_analysis_token(latest, expected_token)
            latest["cross_year_findings"] = serialized
            return latest

        self._store.update_state(project_id, update)
        return serialized

    def run_rules(self, project_id: str) -> list[dict]:
        manifest = self._store.load_manifest(project_id)
        state = self._store.load_state(project_id)
        expected_token = _analysis_token(state)
        rules = self.load_rules(project_id)
        frames = []
        for year in manifest.years:
            df = self._store.get_analysis_work_df(project_id, year)
            if not df.empty:
                if "_year" not in df.columns:
                    df = df.copy()
                    df["_year"] = year
                frames.append(df)
        if not frames:
            return []
        unified = ensure_analysis_columns(pd.concat(frames, ignore_index=True))
        cross_findings = _cross_year_findings_from_state(state)
        quality = audit_input_quality(unified)
        results = run_all_rules(
            unified,
            rules,
            cross_year_findings=cross_findings if cross_findings else None,
        )
        serialized = _rule_results_to_dict(results)
        result_payload = json.dumps(
            serialized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        result_hash = hashlib.sha256(result_payload.encode()).hexdigest()
        engine_revision = workbench_version()
        run_identity = {
            "data_version": self._store.current_data_version(project_id),
            "ingest_run_id": self._store.current_ingest_run_id(project_id),
            "classification_revision": self._store.current_classification_revision(
                project_id
            ),
            "analysis_scope_revision": str(
                state.get("analysis_scope_revision") or ""
            ),
            "currency_scope": self._store.current_analysis_currency(project_id),
            "rule_revision": rule_revision(rules),
            "engine_revision": engine_revision,
            "result_hash": result_hash,
        }
        rule_run_id = "rr_" + hashlib.sha256(
            json.dumps(
                run_identity,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode()
            ).hexdigest()[:20]
        result_artifact = self._store.persist_rule_run_results(
            project_id,
            rule_run_id,
            serialized,
            expected_hash=result_hash,
        )
        context = {
            "rule_run_id": rule_run_id,
            "scope": "full_population",
            **run_identity,
            "result_artifact": result_artifact,
            "rules_snapshot": rules,
            "years": list(manifest.years),
            "row_count": int(len(unified)),
            "voucher_count": int(unified[VOUCHER_KEY_COLUMN].astype(str).nunique()),
            "input_quality": quality,
            "complete_hit_count": sum(result.count for result in results),
            "candidate_pool_restricted": False,
        }

        def update(latest: dict[str, Any]) -> dict[str, Any]:
            _assert_analysis_token(latest, expected_token)
            latest["rule_results"] = [
                {
                    "rule_name": block.get("rule_name", ""),
                    "count": int(block.get("count") or len(block.get("hits") or [])),
                    "hits": [],
                }
                for block in serialized
            ]
            latest["rule_run_context"] = context
            history = list(latest.get("rule_run_history") or [])
            last_run = history[-1] if history and isinstance(history[-1], dict) else {}
            if last_run.get("rule_run_id") != rule_run_id:
                history.append(context)
            latest["rule_run_history"] = history[-100:]
            return latest

        self._store.update_state(project_id, update)
        return serialized

    def extract_samples(
        self,
        project_id: str,
        *,
        method: str = "by_rule",
        size: int | None = None,
        seed: int = 42,
        plan: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        manifest = self._store.load_manifest(project_id)
        state = self._store.load_state(project_id)
        expected_token = _analysis_token(state)
        rules = self.load_rules(project_id)
        plan_payload = dict(plan or {})
        if "method" not in plan_payload:
            plan_payload["method"] = method
        if "size" not in plan_payload and size is not None:
            plan_payload["size"] = size
        if "seed" not in plan_payload:
            plan_payload["seed"] = seed
        requested_plan = normalize_sampling_plan(
            plan_payload,
            default_size=min(500, int(rules.get("max_sample_size", 50))),
            default_seed=seed,
        )
        unknown_years = sorted(set(requested_plan.years) - set(manifest.years))
        if unknown_years:
            raise ValueError(f"抽样计划包含项目中不存在的年度：{unknown_years}")
        if (
            requested_plan.coverage_constraints.max_same_risk_signal_ratio
            is not None
            and requested_plan.strategy != "risk_directed"
        ):
            raise ValueError(
                "“单一风险信号最高占比”仅适用于风险定向抽样；"
                "随机、MUS、分层和不可预测抽样请留空"
            )

        pool = self._store.load_candidate_pool(project_id)
        frames = []
        for year in manifest.years:
            df = self._store.get_analysis_work_df(project_id, year)
            if not df.empty:
                if "_year" not in df.columns:
                    df = df.copy()
                    df["_year"] = year
                frames.append(df)
        unified = (
            ensure_analysis_columns(pd.concat(frames, ignore_index=True))
            if frames
            else pd.DataFrame()
        )
        if unified.empty:
            raise ValueError("项目无可用于抽样的序时账数据")
        scoped = unified
        if requested_plan.years:
            scoped = unified[
                pd.to_numeric(unified["_year"], errors="coerce").isin(requested_plan.years)
            ].copy()
        if scoped.empty:
            raise ValueError("抽样计划选定年度没有可用序时账数据")

        needs_risk_signals = (
            requested_plan.population_scope == "risk_signals"
            or requested_plan.strategy == "risk_directed"
        )
        serialized_results = self._store.load_rule_run_results(
            project_id,
            state=state,
        )
        if needs_risk_signals and not serialized_results:
            serialized_results = self.run_rules(project_id)
            state = self._store.load_state(project_id)
        rule_results = _rule_results_from_dict(serialized_results)
        candidate_result = _candidate_signal_result(pool, scoped)
        risk_results = list(rule_results)
        if candidate_result is not None:
            risk_results.append(candidate_result)

        available_keys = set(scoped[VOUCHER_KEY_COLUMN].dropna().astype(str))
        ranked_signals = rank_rule_result_vouchers(risk_results, df=scoped)
        risk_keys = {
            str(item.get("voucher_key") or "")
            for item in ranked_signals
            if str(item.get("voucher_key") or "") in available_keys
        }
        if requested_plan.population_scope == "risk_signals":
            population_df = scoped[
                scoped[VOUCHER_KEY_COLUMN].astype(str).isin(risk_keys)
            ].copy()
        else:
            population_df = scoped

        population_keys = sorted(
            set(population_df[VOUCHER_KEY_COLUMN].dropna().astype(str))
        )
        synthetic_pool = [{
            "group_id": "sampling_population",
            "title": "抽样计划总体",
            "source_module": "抽样计划",
            "source_view": requested_plan.population_scope,
            "status": DEFAULT_STATUS,
            "voucher_ids": [],
            "voucher_keys": population_keys,
        }]
        max_same_signal_ratio = (
            requested_plan.coverage_constraints.max_same_risk_signal_ratio
        )
        if requested_plan.strategy == "risk_directed":
            base_samples = (
                sample_from_rule_results(
                    risk_results,
                    scoped,
                    size=requested_plan.size,
                    pool=pool,
                    rules_config=None,
                    max_same_signal_ratio=max_same_signal_ratio,
                )
                if risk_results and risk_keys
                else []
            )
        else:
            base_samples = sample_from_pool(
                synthetic_pool,
                population_df,
                method=requested_plan.method,
                size=requested_plan.size,
                rules_config=None,
                seed=requested_plan.seed,
                stratify_by=requested_plan.stratify_by,
                stratify_mode=requested_plan.stratify_mode or "proportional",
                max_same_signal_ratio=max_same_signal_ratio,
            )

        base_keys = list(dict.fromkeys(
            str(row.get(VOUCHER_KEY_COLUMN) or row.get("凭证唯一键") or "")
            for row in base_samples
            if str(row.get(VOUCHER_KEY_COLUMN) or row.get("凭证唯一键") or "").strip()
        ))
        selected_keys, coverage_selection = apply_minimum_coverage(
            population_df,
            base_keys,
            size=requested_plan.size,
            constraints=requested_plan.coverage_constraints,
            seed=requested_plan.seed,
        )
        metadata = _sampling_metadata(base_samples)
        ranked_metadata = {
            str(item.get("voucher_key") or ""): item
            for item in ranked_signals
            if str(item.get("voucher_key") or "")
        }
        for key in selected_keys:
            if key not in metadata:
                ranked = ranked_metadata.get(key) or {}
                metadata[key] = {
                    "risk_score": ranked.get("risk_score"),
                    "primary_signal": str(ranked.get("primary_signal") or ""),
                    "selection_reason": "为满足抽样计划覆盖约束补入",
                    "risk_factors": [
                        *list(ranked.get("risk_factors") or []),
                        "抽样计划覆盖约束",
                    ],
                    "rule_types": list(ranked.get("rule_types") or []),
                }
        samples = samples_for_voucher_ids(
            selected_keys,
            scoped,
            pool=pool + synthetic_pool,
            rules_config=None,
            selection_metadata=metadata,
        )

        state_for_scope = self._store.load_state(project_id)
        currency_values = sorted({
            str(value).strip().upper()
            for value in population_df.get(
                "_amount_currency", pd.Series(dtype="string")
            ).dropna()
            if str(value).strip() and str(value).strip() != "未维护"
        })
        currency_scope = {
            "selected_currency": self._store.current_analysis_currency(project_id),
            "currencies": currency_values,
            "basis": (
                str(population_df["_currency_basis"].iat[0])
                if "_currency_basis" in population_df.columns and not population_df.empty
                else ""
            ),
        }
        snapshot = build_population_snapshot(
            population_df,
            project_id=project_id,
            plan=requested_plan,
            data_version=self._store.current_data_version(project_id),
            classification_revision=self._store.current_classification_revision(project_id),
            analysis_scope_revision=str(
                state_for_scope.get("analysis_scope_revision") or ""
            ),
            ingest_run_id=self._store.current_ingest_run_id(project_id),
            engine_revision=workbench_version(),
            rule_run_id=str(
                (state_for_scope.get("rule_run_context") or {}).get("rule_run_id")
                or ""
            ),
            rule_result_hash=str(
                (state_for_scope.get("rule_run_context") or {}).get("result_hash")
                or ""
            ),
            currency_scope=currency_scope,
            rules=rules,
        )
        trace = build_selection_trace(requested_plan, snapshot, samples)
        trace["coverage_selection"] = coverage_selection
        unmet_constraints = [
            check
            for check in trace["coverage_checks"]
            if check.get("status") != "met"
        ]
        if unmet_constraints:
            labels = "、".join(
                str(check.get("label") or check.get("id") or "未知约束")
                for check in unmet_constraints
            )
            raise ValueError(
                f"抽样计划未满足明确设置的覆盖约束：{labels}；"
                "本次结果未保存，请提高样本量、放宽约束或调整总体"
            )
        uniform_probability = trace.get("uniform_inclusion_probability")
        for row in samples:
            row["抽样计划"] = requested_plan.plan_name
            row["抽样总体ID"] = snapshot["population_id"]
            row["选择轨迹ID"] = trace["selection_id"]
            row["抽样策略"] = requested_plan.strategy
            row["入样概率"] = uniform_probability
            row["允许统计推断"] = bool(trace["statistical_projection_allowed"])

        voucher_count = len({
            str(sample.get(VOUCHER_KEY_COLUMN) or sample.get("凭证唯一键") or "")
            for sample in samples
            if sample.get(VOUCHER_KEY_COLUMN) or sample.get("凭证唯一键")
        })
        plan_record = {
            "requested_plan": requested_plan.to_dict(),
            "population_snapshot": snapshot,
            "selection_trace": trace,
        }

        def update(latest: dict[str, Any]) -> dict[str, Any]:
            _assert_analysis_token(latest, expected_token)
            previous_plan = latest.get("sampling_plan") or {}
            previous_selection = str(
                ((previous_plan.get("selection_trace") or {}).get("selection_id")) or ""
            )
            new_selection = str(trace.get("selection_id") or "")
            latest["samples"] = samples
            latest["sampling_plan"] = plan_record
            history = list(latest.get("sampling_plan_history") or [])
            history.append(plan_record)
            latest["sampling_plan_history"] = history[-100:]
            # Sample A → Sample B：旧 LLM 核验不得冒充当前样本核验
            if previous_selection and previous_selection != new_selection:
                latest["llm_judgments"] = {}
                stale_ctx = dict(latest.get("verification_run_context") or {})
                if stale_ctx:
                    stale_ctx["status"] = "stale"
                    stale_ctx["stale_reason"] = (
                        f"抽样已从 {previous_selection} 变更为 {new_selection}"
                    )
                    stale_history = list(latest.get("verification_run_history") or [])
                    stale_history.append(stale_ctx)
                    latest["verification_run_history"] = stale_history[-50:]
                latest.pop("verification_run_context", None)
                latest.pop("llm_verify_boundary", None)
            elif not previous_selection and latest.get("llm_judgments"):
                # 无旧 selection 记录但仍有 judgments → 也清空，避免孤儿核验
                latest["llm_judgments"] = {}
                latest.pop("verification_run_context", None)
                latest.pop("llm_verify_boundary", None)
            return latest

        self._store.update_state(project_id, update)
        return {
            "method": requested_plan.method,
            "sample_rows": len(samples),
            "voucher_count": voucher_count,
            "samples": samples[:500],
            **plan_record,
        }

    def export_excel(self, project_id: str) -> tuple[bytes, dict]:
        from audit_engine.analysis_context import verification_freshness
        from audit_engine.llm_verifier import judgments_from_state

        manifest = self._store.load_manifest(project_id)
        state = self._store.load_state(project_id)
        rules = self.load_rules(project_id)
        frames = []
        for year in manifest.years:
            df = self._store.get_analysis_work_df(project_id, year)
            if not df.empty:
                if "_year" not in df.columns:
                    df = df.copy()
                    df["_year"] = year
                frames.append(df)
        unified = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        rule_results = _rule_results_from_dict(
            self._store.load_rule_run_results(project_id, state=state)
        )
        if not rule_results:
            rule_results = run_all_rules(unified, rules)
        manual_final = [
            g for g in self._store.load_candidate_pool(project_id)
            if g.get("status") == MANUAL_FINAL_STATUS
        ]
        freshness = verification_freshness(
            verification_context=state.get("verification_run_context"),
            sampling_plan=state.get("sampling_plan"),
        )
        # stale 核验不得写入当前正式底稿
        llm_judgments = (
            judgments_from_state(state.get("llm_judgments"))
            if freshness.get("fresh")
            else {}
        )
        data, stats = generate_report_bytes(
            unified,
            rule_results,
            llm_judgments=llm_judgments,
            max_sample_size=int(rules.get("max_sample_size", 50)),
            manual_final_samples=manual_final,
            explicit_samples=list(state.get("samples") or []) if "samples" in state else None,
            rules_config=rules,
        )
        stats["verification_freshness"] = freshness
        stats["selection_id"] = freshness.get("current_selection_id")
        stats["verification_run_id"] = (
            freshness.get("verification_run_id") if freshness.get("fresh") else None
        )
        return data, stats

    def verify_with_llm(
        self,
        project_id: str,
        *,
        api_key: str,
        model: str,
        base_url: str,
        max_verify: int = 50,
        redaction: str | None = None,
        verification_scope: str = "current_sample",
        boundary_hash: str | None = None,
    ) -> dict[str, Any]:
        import uuid
        from datetime import UTC, datetime

        from audit_engine.llm_verifier import (
            LLM_BOUNDARY_POLICY_VERSION,
            RedactionMode,
            SYSTEM_PROMPT,
            boundary_consent_hash,
            describe_llm_verify_boundary,
            judgments_to_state,
            summarize_judgments,
            verify_with_llm,
        )
        from audit_engine.runtime import workbench_version

        mode: RedactionMode | None = None
        if redaction in {"none", "pseudonym"}:
            mode = redaction  # type: ignore[assignment]

        scope = verification_scope if verification_scope in {"current_sample", "risk_signals"} else "current_sample"

        manifest = self._store.load_manifest(project_id)
        state = self._store.load_state(project_id)
        expected_token = _analysis_token(state)
        frames = []
        for year in manifest.years:
            df = self._store.get_analysis_work_df(project_id, year)
            if not df.empty:
                if "_year" not in df.columns:
                    df = df.copy()
                    df["_year"] = year
                frames.append(df)
        unified = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if unified.empty:
            raise ValueError("项目无序时账数据")

        sampling_plan = state.get("sampling_plan") or {}
        samples = list(state.get("samples") or [])
        sample_voucher_keys = [
            str(
                row.get(VOUCHER_KEY_COLUMN)
                or row.get("凭证唯一键")
                or row.get("_voucher_key")
                or ""
            ).strip()
            for row in samples
        ]
        sample_voucher_keys = [k for k in sample_voucher_keys if k]
        sample_voucher_keys = list(dict.fromkeys(sample_voucher_keys))

        if scope == "current_sample":
            if not sample_voucher_keys:
                raise ValueError(
                    "当前无抽样结果；请先在抽样底稿页完成抽样，"
                    "或改用 verification_scope=risk_signals 核验规则高风险信号"
                )
            scoped_keys: set[str] | None = set(sample_voucher_keys)
            effective_max = max(int(max_verify), len(sample_voucher_keys))
        else:
            scoped_keys = None
            effective_max = int(max_verify)

        cached_results = self._store.load_rule_run_results(project_id, state=state)
        rule_results = _rule_results_from_dict(cached_results)
        if not rule_results and scope == "risk_signals":
            cached_results = self.run_rules(project_id)
            state = self._store.load_state(project_id)
            expected_token = _analysis_token(state)
            rule_results = _rule_results_from_dict(cached_results)

        selection_trace = sampling_plan.get("selection_trace") or {}
        population_snapshot = sampling_plan.get("population_snapshot") or {}
        rule_run_context = state.get("rule_run_context") or {}
        boundary = describe_llm_verify_boundary(base_url, model, redaction=mode)
        expected_boundary_hash = boundary_consent_hash(boundary)
        if boundary_hash is not None and str(boundary_hash).strip():
            if str(boundary_hash).strip() != expected_boundary_hash:
                raise ValueError(
                    "数据边界已变化，请重新确认后再发送。"
                    f"（expected={expected_boundary_hash[:12]}…）"
                )

        engine_rev = workbench_version()
        prompt_revision = hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()[:12]
        sample_digest = hashlib.sha256(
            ",".join(sorted(scoped_keys or sample_voucher_keys)).encode()
        ).hexdigest()[:16]
        context_hash = hashlib.sha256(
            "|".join([
                scope,
                str(selection_trace.get("selection_id") or ""),
                str(population_snapshot.get("population_id") or ""),
                str(rule_run_context.get("rule_run_id") or ""),
                str(state.get("data_version") or ""),
                sample_digest,
                model,
                base_url,
                mode or "pseudonym",
                str(boundary.get("policy_version") or LLM_BOUNDARY_POLICY_VERSION),
                prompt_revision,
                engine_rev,
            ]).encode()
        ).hexdigest()[:16]
        # 每次执行唯一 ID（非确定性 LLM 输出不得复用同一 run id）
        verification_run_id = "vr_" + uuid.uuid4().hex[:16]
        requested_at = datetime.now(UTC).isoformat()
        run_context: dict[str, Any] = {
            "verification_run_id": verification_run_id,
            "verification_context_hash": context_hash,
            "verification_scope": scope,
            "selection_id": selection_trace.get("selection_id"),
            "population_id": population_snapshot.get("population_id"),
            "rule_run_id": rule_run_context.get("rule_run_id"),
            "data_version": state.get("data_version"),
            "sample_voucher_keys": list(scoped_keys) if scoped_keys is not None else sample_voucher_keys,
            "sample_voucher_count": len(scoped_keys) if scoped_keys is not None else len(sample_voucher_keys),
            "sample_keys_digest": sample_digest,
            "model": model,
            "endpoint": base_url,
            "redaction": mode or "pseudonym",
            "boundary_policy_version": boundary.get("policy_version"),
            "boundary_hash": expected_boundary_hash,
            "prompt_revision": prompt_revision,
            "engine_revision": engine_rev,
            "status": "fresh",
            "requested_at": requested_at,
        }

        if scope == "risk_signals" and not any(rr.hits for rr in rule_results):
            run_context["completed_at"] = datetime.now(UTC).isoformat()
            run_context["verified_voucher_keys"] = []
            run_context["verified_voucher_count"] = 0

            def clear(latest: dict[str, Any]) -> dict[str, Any]:
                _assert_analysis_token(latest, expected_token)
                latest["llm_judgments"] = {}
                latest["verification_run_context"] = run_context
                latest["llm_verify_boundary"] = boundary
                return latest

            self._store.update_state(project_id, clear)
            return {
                "summary": summarize_judgments({}),
                "judgments": {},
                "data_boundary": boundary,
                "verification_run_context": run_context,
            }

        judgments = verify_with_llm(
            unified,
            rule_results,
            api_key=api_key,
            model=model,
            base_url=base_url,
            max_verify=effective_max,
            redaction=mode,
            voucher_keys=scoped_keys,
            verification_scope=scope,  # type: ignore[arg-type]
        )
        verified_keys = sorted({
            str(getattr(j, "voucher_key", "") or "")
            for items in judgments.values()
            for j in items
            if str(getattr(j, "voucher_key", "") or "").strip()
        })
        serialized = judgments_to_state(judgments)
        completed_at = datetime.now(UTC).isoformat()
        run_context["verified_voucher_keys"] = verified_keys
        run_context["verified_voucher_count"] = len(verified_keys)
        run_context["completed_at"] = completed_at
        run_context["verified_at"] = completed_at
        summary = summarize_judgments(judgments)

        artifact_payload = {
            "verification_run_id": verification_run_id,
            "verification_context_hash": context_hash,
            "context": {k: v for k, v in run_context.items() if k != "sample_voucher_keys"},
            "sample_voucher_keys": run_context["sample_voucher_keys"],
            "judgments": serialized,
            "summary": summary,
            "data_boundary": boundary,
        }
        result_payload = json.dumps(
            artifact_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
        )
        result_hash = hashlib.sha256(result_payload.encode()).hexdigest()
        artifact_path = self._store.persist_verification_run(
            project_id,
            verification_run_id,
            artifact_payload,
            expected_hash=result_hash,
        )
        run_context["result_artifact"] = artifact_path
        run_context["result_hash"] = result_hash

        def update(latest: dict[str, Any]) -> dict[str, Any]:
            _assert_analysis_token(latest, expected_token)
            latest["llm_judgments"] = serialized
            latest["llm_verify_boundary"] = boundary
            latest["verification_run_context"] = run_context
            history = list(latest.get("verification_run_history") or [])
            history.append({
                "verification_run_id": verification_run_id,
                "verification_context_hash": context_hash,
                "selection_id": run_context.get("selection_id"),
                "result_artifact": artifact_path,
                "result_hash": result_hash,
                "completed_at": completed_at,
                "summary": summary,
            })
            latest["verification_run_history"] = history[-50:]
            return latest

        self._store.update_state(project_id, update)
        return {
            "summary": summary,
            "judgments": serialized,
            "data_boundary": boundary,
            "verification_run_context": run_context,
        }
