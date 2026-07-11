"""分析流水线 — 画像 / 跨年 / 规则执行 / 抽样。"""

from __future__ import annotations

from typing import Any

import pandas as pd

from audit_engine.candidate_pool import (
    MANUAL_FINAL_STATUS,
    sample_from_pool,
    sample_from_rule_results,
)
from audit_engine.cross_year import CrossYearFinding, run_cross_year_analysis
from audit_engine.profiler import build_financial_summary, build_profile
from audit_engine.reporter import generate_report_bytes
from audit_engine.rule_engine import RuleResult, run_all_rules
from audit_engine.rules_config import merge_rules_config
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
                    "priority": h.priority,
                    "year": h.year,
                    "group_id": h.group_id,
                    "related_voucher_ids": list(h.related_voucher_ids),
                    "relation_evidence": h.relation_evidence,
                }
                for h in rr.hits
            ],
        })
    return out


def _rule_results_from_dict(data: list[dict[str, Any]]) -> list[RuleResult]:
    from audit_engine.rule_engine import RuleHit

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
            )
            for h in block.get("hits", [])
        ]
        results.append(RuleResult(rule_name=str(block.get("rule_name", "")), hits=hits))
    return results


class AnalysisPipeline:
    def __init__(self, store: ProjectStore) -> None:
        self._store = store

    def load_rules(self, project_id: str) -> dict:
        state = self._store.load_state(project_id)
        return merge_rules_config(None, state.get("rules_config"))

    def save_rules(self, project_id: str, rules: dict) -> dict:
        state = self._store.load_state(project_id)
        state["rules_config"] = rules
        self._store.save_state(project_id, state)
        return rules

    def build_profiles(self, project_id: str) -> dict[int, dict]:
        manifest = self._store.load_manifest(project_id)
        state = self._store.load_state(project_id)
        profiles: dict[int, dict] = {}
        financials: dict[int, dict] = {}
        for year in manifest.years:
            work = self._store.get_work_df(project_id, year)
            if work.empty:
                continue
            profiles[year] = build_profile(work, year)
            financials[year] = build_financial_summary(work, year)
        state["profiles"] = profiles
        state["financials"] = financials
        self._store.save_state(project_id, state)
        return profiles

    def run_cross_year(self, project_id: str) -> list[dict]:
        manifest = self._store.load_manifest(project_id)
        state = self._store.load_state(project_id)
        overrides = _category_overrides(state)
        rules = self.load_rules(project_id)
        year_map = {
            year: self._store.get_work_df(project_id, year)
            for year in manifest.years
        }
        findings = run_cross_year_analysis(year_map, rules, category_overrides=overrides)
        serialized = [_finding_to_dict(f) for f in findings]
        state["cross_year_findings"] = serialized
        self._store.save_state(project_id, state)
        return serialized

    def run_rules(self, project_id: str) -> list[dict]:
        manifest = self._store.load_manifest(project_id)
        state = self._store.load_state(project_id)
        rules = self.load_rules(project_id)
        frames = []
        for year in manifest.years:
            df = self._store.get_work_df(project_id, year)
            if not df.empty:
                if "_year" not in df.columns:
                    df = df.copy()
                    df["_year"] = year
                frames.append(df)
        if not frames:
            return []
        unified = pd.concat(frames, ignore_index=True)
        cross_raw = state.get("cross_year_findings") or []
        cross_findings = [
            CrossYearFinding(
                category=str(f.get("category", "")),
                description=str(f.get("description", "")),
                years_involved=list(f.get("years_involved", [])),
                voucher_ids=[str(v) for v in f.get("voucher_ids", [])],
                amount=float(f.get("amount", 0)),
                severity=str(f.get("severity", "中")),
                evidence=dict(f.get("evidence", {})),
            )
            for f in cross_raw
        ]
        pool = self._store.load_candidate_pool(project_id)
        candidate_ids = None
        if pool:
            from audit_engine.candidate_pool import active_candidate_voucher_ids
            candidate_ids = active_candidate_voucher_ids(pool)
        results = run_all_rules(
            unified,
            rules,
            cross_year_findings=cross_findings if cross_findings else None,
            candidate_voucher_ids=candidate_ids,
        )
        serialized = _rule_results_to_dict(results)
        state["rule_results"] = serialized
        self._store.save_state(project_id, state)
        return serialized

    def extract_samples(
        self,
        project_id: str,
        *,
        method: str = "by_rule",
        size: int | None = None,
        seed: int = 42,
    ) -> dict[str, Any]:
        manifest = self._store.load_manifest(project_id)
        state = self._store.load_state(project_id)
        rules = self.load_rules(project_id)
        pool = self._store.load_candidate_pool(project_id)
        frames = []
        for year in manifest.years:
            df = self._store.get_work_df(project_id, year)
            if not df.empty:
                if "_year" not in df.columns:
                    df = df.copy()
                    df["_year"] = year
                frames.append(df)
        unified = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

        if method == "by_rule" and state.get("rule_results"):
            results = _rule_results_from_dict(state["rule_results"])
            max_size = size or int(rules.get("max_sample_size", 50))
            samples = sample_from_rule_results(results, unified, size=max_size, pool=pool)
        else:
            max_size = size or int(rules.get("max_sample_size", 50))
            cross_raw = state.get("cross_year_findings") or []
            cross_findings = [
                CrossYearFinding(
                    category=str(f.get("category", "")),
                    description=str(f.get("description", "")),
                    years_involved=list(f.get("years_involved", [])),
                    voucher_ids=[str(v) for v in f.get("voucher_ids", [])],
                    amount=float(f.get("amount", 0)),
                    severity=str(f.get("severity", "中")),
                    evidence=dict(f.get("evidence", {})),
                )
                for f in cross_raw
            ] if method == "by_rule" else None
            if method == "by_rule" and not state.get("rule_results"):
                results = run_all_rules(
                    unified,
                    rules,
                    cross_year_findings=cross_findings,
                )
                state["rule_results"] = _rule_results_to_dict(results)
                self._store.save_state(project_id, state)
            samples = sample_from_pool(
                pool,
                unified,
                method=method,
                size=max_size,
                rules_config=rules,
                seed=seed,
            )

        voucher_count = len({s["凭证编号"] for s in samples})
        state["samples"] = samples
        self._store.save_state(project_id, state)
        return {
            "method": method,
            "sample_rows": len(samples),
            "voucher_count": voucher_count,
            "samples": samples[:500],
        }

    def export_excel(self, project_id: str) -> tuple[bytes, dict]:
        manifest = self._store.load_manifest(project_id)
        state = self._store.load_state(project_id)
        rules = self.load_rules(project_id)
        frames = []
        for year in manifest.years:
            df = self._store.get_work_df(project_id, year)
            if not df.empty:
                if "_year" not in df.columns:
                    df = df.copy()
                    df["_year"] = year
                frames.append(df)
        unified = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        rule_results = _rule_results_from_dict(state.get("rule_results") or [])
        if not rule_results:
            rule_results = run_all_rules(unified, rules)
        manual_final = [
            g for g in self._store.load_candidate_pool(project_id)
            if g.get("status") == MANUAL_FINAL_STATUS
        ]
        return generate_report_bytes(
            unified,
            rule_results,
            llm_judgments={},
            max_sample_size=int(rules.get("max_sample_size", 50)),
            manual_final_samples=manual_final,
            explicit_samples=list(state.get("samples") or []) if "samples" in state else None,
        )
