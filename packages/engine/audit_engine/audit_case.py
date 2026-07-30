"""Versioned audit-case domain model and state-backed service.

The analytical data plane remains Parquet/DuckDB.  Audit cases are control-plane
records: they survive re-ingestion, keep an append-only event stream, and retain
the version scope that made each piece of evidence meaningful.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from audit_engine.store import ProjectStore


AUDIT_CASE_STATE_KEY = "audit_cases_v1"
AUDIT_CASE_SCHEMA_VERSION = 2

CASE_KIND_PROPOSAL = "proposal"
CASE_KIND_FORMAL = "case"
CASE_STATUS_DRAFT = "draft"
CASE_STATUS_PLANNED = "planned"
CASE_STATUS_IN_PROGRESS = "in_progress"
CASE_STATUS_PENDING_EVIDENCE = "pending_evidence"
CASE_STATUS_CONCLUDED = "concluded"
CASE_STATUS_CLOSED = "closed"

EVIDENCE_STATUS_ACTIVE = "active"
EVIDENCE_STATUS_STALE = "stale"
EVIDENCE_STATUS_VERIFIED = "verified"
EVIDENCE_STATUS_REQUESTED = "requested"
EVIDENCE_STATUS_MISSING = "missing"

_ASSERTION_STATUSES = {"open", "supported", "exception"}
_EVIDENCE_STATUSES = {
    EVIDENCE_STATUS_ACTIVE,
    EVIDENCE_STATUS_VERIFIED,
    EVIDENCE_STATUS_STALE,
    EVIDENCE_STATUS_REQUESTED,
    EVIDENCE_STATUS_MISSING,
}
_PROCEDURE_STATUSES = {"planned", "in_progress", "completed", "not_applicable"}
_CONCLUSION_PENDING_OUTCOMES = {"insufficient_evidence", "scope_limitation"}
_CONCLUSION_OUTCOMES = {
    "no_exception",
    "reasonable_exception",
    "exception",
    "finding",
    "insufficient_evidence",
    "control_deficiency",
    "misstatement",
    "scope_limitation",
}
_EXCEPTION_BEARING_OUTCOMES = {
    "reasonable_exception",
    "exception",
    "finding",
    "control_deficiency",
    "misstatement",
}

_CASE_MATERIALITY_LEVELS = {"unassessed", "low", "medium", "high"}
_CASE_STATUS_TRANSITIONS = {
    CASE_STATUS_DRAFT: {CASE_STATUS_PLANNED, CASE_STATUS_CLOSED},
    CASE_STATUS_PLANNED: {
        CASE_STATUS_IN_PROGRESS,
        CASE_STATUS_PENDING_EVIDENCE,
    },
    CASE_STATUS_IN_PROGRESS: {CASE_STATUS_PENDING_EVIDENCE, CASE_STATUS_CONCLUDED},
    CASE_STATUS_PENDING_EVIDENCE: {CASE_STATUS_IN_PROGRESS, CASE_STATUS_CONCLUDED},
    CASE_STATUS_CONCLUDED: {CASE_STATUS_IN_PROGRESS, CASE_STATUS_CLOSED},
    CASE_STATUS_CLOSED: set(),
}
_DATA_DERIVED_EVIDENCE_TYPES = {
    "candidate_group",
    "chart_selection",
    "journal",
    "module_insight",
    "query_result",
    "rule_hit",
    "source_coordinate",
}
_RISK_SIGNAL_EVIDENCE_TYPES = {
    "candidate_group",
    "chart_selection",
    "module_insight",
    "query_result",
    "rule_hit",
}
_CORROBORATIVE_ONLY_EVIDENCE_TYPES = {"management_explanation"}
_NON_SUBSTANTIVE_EVIDENCE_TYPES = (
    _RISK_SIGNAL_EVIDENCE_TYPES | _CORROBORATIVE_ONLY_EVIDENCE_TYPES
)
_SUPPORTED_EVIDENCE_SOURCE_TYPES = {
    *_DATA_DERIVED_EVIDENCE_TYPES,
    "attachment",
    "management_explanation",
    "counter_evidence",
}


class CaseVersionConflict(ValueError):
    """Raised when a write is based on a stale case version."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _json_clone(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class VersionScope:
    data_version: str = ""
    ingest_run_id: str = ""
    classification_revision: str = ""
    analysis_scope_revision: str = ""
    currency_scope: str | None = None
    rule_revision: str = ""
    rule_run_id: str = ""
    engine_revision: str = ""
    result_hash: str = ""
    years: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Assertion:
    assertion_id: str
    title: str
    risk_statement: str
    financial_statement_assertions: list[str] = field(default_factory=list)
    affected_accounts: list[str] = field(default_factory=list)
    status: str = "open"
    created_by: str = "user"
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SourceRef:
    """Stable source coordinate; ``locator`` carries non-row aggregate context."""

    source_asset_id: str = ""
    file_hash: str = ""
    sheet: str = ""
    source_row: int | None = None
    source_column: str = ""
    voucher_key: str = ""
    line_key: str = ""
    locator: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EvidenceRef:
    evidence_id: str
    source_type: str
    source_ref: dict[str, Any]
    description: str = ""
    status: str = EVIDENCE_STATUS_ACTIVE
    assertion_ids: list[str] = field(default_factory=list)
    procedure_id: str = ""
    stale_reason: str = ""
    stale_at: str = ""
    scope: dict[str, Any] = field(default_factory=dict)
    created_by: str = "user"
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Procedure:
    procedure_id: str
    title: str
    description: str
    assertion_ids: list[str] = field(default_factory=list)
    status: str = "planned"
    result: str = ""
    performed_by: str = ""
    performed_at: str = ""
    created_by: str = "user"
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Conclusion:
    outcome: str
    summary: str
    basis_evidence_ids: list[str] = field(default_factory=list)
    procedure_ids: list[str] = field(default_factory=list)
    unresolved_assertion_ids: list[str] = field(default_factory=list)
    override_reason: str = ""
    status: str = "active"
    stale_reason: str = ""
    stale_at: str = ""
    misstatement_amount: float | None = None
    currency: str = ""
    concluded_by: str = "user"
    concluded_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Event:
    event_id: str
    case_id: str
    event_type: str
    actor: str
    at: str
    case_version: int
    prev_hash: str
    event_hash: str
    snapshot_id: str
    snapshot_digest: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Snapshot:
    snapshot_id: str
    case_id: str
    case_version: int
    scope: dict[str, Any]
    case_digest: str
    case_state: dict[str, Any]
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Case:
    case_id: str
    title: str
    risk_summary: str
    materiality: str
    kind: str
    status: str
    risk_domain: str
    version_scope: dict[str, Any]
    assertions: list[dict[str, Any]] = field(default_factory=list)
    evidence_refs: list[dict[str, Any]] = field(default_factory=list)
    procedures: list[dict[str, Any]] = field(default_factory=list)
    conclusion: dict[str, Any] | None = None
    reviews: list[dict[str, Any]] = field(default_factory=list)
    signoffs: list[dict[str, Any]] = field(default_factory=list)
    source_candidate_group_id: str = ""
    owner: str = ""
    tags: list[str] = field(default_factory=list)
    created_by: str = "user"
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _version_scope_from_state(
    store: ProjectStore,
    project_id: str,
    state: dict[str, Any],
) -> VersionScope:
    from audit_engine.rules_config import merge_rules_config
    from audit_engine.runtime import workbench_version

    manifest = store.load_manifest(project_id)
    rules_payload = merge_rules_config(None, state.get("rules_config"))
    classification_revision = str(state.get("classification_revision") or "")
    if not classification_revision:
        classification_revision = _digest({
            "decisions": state.get("account_classification_decisions") or {},
            "overrides": state.get("account_category_overrides") or {},
        })[:16]
    rule_context = (
        state.get("rule_run_context")
        if isinstance(state.get("rule_run_context"), dict)
        else {}
    )

    return VersionScope(
        data_version=str(state.get("data_version") or ""),
        ingest_run_id=str(
            state.get("current_ingest_run_id")
            or getattr(manifest, "current_ingest_run_id", "")
            or ""
        ),
        classification_revision=classification_revision,
        analysis_scope_revision=str(state.get("analysis_scope_revision") or ""),
        currency_scope=str(state.get("analysis_currency_scope") or "") or None,
        rule_revision=_digest(rules_payload)[:16],
        rule_run_id=str(rule_context.get("rule_run_id") or ""),
        engine_revision=str(
            rule_context.get("engine_revision")
            or state.get("engine_revision")
            or workbench_version()
        ),
        result_hash=str(rule_context.get("result_hash") or ""),
        years=list(manifest.years),
    )


def current_version_scope(store: ProjectStore, project_id: str) -> VersionScope:
    return _version_scope_from_state(store, project_id, store.load_state(project_id))


def _container(state: dict[str, Any]) -> dict[str, Any]:
    raw = state.get(AUDIT_CASE_STATE_KEY)
    if not isinstance(raw, dict):
        raw = {}
    raw["schema_version"] = AUDIT_CASE_SCHEMA_VERSION
    if not isinstance(raw.get("cases"), dict):
        raw["cases"] = {}
    if not isinstance(raw.get("events"), list):
        raw["events"] = []
    if not isinstance(raw.get("snapshots"), list):
        raw["snapshots"] = []
    if not isinstance(raw.get("anchors"), dict):
        raw["anchors"] = {}
    state[AUDIT_CASE_STATE_KEY] = raw
    return raw


def _case_records(
    container: dict[str, Any],
    key: str,
    case_id: str,
) -> list[dict[str, Any]]:
    return [
        item
        for item in container.get(key) or []
        if isinstance(item, dict) and item.get("case_id") == case_id
    ]


def _append_snapshot(
    container: dict[str, Any],
    case: dict[str, Any],
    *,
    at: str | None = None,
) -> dict[str, Any]:
    state_copy = _json_clone(case)
    snapshot = Snapshot(
        snapshot_id=_new_id("snap"),
        case_id=str(case["case_id"]),
        case_version=int(case.get("version") or 1),
        scope=_json_clone(case.get("version_scope") or {}),
        case_digest=_digest(state_copy),
        case_state=state_copy,
        created_at=at or _now(),
    ).to_dict()
    container["snapshots"].append(snapshot)
    return snapshot


def _append_event(
    container: dict[str, Any],
    case: dict[str, Any],
    event_type: str,
    snapshot: dict[str, Any],
    *,
    actor: str,
    payload: dict[str, Any] | None = None,
    at: str | None = None,
) -> dict[str, Any]:
    case_id = str(case["case_id"])
    previous_events = _case_records(container, "events", case_id)
    previous_hash = str(previous_events[-1].get("event_hash") or "") if previous_events else ""
    event = Event(
        event_id=_new_id("evt"),
        case_id=case_id,
        event_type=event_type,
        actor=actor or "user",
        at=at or _now(),
        case_version=int(case.get("version") or 1),
        prev_hash=previous_hash,
        event_hash="",
        snapshot_id=str(snapshot["snapshot_id"]),
        snapshot_digest=str(snapshot["case_digest"]),
        payload=_json_clone(payload or {}),
    ).to_dict()
    event["event_hash"] = _digest(
        {key: value for key, value in event.items() if key != "event_hash"}
    )
    container["events"].append(event)
    return event


def _refresh_anchor(
    container: dict[str, Any],
    case: dict[str, Any],
    event: dict[str, Any],
    snapshot: dict[str, Any],
) -> None:
    case_id = str(case["case_id"])
    container["anchors"][case_id] = {
        "event_count": len(_case_records(container, "events", case_id)),
        "head_event_hash": str(event["event_hash"]),
        "snapshot_count": len(_case_records(container, "snapshots", case_id)),
        "head_snapshot_id": str(snapshot["snapshot_id"]),
        "head_snapshot_digest": str(snapshot["case_digest"]),
        "case_version": int(case.get("version") or 0),
        "case_digest": _digest(case),
    }


def _record_change(
    container: dict[str, Any],
    case: dict[str, Any],
    event_type: str,
    *,
    actor: str,
    payload: dict[str, Any] | None = None,
    at: str | None = None,
) -> dict[str, Any]:
    timestamp = at or _now()
    snapshot = _append_snapshot(container, case, at=timestamp)
    event = _append_event(
        container,
        case,
        event_type,
        snapshot,
        actor=actor,
        payload=payload,
        at=timestamp,
    )
    _refresh_anchor(container, case, event, snapshot)
    return event


def _mark_signoffs_stale(
    case: dict[str, Any],
    *,
    reason: str,
    at: str,
) -> list[str]:
    stale_ids: list[str] = []
    for signoff in case.get("signoffs") or []:
        if not isinstance(signoff, dict) or signoff.get("status") != "active":
            continue
        signoff["status"] = "stale"
        signoff["stale_reason"] = reason
        signoff["stale_at"] = at
        stale_ids.append(str(signoff.get("signoff_id") or ""))
    return stale_ids


def _mark_conclusion_stale(
    case: dict[str, Any],
    *,
    reason: str,
    at: str,
) -> bool:
    conclusion = case.get("conclusion")
    if not isinstance(conclusion, dict) or conclusion.get("status") == "stale":
        return False
    conclusion["status"] = "stale"
    conclusion["stale_reason"] = reason
    conclusion["stale_at"] = at
    return True


def mark_case_evidence_stale_in_state(
    state: dict[str, Any],
    *,
    reason: str,
    trigger: str,
    at: str | None = None,
    source_types: set[str] | list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Mark data-derived evidence stale without deleting cases or past events."""
    raw = state.get(AUDIT_CASE_STATE_KEY)
    if not isinstance(raw, dict) or not isinstance(raw.get("cases"), dict):
        return state

    container = _container(state)
    timestamp = at or _now()
    selected_source_types = {
        str(value).strip()
        for value in (
            source_types if source_types is not None else _DATA_DERIVED_EVIDENCE_TYPES
        )
        if str(value).strip()
    }
    for case in container["cases"].values():
        if not isinstance(case, dict):
            continue
        stale_ids: list[str] = []
        evidence_refs = list(case.get("evidence_refs") or [])
        for evidence in evidence_refs:
            if not isinstance(evidence, dict) or evidence.get("status") not in {
                EVIDENCE_STATUS_ACTIVE,
                EVIDENCE_STATUS_VERIFIED,
            }:
                continue
            source_type = str(evidence.get("source_type") or "")
            if source_type not in selected_source_types:
                continue
            evidence["status"] = EVIDENCE_STATUS_STALE
            evidence["stale_reason"] = reason
            evidence["stale_at"] = timestamp
            stale_ids.append(str(evidence.get("evidence_id") or ""))
        if not stale_ids:
            continue
        case["evidence_refs"] = evidence_refs
        previous_status = str(case.get("status") or "")
        conclusion = case.get("conclusion")
        conclusion_basis = (
            {
                str(value)
                for value in conclusion.get("basis_evidence_ids") or []
                if str(value)
            }
            if isinstance(conclusion, dict)
            else set()
        )
        invalidates_conclusion = bool(conclusion_basis.intersection(stale_ids)) or previous_status in {
            CASE_STATUS_CONCLUDED,
            CASE_STATUS_CLOSED,
        }
        stale_signoff_ids: list[str] = []
        if invalidates_conclusion:
            case["status"] = CASE_STATUS_PENDING_EVIDENCE
            _mark_conclusion_stale(case, reason=reason, at=timestamp)
            stale_signoff_ids = _mark_signoffs_stale(case, reason=reason, at=timestamp)
        case["version"] = int(case.get("version") or 1) + 1
        case["updated_at"] = timestamp
        _record_change(
            container,
            case,
            "case.evidence_stale",
            actor="system",
            payload={
                "trigger": trigger,
                "reason": reason,
                "evidence_ids": stale_ids,
                "source_types": sorted(selected_source_types),
                "from_status": previous_status,
                "to_status": case.get("status"),
                "stale_signoff_ids": stale_signoff_ids,
            },
            at=timestamp,
        )
    return state


def verify_case_integrity_in_state(
    state: dict[str, Any],
    case_id: str,
) -> dict[str, Any]:
    """Verify event, snapshot, live-case and durable anchor integrity."""
    raw = state.get(AUDIT_CASE_STATE_KEY) or {}
    case = (raw.get("cases") or {}).get(case_id) if isinstance(raw, dict) else None
    events = _case_records(raw, "events", case_id) if isinstance(raw, dict) else []
    snapshots = _case_records(raw, "snapshots", case_id) if isinstance(raw, dict) else []
    anchor = (raw.get("anchors") or {}).get(case_id) if isinstance(raw, dict) else None
    checks = {
        "case_exists": isinstance(case, dict),
        "anchor_present": isinstance(anchor, dict),
        "event_chain": True,
        "event_versions_contiguous": True,
        "snapshot_digests": True,
        "snapshot_versions_contiguous": True,
        "event_snapshot_binding": True,
        "latest_snapshot_matches_case": True,
        "anchor_matches": True,
    }
    errors: list[dict[str, Any]] = []

    def fail(check: str, reason: str, **details: Any) -> None:
        checks[check] = False
        errors.append({"check": check, "reason": reason, **details})

    if not isinstance(case, dict):
        fail("case_exists", "case_missing")
    if not isinstance(anchor, dict):
        fail("anchor_present", "anchor_missing")

    previous_hash = ""
    for index, event in enumerate(events):
        event_id = str(event.get("event_id") or "")
        if str(event.get("prev_hash") or "") != previous_hash:
            fail(
                "event_chain",
                "prev_hash_mismatch",
                index=index,
                event_id=event_id,
            )
        claimed_hash = str(event.get("event_hash") or "")
        expected_hash = _digest(
            {key: value for key, value in event.items() if key != "event_hash"}
        )
        if not claimed_hash or claimed_hash != expected_hash:
            fail(
                "event_chain",
                "event_hash_mismatch",
                index=index,
                event_id=event_id,
            )
        if int(event.get("case_version") or 0) != index + 1:
            fail(
                "event_versions_contiguous",
                "event_version_gap",
                index=index,
                event_id=event_id,
            )
        previous_hash = claimed_hash

    snapshots_by_id: dict[str, dict[str, Any]] = {}
    for index, snapshot in enumerate(snapshots):
        snapshot_id = str(snapshot.get("snapshot_id") or "")
        snapshots_by_id[snapshot_id] = snapshot
        expected_digest = _digest(snapshot.get("case_state"))
        if not snapshot.get("case_digest") or snapshot.get("case_digest") != expected_digest:
            fail(
                "snapshot_digests",
                "snapshot_digest_mismatch",
                index=index,
                snapshot_id=snapshot_id,
            )
        if int(snapshot.get("case_version") or 0) != index + 1:
            fail(
                "snapshot_versions_contiguous",
                "snapshot_version_gap",
                index=index,
                snapshot_id=snapshot_id,
            )

    for event in events:
        snapshot = snapshots_by_id.get(str(event.get("snapshot_id") or ""))
        if (
            not snapshot
            or event.get("snapshot_digest") != snapshot.get("case_digest")
            or event.get("case_version") != snapshot.get("case_version")
        ):
            fail(
                "event_snapshot_binding",
                "event_snapshot_mismatch",
                event_id=str(event.get("event_id") or ""),
            )

    if isinstance(case, dict):
        latest = snapshots[-1] if snapshots else None
        if (
            not latest
            or latest.get("case_state") != case
            or latest.get("case_digest") != _digest(case)
        ):
            fail("latest_snapshot_matches_case", "live_case_mismatch")

    if isinstance(anchor, dict):
        latest_snapshot = snapshots[-1] if snapshots else {}
        anchor_expected = {
            "event_count": len(events),
            "head_event_hash": previous_hash,
            "snapshot_count": len(snapshots),
            "head_snapshot_id": str(latest_snapshot.get("snapshot_id") or ""),
            "head_snapshot_digest": str(latest_snapshot.get("case_digest") or ""),
            "case_version": int(case.get("version") or 0) if isinstance(case, dict) else 0,
            "case_digest": _digest(case) if isinstance(case, dict) else "",
        }
        if any(anchor.get(key) != value for key, value in anchor_expected.items()):
            fail("anchor_matches", "anchor_mismatch")

    return {
        "valid": all(checks.values()),
        "checks": checks,
        "errors": errors,
        "reason": str(errors[0].get("reason") or "") if errors else "",
        "event_count": len(events),
        "snapshot_count": len(snapshots),
        "head_hash": previous_hash,
        "case_version": int(case.get("version") or 0) if isinstance(case, dict) else 0,
    }


def verify_case_event_chain_in_state(
    state: dict[str, Any],
    case_id: str,
) -> dict[str, Any]:
    """Backward-compatible alias for the expanded case integrity verifier."""
    return verify_case_integrity_in_state(state, case_id)


def _case_readiness(case: dict[str, Any]) -> dict[str, Any]:
    """Return the objective gates for a substantive audit conclusion."""
    blockers: list[dict[str, Any]] = []
    assertions = [
        item for item in case.get("assertions") or [] if isinstance(item, dict)
    ]
    evidence = [
        item for item in case.get("evidence_refs") or [] if isinstance(item, dict)
    ]
    procedures = [
        item for item in case.get("procedures") or [] if isinstance(item, dict)
    ]
    assertion_ids = {
        str(item.get("assertion_id") or "")
        for item in assertions
        if str(item.get("assertion_id") or "")
    }
    unresolved_assertion_ids = sorted(
        str(item.get("assertion_id") or "")
        for item in assertions
        if str(item.get("assertion_id") or "")
        and str(item.get("status") or "open") == "open"
    )
    verified_evidence = [
        item
        for item in evidence
        if item.get("status") == EVIDENCE_STATUS_VERIFIED
        and str(item.get("source_type") or "")
        not in _NON_SUBSTANTIVE_EVIDENCE_TYPES
    ]
    completed_procedures = [
        item for item in procedures if item.get("status") == "completed"
    ]

    if case.get("kind") != CASE_KIND_FORMAL:
        blockers.append({
            "code": "not_formal_case",
            "message": "案件建议尚未立项。",
            "hard": True,
        })
    if not assertions:
        blockers.append({
            "code": "missing_assertions",
            "message": "至少需要一项待验证的财务报表认定。",
            "hard": True,
        })
    if unresolved_assertion_ids:
        blockers.append({
            "code": "open_assertions",
            "message": "仍有认定未完成判断。",
            "assertion_ids": unresolved_assertion_ids,
            "hard": False,
        })
    if not verified_evidence:
        blockers.append({
            "code": "missing_verified_evidence",
            "message": "至少需要一项已核验的实质性证据；风险信号不能代替证据。",
            "hard": True,
        })
    if not completed_procedures:
        blockers.append({
            "code": "missing_completed_procedure",
            "message": "至少需要完成一项审计程序并记录结果。",
            "hard": True,
        })

    evidence_coverage = {
        assertion_id: sorted(
            str(item.get("evidence_id") or "")
            for item in verified_evidence
            if assertion_id in {
                str(value) for value in item.get("assertion_ids") or [] if str(value)
            }
        )
        for assertion_id in assertion_ids
    }
    procedure_coverage = {
        assertion_id: sorted(
            str(item.get("procedure_id") or "")
            for item in completed_procedures
            if assertion_id in {
                str(value) for value in item.get("assertion_ids") or [] if str(value)
            }
        )
        for assertion_id in assertion_ids
    }
    missing_evidence_coverage = sorted(
        assertion_id
        for assertion_id, linked in evidence_coverage.items()
        if not linked
    )
    missing_procedure_coverage = sorted(
        assertion_id
        for assertion_id, linked in procedure_coverage.items()
        if not linked
    )
    if missing_evidence_coverage:
        blockers.append({
            "code": "assertions_without_verified_evidence",
            "message": "部分认定没有关联已核验证据。",
            "assertion_ids": missing_evidence_coverage,
            "hard": True,
        })
    if missing_procedure_coverage:
        blockers.append({
            "code": "assertions_without_completed_procedure",
            "message": "部分认定没有关联已完成程序。",
            "assertion_ids": missing_procedure_coverage,
            "hard": True,
        })

    stale_evidence_ids = sorted(
        str(item.get("evidence_id") or "")
        for item in evidence
        if item.get("status") == EVIDENCE_STATUS_STALE
    )
    pending_evidence_ids = sorted(
        str(item.get("evidence_id") or "")
        for item in evidence
        if item.get("status") in {EVIDENCE_STATUS_REQUESTED, EVIDENCE_STATUS_MISSING}
    )
    return {
        "ready": not blockers,
        "blockers": blockers,
        "assertion_count": len(assertions),
        "verified_evidence_count": len(verified_evidence),
        "completed_procedure_count": len(completed_procedures),
        "unresolved_assertion_ids": unresolved_assertion_ids,
        "stale_evidence_ids": stale_evidence_ids,
        "pending_evidence_ids": pending_evidence_ids,
        "evidence_coverage": evidence_coverage,
        "procedure_coverage": procedure_coverage,
    }


def _material_change(case: dict[str, Any], *, reason: str, at: str) -> None:
    """Invalidate a prior conclusion/signoff when its factual basis changes."""
    conclusion_changed = _mark_conclusion_stale(case, reason=reason, at=at)
    stale_signoffs = _mark_signoffs_stale(case, reason=reason, at=at)
    if (
        (conclusion_changed or stale_signoffs)
        and case.get("kind") == CASE_KIND_FORMAL
        and case.get("status") in {CASE_STATUS_CONCLUDED, CASE_STATUS_CLOSED}
    ):
        case["status"] = CASE_STATUS_IN_PROGRESS


def _assert_link_ids(
    case: dict[str, Any],
    assertion_ids: list[str] | None,
) -> list[str]:
    available = {
        str(item.get("assertion_id") or "")
        for item in case.get("assertions") or []
        if isinstance(item, dict) and str(item.get("assertion_id") or "")
    }
    selected = [
        str(value).strip()
        for value in (assertion_ids if assertion_ids is not None else sorted(available))
        if str(value).strip()
    ]
    selected = list(dict.fromkeys(selected))
    missing = sorted(set(selected) - available)
    if missing:
        raise ValueError(f"关联了不存在的审计认定：{missing}")
    if not selected:
        raise ValueError("证据或程序必须至少关联一项审计认定")
    return selected


class AuditCaseService:
    """Strict, atomic AuditCase lifecycle with replayable evidence lineage."""

    def __init__(self, store: ProjectStore) -> None:
        self._store = store

    def list_cases(
        self,
        project_id: str,
        *,
        status: str = "",
        kind: str = "",
    ) -> dict[str, Any]:
        state = self._store.load_state(project_id)
        raw = state.get(AUDIT_CASE_STATE_KEY) or {}
        cases = list((raw.get("cases") or {}).values()) if isinstance(raw, dict) else []
        if status:
            cases = [case for case in cases if case.get("status") == status]
        if kind:
            cases = [case for case in cases if case.get("kind") == kind]
        cases.sort(key=lambda case: str(case.get("updated_at") or ""), reverse=True)
        return {
            "schema_version": AUDIT_CASE_SCHEMA_VERSION,
            "count": len(cases),
            "cases": _json_clone(cases),
            "readiness": {
                str(case.get("case_id") or ""): _case_readiness(case)
                for case in cases
            },
            "current_scope": _version_scope_from_state(
                self._store, project_id, state
            ).to_dict(),
        }

    def get_case(self, project_id: str, case_id: str) -> dict[str, Any]:
        state = self._store.load_state(project_id)
        raw = state.get(AUDIT_CASE_STATE_KEY) or {}
        case = (raw.get("cases") or {}).get(case_id) if isinstance(raw, dict) else None
        if not isinstance(case, dict):
            raise KeyError(f"审计案件不存在：{case_id}")
        return {
            "case": _json_clone(case),
            "readiness": _case_readiness(case),
            "integrity": verify_case_integrity_in_state(state, case_id),
            "current_scope": _version_scope_from_state(
                self._store, project_id, state
            ).to_dict(),
        }

    def case_readiness(self, project_id: str, case_id: str) -> dict[str, Any]:
        return self.get_case(project_id, case_id)["readiness"]

    def create_case(
        self,
        project_id: str,
        *,
        title: str,
        risk_statement: str = "",
        financial_statement_assertions: list[str] | None = None,
        affected_accounts: list[str] | None = None,
        risk_domain: str = "",
        materiality: str = "unassessed",
        proposal: bool = True,
        owner: str = "",
        tags: list[str] | None = None,
        actor: str = "user",
    ) -> dict[str, Any]:
        clean_title = title.strip()
        if not clean_title:
            raise ValueError("案件标题不能为空")
        clean_materiality = materiality.strip().lower() or "unassessed"
        if clean_materiality not in _CASE_MATERIALITY_LEVELS:
            raise ValueError(f"不支持的重要程度：{materiality}")
        case_id = _new_id("case")
        result: dict[str, Any] = {}
        event: dict[str, Any] = {}
        result_scope: dict[str, Any] = {}

        def update(state: dict[str, Any]) -> None:
            nonlocal result, event, result_scope
            container = _container(state)
            now = _now()
            scope = _version_scope_from_state(
                self._store, project_id, state
            ).to_dict()
            assertions: list[dict[str, Any]] = []
            risk_text = risk_statement.strip()
            if risk_text:
                assertions.append(
                    Assertion(
                        assertion_id=_new_id("asrt"),
                        title=clean_title,
                        risk_statement=risk_text,
                        financial_statement_assertions=list(
                            financial_statement_assertions or []
                        ),
                        affected_accounts=list(affected_accounts or []),
                        created_by=actor,
                        created_at=now,
                    ).to_dict()
                )
            case = Case(
                case_id=case_id,
                title=clean_title,
                risk_summary=risk_text,
                materiality=clean_materiality,
                kind=CASE_KIND_PROPOSAL if proposal else CASE_KIND_FORMAL,
                status=CASE_STATUS_DRAFT if proposal else CASE_STATUS_PLANNED,
                risk_domain=risk_domain.strip(),
                version_scope=scope,
                assertions=assertions,
                owner=owner.strip(),
                tags=sorted({tag.strip() for tag in tags or [] if tag.strip()}),
                created_by=actor.strip() or "user",
                created_at=now,
                updated_at=now,
            ).to_dict()
            container["cases"][case_id] = case
            event = _record_change(
                container,
                case,
                "case.proposed" if proposal else "case.created",
                actor=actor,
                payload={"source": "manual"},
                at=now,
            )
            result = case
            result_scope = scope

        self._store.update_state(project_id, update)
        return self._result(result, result_scope, event)

    def create_from_candidate(
        self,
        project_id: str,
        group_id: str,
        *,
        formal: bool = False,
        title: str = "",
        risk_statement: str = "",
        financial_statement_assertions: list[str] | None = None,
        materiality: str = "unassessed",
        owner: str = "",
        actor: str = "user",
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        clean_materiality = materiality.strip().lower() or "unassessed"
        if clean_materiality not in _CASE_MATERIALITY_LEVELS:
            raise ValueError(f"不支持的重要程度：{materiality}")
        case_id = "case_" + hashlib.sha256(
            f"{project_id}|{group_id}".encode()
        ).hexdigest()[:12]
        result: dict[str, Any] = {}
        result_event: dict[str, Any] = {}
        result_scope: dict[str, Any] = {}

        def update(state: dict[str, Any]) -> None:
            nonlocal result, result_event, result_scope
            container = _container(state)
            scope = _version_scope_from_state(
                self._store, project_id, state
            ).to_dict()
            existing = container["cases"].get(case_id)
            if isinstance(existing, dict):
                if formal and existing.get("kind") == CASE_KIND_PROPOSAL:
                    if expected_version is None:
                        raise ValueError(
                            "已有案件建议立项必须提供 expected_version"
                        )
                    integrity = verify_case_integrity_in_state(state, case_id)
                    if not integrity["valid"]:
                        raise ValueError(
                            "案件完整性校验失败，禁止继续立项或修改；"
                            "请先保全当前项目并复核事件、快照与案件记录。"
                        )
                    self._check_expected_version(existing, expected_version)
                    if existing.get("status") != CASE_STATUS_DRAFT:
                        raise ValueError("只有草稿状态的案件建议可以立项")
                    now = _now()
                    existing["kind"] = CASE_KIND_FORMAL
                    existing["status"] = CASE_STATUS_PLANNED
                    existing["version"] = int(existing.get("version") or 1) + 1
                    existing["updated_at"] = now
                    existing["version_scope"] = scope
                    result_event = _record_change(
                        container,
                        existing,
                        "case.promoted",
                        actor=actor,
                        payload={"source_candidate_group_id": group_id},
                        at=now,
                    )
                result = existing
                result_scope = scope
                return

            group = next(
                (
                    item
                    for item in state.get("candidate_pool") or []
                    if isinstance(item, dict) and item.get("group_id") == group_id
                ),
                None,
            )
            if group is None:
                raise KeyError(f"疑点组不存在：{group_id}")
            now = _now()
            case_title = title.strip() or str(group.get("title") or "候选审计事项")
            risk_text = risk_statement.strip() or str(group.get("reason") or case_title)
            assertion = Assertion(
                assertion_id=_new_id("asrt"),
                title=case_title,
                risk_statement=risk_text,
                financial_statement_assertions=list(
                    financial_statement_assertions or []
                ),
                created_by=actor,
                created_at=now,
            ).to_dict()
            voucher_ids = sorted({
                str(value)
                for value in group.get("voucher_ids") or []
                if str(value)
            })
            voucher_keys = self._candidate_voucher_keys(
                project_id, group, voucher_ids
            )
            locator = {
                "group_id": group_id,
                "source_module": group.get("source_module"),
                "source_view": group.get("source_view"),
                "selector": _json_clone(group.get("selector") or {}),
                "voucher_ids": voucher_ids,
                "voucher_keys": voucher_keys,
                "candidate_snapshot_digest": _digest(group),
                "row_count": int(group.get("row_count") or 0),
                "voucher_count": int(group.get("voucher_count") or len(voucher_keys)),
            }
            evidence = EvidenceRef(
                evidence_id=_new_id("evd"),
                source_type="candidate_group",
                source_ref=SourceRef(locator=locator).to_dict(),
                description=str(group.get("reason") or ""),
                status=EVIDENCE_STATUS_ACTIVE,
                assertion_ids=[assertion["assertion_id"]],
                scope=scope,
                created_by=actor,
                created_at=now,
            ).to_dict()
            case = Case(
                case_id=case_id,
                title=case_title,
                risk_summary=risk_text,
                materiality=clean_materiality,
                kind=CASE_KIND_FORMAL if formal else CASE_KIND_PROPOSAL,
                status=CASE_STATUS_PLANNED if formal else CASE_STATUS_DRAFT,
                risk_domain=str(group.get("source_module") or ""),
                version_scope=scope,
                assertions=[assertion],
                evidence_refs=[evidence],
                source_candidate_group_id=group_id,
                owner=owner.strip(),
                tags=list(group.get("tags") or []),
                created_by=actor,
                created_at=now,
                updated_at=now,
            ).to_dict()
            container["cases"][case_id] = case
            result_event = _record_change(
                container,
                case,
                "case.created" if formal else "case.proposed",
                actor=actor,
                payload={
                    "source": "candidate_group",
                    "group_id": group_id,
                    "candidate_snapshot_digest": locator["candidate_snapshot_digest"],
                },
                at=now,
            )
            result = case
            result_scope = scope

        self._store.update_state(project_id, update)
        return self._result(result, result_scope, result_event)

    def promote_case(
        self,
        project_id: str,
        case_id: str,
        *,
        actor: str = "user",
        comment: str = "",
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        def promote(case: dict[str, Any], _state: dict[str, Any]) -> None:
            if case.get("kind") != CASE_KIND_PROPOSAL:
                raise ValueError("该事项已经是正式案件")
            if case.get("status") != CASE_STATUS_DRAFT:
                raise ValueError("只有草稿状态的案件建议可以立项")
            case.update({"kind": CASE_KIND_FORMAL, "status": CASE_STATUS_PLANNED})

        return self._mutate_case(
            project_id,
            case_id,
            actor=actor,
            event_type="case.promoted",
            payload={"comment": comment.strip()},
            mutator=promote,
            expected_version=expected_version,
        )

    def update_case(
        self,
        project_id: str,
        case_id: str,
        *,
        title: str | None = None,
        risk_summary: str | None = None,
        materiality: str | None = None,
        owner: str | None = None,
        status: str | None = None,
        actor: str = "user",
        note: str = "",
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        changed_fields: list[str] = []
        transition: dict[str, str] = {}

        def mutate(case: dict[str, Any], _state: dict[str, Any]) -> None:
            nonlocal changed_fields, transition
            updates: dict[str, str] = {}
            if title is not None:
                clean_title = title.strip()
                if not clean_title:
                    raise ValueError("案件标题不能为空")
                updates["title"] = clean_title
            if risk_summary is not None:
                updates["risk_summary"] = risk_summary.strip()
            if materiality is not None:
                clean_materiality = materiality.strip().lower()
                if clean_materiality not in _CASE_MATERIALITY_LEVELS:
                    raise ValueError(f"不支持的重要程度：{materiality}")
                updates["materiality"] = clean_materiality
            if owner is not None:
                updates["owner"] = owner.strip()
            changed_fields = [
                field_name
                for field_name, value in updates.items()
                if case.get(field_name) != value
            ]
            if status == CASE_STATUS_CLOSED and changed_fields:
                raise ValueError("关闭案件时不能同时修改案件内容")
            if changed_fields:
                _material_change(
                    case,
                    reason="案件关键属性已修改，原结论与签署需重新复核。",
                    at=_now(),
                )
                case.update({
                    field_name: updates[field_name] for field_name in changed_fields
                })

            if status is None or status == case.get("status"):
                return
            target_status = status.strip()
            current_status = str(case.get("status") or CASE_STATUS_DRAFT)
            if case.get("kind") == CASE_KIND_PROPOSAL:
                if target_status != CASE_STATUS_CLOSED:
                    raise ValueError("案件建议需先立项，不能直接推进正式案件状态")
                if not note.strip():
                    raise ValueError("关闭案件建议需要记录原因")
            if target_status == CASE_STATUS_CONCLUDED:
                raise ValueError("请通过结论接口形成案件结论")
            if target_status == CASE_STATUS_CLOSED and case.get("kind") == CASE_KIND_FORMAL:
                self._validate_close(case)
            allowed = _CASE_STATUS_TRANSITIONS.get(current_status, set())
            if target_status not in allowed:
                raise ValueError(f"不允许的案件状态迁移：{current_status} → {target_status}")
            if current_status == CASE_STATUS_CONCLUDED and target_status != CASE_STATUS_CLOSED:
                _material_change(
                    case,
                    reason="案件重新开启，原结论与签署已失效。",
                    at=_now(),
                )
            transition = {"from_status": current_status, "to_status": target_status}
            case["status"] = target_status
            changed_fields.append("status")

        return self._mutate_case(
            project_id,
            case_id,
            actor=actor,
            event_type="case.status_changed" if status is not None else "case.updated",
            payload=lambda _case: {
                "fields": changed_fields,
                "note": note.strip(),
                **transition,
            },
            mutator=mutate,
            expected_version=expected_version,
        )

    def add_assertion(
        self,
        project_id: str,
        case_id: str,
        *,
        title: str,
        risk_statement: str,
        financial_statement_assertions: list[str] | None = None,
        affected_accounts: list[str] | None = None,
        actor: str = "user",
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        clean_title = title.strip()
        clean_risk = risk_statement.strip()
        if not clean_title or not clean_risk:
            raise ValueError("认定标题和风险陈述不能为空")
        assertion = Assertion(
            assertion_id=_new_id("asrt"),
            title=clean_title,
            risk_statement=clean_risk,
            financial_statement_assertions=list(financial_statement_assertions or []),
            affected_accounts=list(affected_accounts or []),
            created_by=actor,
        ).to_dict()

        def mutate(case: dict[str, Any], _state: dict[str, Any]) -> None:
            _material_change(
                case,
                reason="新增审计认定，原结论覆盖范围已改变。",
                at=_now(),
            )
            case["assertions"] = [*list(case.get("assertions") or []), assertion]
            self._mark_work_started(case)

        return self._mutate_case(
            project_id,
            case_id,
            actor=actor,
            event_type="case.assertion_added",
            payload={"assertion_id": assertion["assertion_id"]},
            mutator=mutate,
            expected_version=expected_version,
        )

    def update_assertion(
        self,
        project_id: str,
        case_id: str,
        assertion_id: str,
        *,
        status: str | None = None,
        title: str | None = None,
        risk_statement: str | None = None,
        actor: str = "user",
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        def mutate(case: dict[str, Any], _state: dict[str, Any]) -> None:
            assertion = self._find_child(case, "assertions", "assertion_id", assertion_id)
            if status is not None:
                clean_status = status.strip().lower()
                if clean_status not in _ASSERTION_STATUSES:
                    raise ValueError(f"不支持的认定状态：{status}")
                assertion["status"] = clean_status
            if title is not None:
                clean_title = title.strip()
                if not clean_title:
                    raise ValueError("认定标题不能为空")
                assertion["title"] = clean_title
            if risk_statement is not None:
                clean_risk = risk_statement.strip()
                if not clean_risk:
                    raise ValueError("风险陈述不能为空")
                assertion["risk_statement"] = clean_risk
            _material_change(
                case,
                reason="审计认定状态或内容已修改。",
                at=_now(),
            )
            self._mark_work_started(case)

        return self._mutate_case(
            project_id,
            case_id,
            actor=actor,
            event_type="case.assertion_updated",
            payload={"assertion_id": assertion_id, "status": status},
            mutator=mutate,
            expected_version=expected_version,
        )

    def add_evidence(
        self,
        project_id: str,
        case_id: str,
        *,
        source_type: str,
        source_ref: dict[str, Any],
        description: str = "",
        status: str = EVIDENCE_STATUS_ACTIVE,
        assertion_ids: list[str] | None = None,
        procedure_id: str = "",
        actor: str = "user",
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        clean_source_type = source_type.strip()
        clean_status = status.strip().lower() or EVIDENCE_STATUS_ACTIVE
        if not clean_source_type:
            raise ValueError("证据来源类型不能为空")
        if clean_source_type not in _SUPPORTED_EVIDENCE_SOURCE_TYPES:
            raise ValueError(f"不支持的证据来源类型：{clean_source_type}")
        if clean_status not in _EVIDENCE_STATUSES - {EVIDENCE_STATUS_STALE}:
            raise ValueError(f"不支持的证据状态：{status}")
        evidence_id = _new_id("evd")

        def mutate(case: dict[str, Any], state: dict[str, Any]) -> None:
            links = _assert_link_ids(case, assertion_ids)
            linked_procedure = procedure_id.strip()
            if linked_procedure:
                self._find_child(
                    case, "procedures", "procedure_id", linked_procedure
                )
            canonical = self._normalize_source_ref(
                project_id,
                source_type=clean_source_type,
                source_ref=source_ref,
                description=description,
                evidence_status=clean_status,
            )
            scope = _version_scope_from_state(
                self._store, project_id, state
            ).to_dict()
            evidence = EvidenceRef(
                evidence_id=evidence_id,
                source_type=clean_source_type,
                source_ref=canonical,
                description=description.strip(),
                status=clean_status,
                assertion_ids=links,
                procedure_id=linked_procedure,
                scope=scope,
                created_by=actor,
            ).to_dict()
            _material_change(
                case,
                reason="案件证据集合已改变，原结论与签署需重新复核。",
                at=_now(),
            )
            case["evidence_refs"] = [*list(case.get("evidence_refs") or []), evidence]
            case["version_scope"] = scope
            if clean_status in {EVIDENCE_STATUS_REQUESTED, EVIDENCE_STATUS_MISSING}:
                if case.get("kind") == CASE_KIND_FORMAL:
                    case["status"] = CASE_STATUS_PENDING_EVIDENCE
            else:
                self._mark_work_started(case)

        return self._mutate_case(
            project_id,
            case_id,
            actor=actor,
            event_type="case.evidence_added",
            payload={
                "evidence_id": evidence_id,
                "source_type": clean_source_type,
                "status": clean_status,
            },
            mutator=mutate,
            expected_version=expected_version,
        )

    def update_evidence(
        self,
        project_id: str,
        case_id: str,
        evidence_id: str,
        *,
        status: str | None = None,
        description: str | None = None,
        source_ref: dict[str, Any] | None = None,
        assertion_ids: list[str] | None = None,
        procedure_id: str | None = None,
        actor: str = "user",
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        def mutate(case: dict[str, Any], state: dict[str, Any]) -> None:
            evidence = self._find_child(
                case, "evidence_refs", "evidence_id", evidence_id
            )
            next_status = (
                status.strip().lower()
                if status is not None
                else str(evidence.get("status") or EVIDENCE_STATUS_ACTIVE)
            )
            if next_status not in _EVIDENCE_STATUSES:
                raise ValueError(f"不支持的证据状态：{next_status}")
            next_description = (
                description.strip()
                if description is not None
                else str(evidence.get("description") or "")
            )
            next_source_ref = (
                source_ref
                if source_ref is not None
                else dict(evidence.get("source_ref") or {})
            )
            canonical = self._normalize_source_ref(
                project_id,
                source_type=str(evidence.get("source_type") or ""),
                source_ref=next_source_ref,
                description=next_description,
                evidence_status=next_status,
            )
            if assertion_ids is not None:
                evidence["assertion_ids"] = _assert_link_ids(case, assertion_ids)
            if procedure_id is not None:
                clean_procedure = procedure_id.strip()
                if clean_procedure:
                    self._find_child(
                        case, "procedures", "procedure_id", clean_procedure
                    )
                evidence["procedure_id"] = clean_procedure
            evidence["status"] = next_status
            evidence["description"] = next_description
            evidence["source_ref"] = canonical
            evidence["scope"] = _version_scope_from_state(
                self._store, project_id, state
            ).to_dict()
            if next_status != EVIDENCE_STATUS_STALE:
                evidence["stale_reason"] = ""
                evidence["stale_at"] = ""
            _material_change(
                case,
                reason="证据状态、内容或关联已修改。",
                at=_now(),
            )
            if next_status in {
                EVIDENCE_STATUS_REQUESTED,
                EVIDENCE_STATUS_MISSING,
                EVIDENCE_STATUS_STALE,
            }:
                if case.get("kind") == CASE_KIND_FORMAL:
                    case["status"] = CASE_STATUS_PENDING_EVIDENCE
            else:
                self._mark_work_started(case)

        return self._mutate_case(
            project_id,
            case_id,
            actor=actor,
            event_type="case.evidence_updated",
            payload={"evidence_id": evidence_id, "status": status},
            mutator=mutate,
            expected_version=expected_version,
        )

    def add_procedure(
        self,
        project_id: str,
        case_id: str,
        *,
        title: str,
        description: str,
        assertion_ids: list[str] | None = None,
        status: str = "planned",
        result: str = "",
        performed_by: str = "",
        performed_at: str = "",
        actor: str = "user",
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        clean_title = title.strip()
        clean_description = description.strip()
        clean_status = status.strip().lower() or "planned"
        if not clean_title or not clean_description:
            raise ValueError("程序标题和描述不能为空")
        self._validate_procedure_completion(
            clean_status, result.strip(), performed_by.strip()
        )
        procedure_id = _new_id("proc")

        def mutate(case: dict[str, Any], _state: dict[str, Any]) -> None:
            links = _assert_link_ids(case, assertion_ids)
            procedure = Procedure(
                procedure_id=procedure_id,
                title=clean_title,
                description=clean_description,
                assertion_ids=links,
                status=clean_status,
                result=result.strip(),
                performed_by=performed_by.strip(),
                performed_at=performed_at.strip() or (
                    _now() if clean_status == "completed" else ""
                ),
                created_by=actor,
            ).to_dict()
            _material_change(
                case,
                reason="审计程序集合已改变，原结论与签署需重新复核。",
                at=_now(),
            )
            case["procedures"] = [*list(case.get("procedures") or []), procedure]
            self._mark_work_started(case)

        return self._mutate_case(
            project_id,
            case_id,
            actor=actor,
            event_type="case.procedure_added",
            payload={"procedure_id": procedure_id, "status": clean_status},
            mutator=mutate,
            expected_version=expected_version,
        )

    def update_procedure(
        self,
        project_id: str,
        case_id: str,
        procedure_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        assertion_ids: list[str] | None = None,
        status: str | None = None,
        result: str | None = None,
        performed_by: str | None = None,
        performed_at: str | None = None,
        actor: str = "user",
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        def mutate(case: dict[str, Any], _state: dict[str, Any]) -> None:
            procedure = self._find_child(
                case, "procedures", "procedure_id", procedure_id
            )
            if title is not None:
                clean_title = title.strip()
                if not clean_title:
                    raise ValueError("程序标题不能为空")
                procedure["title"] = clean_title
            if description is not None:
                clean_description = description.strip()
                if not clean_description:
                    raise ValueError("程序描述不能为空")
                procedure["description"] = clean_description
            if assertion_ids is not None:
                procedure["assertion_ids"] = _assert_link_ids(case, assertion_ids)
            next_status = (
                status.strip().lower()
                if status is not None
                else str(procedure.get("status") or "planned")
            )
            next_result = (
                result.strip()
                if result is not None
                else str(procedure.get("result") or "")
            )
            next_performer = (
                performed_by.strip()
                if performed_by is not None
                else str(procedure.get("performed_by") or "")
            )
            self._validate_procedure_completion(
                next_status, next_result, next_performer
            )
            procedure["status"] = next_status
            procedure["result"] = next_result
            procedure["performed_by"] = next_performer
            if performed_at is not None:
                procedure["performed_at"] = performed_at.strip()
            elif next_status == "completed" and not procedure.get("performed_at"):
                procedure["performed_at"] = _now()
            _material_change(
                case,
                reason="审计程序状态、结果或关联已修改。",
                at=_now(),
            )
            self._mark_work_started(case)

        return self._mutate_case(
            project_id,
            case_id,
            actor=actor,
            event_type="case.procedure_updated",
            payload={"procedure_id": procedure_id, "status": status},
            mutator=mutate,
            expected_version=expected_version,
        )

    def set_conclusion(
        self,
        project_id: str,
        case_id: str,
        *,
        outcome: str,
        summary: str,
        basis_evidence_ids: list[str] | None = None,
        procedure_ids: list[str] | None = None,
        unresolved_assertion_ids: list[str] | None = None,
        override_reason: str = "",
        misstatement_amount: float | None = None,
        currency: str = "",
        actor: str = "user",
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        clean_outcome = outcome.strip()
        clean_summary = summary.strip()
        clean_actor = actor.strip()
        if not clean_outcome or not clean_summary:
            raise ValueError("结论类型和结论摘要不能为空")
        if not clean_actor:
            raise ValueError("结论编制人不能为空")
        if clean_outcome not in _CONCLUSION_OUTCOMES:
            raise ValueError(f"不支持的结论类型：{clean_outcome}")
        if misstatement_amount is not None and not currency.strip():
            raise ValueError("记录错报金额时必须同时记录币种")
        if clean_outcome == "misstatement" and misstatement_amount is None:
            raise ValueError("错报结论必须记录错报金额和币种")
        pending = clean_outcome in _CONCLUSION_PENDING_OUTCOMES

        def mutate(case: dict[str, Any], state: dict[str, Any]) -> None:
            if case.get("kind") != CASE_KIND_FORMAL:
                raise ValueError("案件建议需先立项，不能直接形成结论")
            assertions = [
                item
                for item in case.get("assertions") or []
                if isinstance(item, dict)
            ]
            if not assertions:
                raise ValueError("至少需要一项审计认定，才能形成结论")
            evidence = {
                str(item.get("evidence_id") or ""): item
                for item in case.get("evidence_refs") or []
                if isinstance(item, dict) and str(item.get("evidence_id") or "")
            }
            procedures = {
                str(item.get("procedure_id") or ""): item
                for item in case.get("procedures") or []
                if isinstance(item, dict) and str(item.get("procedure_id") or "")
            }
            default_basis = [
                evidence_id
                for evidence_id, item in evidence.items()
                if item.get("status") == EVIDENCE_STATUS_VERIFIED
                and str(item.get("source_type") or "")
                not in _NON_SUBSTANTIVE_EVIDENCE_TYPES
            ]
            default_procedures = [
                procedure_id
                for procedure_id, item in procedures.items()
                if item.get("status") == "completed"
            ]
            selected_evidence = list(dict.fromkeys(
                str(value)
                for value in (
                    default_basis if basis_evidence_ids is None else basis_evidence_ids
                )
                if str(value)
            ))
            selected_procedures = list(dict.fromkeys(
                str(value)
                for value in (
                    default_procedures if procedure_ids is None else procedure_ids
                )
                if str(value)
            ))
            assertion_id_set = {
                str(item.get("assertion_id") or "")
                for item in assertions
                if str(item.get("assertion_id") or "")
            }
            default_unresolved = [
                str(item.get("assertion_id") or "")
                for item in assertions
                if str(item.get("status") or "open") == "open"
            ]
            selected_unresolved = list(dict.fromkeys(
                str(value)
                for value in (
                    default_unresolved
                    if unresolved_assertion_ids is None
                    else unresolved_assertion_ids
                )
                if str(value)
            ))
            missing_evidence = sorted(set(selected_evidence) - set(evidence))
            missing_procedures = sorted(set(selected_procedures) - set(procedures))
            missing_assertions = sorted(set(selected_unresolved) - assertion_id_set)
            if missing_evidence:
                raise ValueError(f"结论引用了不存在的证据：{missing_evidence}")
            if missing_procedures:
                raise ValueError(f"结论引用了不存在的程序：{missing_procedures}")
            if missing_assertions:
                raise ValueError(f"结论引用了不存在的认定：{missing_assertions}")

            if pending:
                if not selected_unresolved:
                    raise ValueError("证据不足或范围受限结论必须明确至少一项未解决认定")
                assertion_status = {
                    str(item.get("assertion_id") or ""): str(
                        item.get("status") or "open"
                    )
                    for item in assertions
                }
                resolved = [
                    assertion_id
                    for assertion_id in selected_unresolved
                    if assertion_status.get(assertion_id) != "open"
                ]
                if resolved:
                    raise ValueError(
                        f"待解决认定必须保持 open 状态，请先重新打开认定：{resolved}"
                    )
            else:
                readiness = _case_readiness(case)
                if not readiness["ready"]:
                    messages = "；".join(
                        str(item.get("message") or item.get("code"))
                        for item in readiness["blockers"]
                    )
                    raise ValueError(f"尚不满足形成实质性结论的条件：{messages}")
                assertion_statuses = {
                    str(item.get("assertion_id") or ""): str(
                        item.get("status") or "open"
                    )
                    for item in assertions
                }
                if clean_outcome == "no_exception" and any(
                    status != "supported"
                    for status in assertion_statuses.values()
                ):
                    raise ValueError(
                        "“未发现异常”结论要求所有认定均判断为已支持"
                    )
                if (
                    clean_outcome in _EXCEPTION_BEARING_OUTCOMES
                    and "exception" not in assertion_statuses.values()
                ):
                    raise ValueError(
                        "该结论类型要求至少一项认定判断为存在例外"
                    )
                invalid_basis = [
                    evidence_id
                    for evidence_id in selected_evidence
                    if evidence[evidence_id].get("status") != EVIDENCE_STATUS_VERIFIED
                    or str(evidence[evidence_id].get("source_type") or "")
                    in _NON_SUBSTANTIVE_EVIDENCE_TYPES
                ]
                invalid_procedures = [
                    procedure_id
                    for procedure_id in selected_procedures
                    if procedures[procedure_id].get("status") != "completed"
                ]
                if not selected_evidence or invalid_basis:
                    raise ValueError("结论依据必须包含已核验的实质性证据")
                if not selected_procedures or invalid_procedures:
                    raise ValueError("结论依据必须包含已完成的审计程序")
                if selected_unresolved:
                    raise ValueError("实质性结论不能保留未解决认定；请改用证据不足或范围受限")
                for assertion_id in assertion_id_set:
                    if not any(
                        assertion_id
                        in {
                            str(value)
                            for value in evidence[evidence_id].get("assertion_ids") or []
                        }
                        for evidence_id in selected_evidence
                    ):
                        raise ValueError(f"结论证据未覆盖认定：{assertion_id}")
                    if not any(
                        assertion_id
                        in {
                            str(value)
                            for value in procedures[procedure_id].get("assertion_ids") or []
                        }
                        for procedure_id in selected_procedures
                    ):
                        raise ValueError(f"结论程序未覆盖认定：{assertion_id}")

            now = _now()
            _mark_signoffs_stale(
                case,
                reason="案件结论已重新形成。",
                at=now,
            )
            conclusion = Conclusion(
                outcome=clean_outcome,
                summary=clean_summary,
                basis_evidence_ids=selected_evidence,
                procedure_ids=selected_procedures,
                unresolved_assertion_ids=selected_unresolved,
                override_reason=override_reason.strip(),
                misstatement_amount=misstatement_amount,
                currency=currency.strip(),
                concluded_by=clean_actor,
                concluded_at=now,
            ).to_dict()
            case["conclusion"] = conclusion
            case["version_scope"] = _version_scope_from_state(
                self._store, project_id, state
            ).to_dict()
            case["status"] = (
                CASE_STATUS_PENDING_EVIDENCE if pending else CASE_STATUS_CONCLUDED
            )

        return self._mutate_case(
            project_id,
            case_id,
            actor=clean_actor,
            event_type="case.pending_evidence" if pending else "case.concluded",
            payload={
                "outcome": clean_outcome,
                "misstatement_amount": misstatement_amount,
            },
            mutator=mutate,
            expected_version=expected_version,
        )

    def record_review(
        self,
        project_id: str,
        case_id: str,
        *,
        action: str,
        actor: str,
        note: str = "",
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        aliases = {
            "review": "reviewed",
            "reviewed": "reviewed",
            "signoff": "signed_off",
            "signed_off": "signed_off",
        }
        normalized = aliases.get(action.strip().lower())
        if normalized is None:
            raise ValueError(f"不支持的复核事件：{action}")
        clean_actor = actor.strip()
        if not clean_actor:
            raise ValueError("复核人不能为空")

        def mutate(case: dict[str, Any], _state: dict[str, Any]) -> None:
            if case.get("kind") != CASE_KIND_FORMAL:
                raise ValueError("案件建议需先立项，才能记录复核事件")
            now = _now()
            if normalized == "reviewed":
                if not note.strip():
                    raise ValueError("记录复核事件必须填写复核意见")
                case["reviews"] = [
                    *list(case.get("reviews") or []),
                    {
                        "review_id": _new_id("rev"),
                        "actor": clean_actor,
                        "note": note.strip(),
                        "reviewed_at": now,
                        "case_version_reviewed": int(case.get("version") or 1),
                    },
                ]
                return
            conclusion = case.get("conclusion")
            if (
                case.get("status") != CASE_STATUS_CONCLUDED
                or not isinstance(conclusion, dict)
                or conclusion.get("status") != "active"
                or conclusion.get("outcome") in _CONCLUSION_PENDING_OUTCOMES
            ):
                raise ValueError("只有有效的实质性结论才能签署")
            if not note.strip():
                raise ValueError("独立复核签署必须记录复核意见")
            prohibited_actors = {
                str(case.get("created_by") or "").strip().casefold(),
                str(conclusion.get("concluded_by") or "").strip().casefold(),
            }
            if clean_actor.casefold() in prohibited_actors:
                raise ValueError("复核签署人必须独立于案件创建人和结论编制人")
            readiness = _case_readiness(case)
            if not readiness["ready"]:
                raise ValueError("案件证据链不完整，不能签署")
            _mark_signoffs_stale(case, reason="由新的独立签署替代。", at=now)
            case["signoffs"] = [
                *list(case.get("signoffs") or []),
                {
                    "signoff_id": _new_id("sign"),
                    "actor": clean_actor,
                    "note": note.strip(),
                    "signed_at": now,
                    "status": "active",
                    "conclusion_digest": _digest(conclusion),
                    "signed_case_version": int(case.get("version") or 1) + 1,
                    "stale_reason": "",
                    "stale_at": "",
                },
            ]

        return self._mutate_case(
            project_id,
            case_id,
            actor=clean_actor,
            event_type=f"case.{normalized}",
            payload={"note": note.strip()},
            mutator=mutate,
            expected_version=expected_version,
        )

    def list_events(self, project_id: str, case_id: str) -> list[dict[str, Any]]:
        state = self._store.load_state(project_id)
        raw = state.get(AUDIT_CASE_STATE_KEY) or {}
        if not isinstance((raw.get("cases") or {}).get(case_id), dict):
            raise KeyError(f"审计案件不存在：{case_id}")
        return _json_clone(_case_records(raw, "events", case_id))

    def list_snapshots(self, project_id: str, case_id: str) -> list[dict[str, Any]]:
        state = self._store.load_state(project_id)
        raw = state.get(AUDIT_CASE_STATE_KEY) or {}
        if not isinstance((raw.get("cases") or {}).get(case_id), dict):
            raise KeyError(f"审计案件不存在：{case_id}")
        return _json_clone(_case_records(raw, "snapshots", case_id))

    def verify_event_chain(self, project_id: str, case_id: str) -> dict[str, Any]:
        self.get_case(project_id, case_id)
        return verify_case_integrity_in_state(
            self._store.load_state(project_id), case_id
        )

    def _mutate_case(
        self,
        project_id: str,
        case_id: str,
        *,
        actor: str,
        event_type: str,
        payload: dict[str, Any] | Callable[[dict[str, Any]], dict[str, Any]],
        mutator: Callable[[dict[str, Any], dict[str, Any]], None],
        expected_version: int | None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        result_event: dict[str, Any] = {}
        result_scope: dict[str, Any] = {}

        def update(state: dict[str, Any]) -> None:
            nonlocal result, result_event, result_scope
            container = _container(state)
            case = container["cases"].get(case_id)
            if not isinstance(case, dict):
                raise KeyError(f"审计案件不存在：{case_id}")
            self._check_expected_version(case, expected_version)
            integrity = verify_case_integrity_in_state(state, case_id)
            if not integrity["valid"]:
                raise ValueError(
                    "案件完整性校验失败，禁止继续修改、结论或签署；"
                    "请先保全当前项目并复核事件、快照与案件记录。"
                )
            if case.get("status") == CASE_STATUS_CLOSED:
                raise ValueError("已关闭案件不可再修改")
            before = _json_clone(case)
            mutator(case, state)
            result_scope = _version_scope_from_state(
                self._store, project_id, state
            ).to_dict()
            if case == before:
                result = case
                return
            now = _now()
            case["version"] = int(before.get("version") or 1) + 1
            case["updated_at"] = now
            event_payload = payload(case) if callable(payload) else payload
            result_event = _record_change(
                container,
                case,
                event_type,
                actor=actor,
                payload=event_payload,
                at=now,
            )
            result = case

        self._store.update_state(project_id, update)
        return self._result(result, result_scope, result_event)

    def _result(
        self,
        case: dict[str, Any],
        current_scope: dict[str, Any],
        event: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "case": _json_clone(case),
            "current_scope": _json_clone(current_scope),
            "readiness": _case_readiness(case),
            "event": _json_clone(event or {}),
        }

    @staticmethod
    def _check_expected_version(
        case: dict[str, Any], expected_version: int | None
    ) -> None:
        if expected_version is None:
            return
        current = int(case.get("version") or 1)
        if int(expected_version) != current:
            raise CaseVersionConflict(
                f"案件版本冲突：当前为 {current}，请求基于 {expected_version}；"
                "请刷新后重试。"
            )

    @staticmethod
    def _find_child(
        case: dict[str, Any],
        collection: str,
        id_field: str,
        child_id: str,
    ) -> dict[str, Any]:
        child = next(
            (
                item
                for item in case.get(collection) or []
                if isinstance(item, dict)
                and str(item.get(id_field) or "") == child_id
            ),
            None,
        )
        if child is None:
            raise KeyError(f"案件子记录不存在：{child_id}")
        return child

    @staticmethod
    def _mark_work_started(case: dict[str, Any]) -> None:
        if case.get("kind") != CASE_KIND_FORMAL:
            return
        if case.get("status") in {
            CASE_STATUS_PLANNED,
            CASE_STATUS_PENDING_EVIDENCE,
            CASE_STATUS_CONCLUDED,
        }:
            case["status"] = CASE_STATUS_IN_PROGRESS

    @staticmethod
    def _validate_procedure_completion(
        status: str,
        result: str,
        performed_by: str,
    ) -> None:
        if status not in _PROCEDURE_STATUSES:
            raise ValueError(f"不支持的程序状态：{status}")
        if status == "completed" and (not result or not performed_by):
            raise ValueError("完成审计程序时必须记录执行人和程序结果")

    @staticmethod
    def _validate_close(case: dict[str, Any]) -> None:
        if case.get("status") != CASE_STATUS_CONCLUDED:
            raise ValueError("案件必须先形成有效结论才能关闭")
        conclusion = case.get("conclusion")
        if not isinstance(conclusion, dict) or conclusion.get("status") != "active":
            raise ValueError("案件没有有效结论，不能关闭")
        active = [
            item
            for item in case.get("signoffs") or []
            if isinstance(item, dict) and item.get("status") == "active"
        ]
        if not active:
            raise ValueError("案件必须经过独立复核签署后才能关闭")
        signoff = active[-1]
        if signoff.get("conclusion_digest") != _digest(conclusion):
            raise ValueError("复核签署对应的结论已变化，请重新签署")
        if int(signoff.get("signed_case_version") or 0) != int(case.get("version") or 0):
            raise ValueError("签署后案件又发生变化，请重新签署")

    def _candidate_voucher_keys(
        self,
        project_id: str,
        group: dict[str, Any],
        voucher_ids: list[str],
    ) -> list[str]:
        keys = {
            str(value)
            for value in group.get("voucher_keys") or []
            if str(value)
        }
        if keys or not voucher_ids:
            return sorted(keys)
        wanted = set(voucher_ids)
        for year in self._store.load_manifest(project_id).years:
            try:
                work = self._store.get_work_df(project_id, year)
            except KeyError:
                work = self._store.load_journal_year(project_id, year)
            if work.empty or "_voucher_key" not in work.columns:
                continue
            display_column = (
                "_voucher_id" if "_voucher_id" in work.columns else "凭证编号"
            )
            if display_column not in work.columns:
                continue
            matched = work.loc[
                work[display_column].astype(str).isin(wanted), "_voucher_key"
            ]
            keys.update(str(value) for value in matched if str(value))
        return sorted(keys)

    def _normalize_source_ref(
        self,
        project_id: str,
        *,
        source_type: str,
        source_ref: dict[str, Any],
        description: str,
        evidence_status: str,
    ) -> dict[str, Any]:
        if source_type not in _SUPPORTED_EVIDENCE_SOURCE_TYPES:
            raise ValueError(f"不支持的证据来源类型：{source_type}")
        raw_locator = source_ref.get("locator")
        canonical = SourceRef(
            source_asset_id=str(source_ref.get("source_asset_id") or "").strip(),
            file_hash=str(source_ref.get("file_hash") or "").strip(),
            sheet=str(source_ref.get("sheet") or "").strip(),
            source_row=source_ref.get("source_row"),
            source_column=str(source_ref.get("source_column") or "").strip(),
            voucher_key=str(source_ref.get("voucher_key") or "").strip(),
            line_key=str(source_ref.get("line_key") or "").strip(),
            locator=_json_clone(raw_locator if isinstance(raw_locator, dict) else {}),
        ).to_dict()
        if canonical["source_row"] is not None:
            try:
                canonical["source_row"] = int(canonical["source_row"])
            except (TypeError, ValueError) as exc:
                raise ValueError("来源行号必须为正整数") from exc
            if canonical["source_row"] < 1:
                raise ValueError("来源行号必须为正整数")

        if evidence_status in {EVIDENCE_STATUS_REQUESTED, EVIDENCE_STATUS_MISSING}:
            if not description.strip():
                raise ValueError("待获取或缺失证据必须说明所需内容")
            return canonical

        asset = None
        if canonical["source_asset_id"]:
            from audit_engine.source_assets import SourceAsset, read_source_asset

            manifest = self._store.load_manifest(project_id)
            raw_asset = next(
                (
                    item
                    for source in manifest.sources
                    for item in source.assets
                    if isinstance(item, dict)
                    and item.get("asset_id") == canonical["source_asset_id"]
                ),
                None,
            )
            if raw_asset is None:
                raise ValueError("证据引用的来源资产不存在")
            asset = SourceAsset.from_dict(raw_asset)
            read_source_asset(self._store.project_dir(project_id), asset)
            if canonical["file_hash"] and canonical["file_hash"] != asset.sha256:
                raise ValueError("证据文件哈希与来源资产不一致")
            canonical["file_hash"] = asset.sha256
        elif canonical["file_hash"]:
            raise ValueError("仅提供文件哈希无法回查原始来源资产")

        if canonical["source_row"] is not None and asset is None:
            raise ValueError("仅提供行号不是稳定证据坐标；必须同时关联来源资产")
        if canonical["source_row"] is not None and not canonical["sheet"]:
            raise ValueError("来源行坐标必须同时记录工作表名称")

        matched_row: dict[str, Any] | None = None
        if canonical["voucher_key"] or canonical["line_key"]:
            for year in self._store.load_manifest(project_id).years:
                try:
                    work = self._store.get_work_df(project_id, year)
                except KeyError:
                    work = self._store.load_journal_year(project_id, year)
                if work.empty:
                    continue
                mask = None
                if canonical["line_key"]:
                    if "_line_key" not in work.columns:
                        continue
                    mask = work["_line_key"].astype(str).eq(canonical["line_key"])
                elif canonical["voucher_key"]:
                    if "_voucher_key" not in work.columns:
                        continue
                    mask = work["_voucher_key"].astype(str).eq(
                        canonical["voucher_key"]
                    )
                matched = work.loc[mask]
                if not matched.empty:
                    matched_row = matched.iloc[0].to_dict()
                    break
            if matched_row is None:
                raise ValueError("凭证键或行项目键无法在当前事实层中回查")
            row_asset_id = str(matched_row.get("_source_asset_id") or "")
            if (
                canonical["source_asset_id"]
                and row_asset_id
                and canonical["source_asset_id"] != row_asset_id
            ):
                raise ValueError("凭证事实与所选来源资产不一致")
            canonical["voucher_key"] = (
                canonical["voucher_key"]
                or str(matched_row.get("_voucher_key") or "")
            )
            canonical["line_key"] = (
                canonical["line_key"] or str(matched_row.get("_line_key") or "")
            )
            canonical["source_asset_id"] = (
                canonical["source_asset_id"] or row_asset_id
            )
            canonical["file_hash"] = (
                canonical["file_hash"]
                or str(matched_row.get("_source_file_hash") or "")
            )
            canonical["sheet"] = (
                canonical["sheet"] or str(matched_row.get("_source_sheet") or "")
            )
            if canonical["source_row"] is None:
                raw_row = matched_row.get("_source_row")
                try:
                    canonical["source_row"] = (
                        int(raw_row) if raw_row is not None and str(raw_row) else None
                    )
                except (TypeError, ValueError):
                    canonical["source_row"] = None

        if source_type == "attachment" and asset is None:
            raise ValueError("附件证据必须关联系统保存且已校验的来源资产")
        if source_type in {"management_explanation", "counter_evidence"}:
            if not canonical["locator"] or not description.strip():
                raise ValueError("说明类证据必须记录说明内容和可追踪定位信息")
        elif source_type in {"journal", "source_coordinate"}:
            has_fact_key = bool(canonical["voucher_key"] or canonical["line_key"])
            has_source_coordinate = bool(
                canonical["source_asset_id"]
                and canonical["sheet"]
                and canonical["source_row"] is not None
            )
            if not (has_fact_key or has_source_coordinate):
                raise ValueError("序时账证据必须关联可回查的凭证键、行项目键或来源坐标")
        elif not (
            asset
            or canonical["voucher_key"]
            or canonical["line_key"]
            or canonical["locator"]
        ):
            raise ValueError("证据来源引用至少需要一个可验证的稳定坐标")
        return canonical
