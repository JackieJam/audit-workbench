"""AuditCase API — versioned assertions, evidence, procedures and conclusions."""

from __future__ import annotations

from typing import Any, Literal

from audit_engine.audit_case import AuditCaseService, CaseVersionConflict
from audit_engine.store import ProjectStore
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from audit_api.deps import get_store
from audit_api.routers.analysis import _manifest_or_404

router = APIRouter(prefix="/projects", tags=["audit-cases"])


class CreateCaseRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    risk: str = Field("", max_length=2000)
    risk_statement: str = Field("", max_length=2000)
    financial_statement_assertions: list[str] = Field(default_factory=list, max_length=20)
    affected_accounts: list[str] = Field(default_factory=list, max_length=200)
    risk_domain: str = Field("", max_length=100)
    materiality: Literal["unassessed", "low", "medium", "high"] = "unassessed"
    proposal: bool = True
    owner: str = Field("", max_length=100)
    tags: list[str] = Field(default_factory=list, max_length=50)
    actor: str = Field("user", min_length=1, max_length=100)


class CandidateCaseRequest(BaseModel):
    formal: bool = False
    title: str = Field("", max_length=200)
    risk: str = Field("", max_length=2000)
    risk_statement: str = Field("", max_length=2000)
    financial_statement_assertions: list[str] = Field(default_factory=list, max_length=20)
    materiality: Literal["unassessed", "low", "medium", "high"] = "unassessed"
    owner: str = Field("", max_length=100)
    actor: str = Field("user", min_length=1, max_length=100)
    expected_version: int | None = Field(None, ge=1)


class PromoteCaseRequest(BaseModel):
    actor: str = Field("user", min_length=1, max_length=100)
    comment: str = Field("", max_length=1000)
    expected_version: int = Field(..., ge=1)


class AssertionRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    risk_statement: str = Field(..., min_length=1, max_length=2000)
    financial_statement_assertions: list[str] = Field(default_factory=list, max_length=20)
    affected_accounts: list[str] = Field(default_factory=list, max_length=200)
    actor: str = Field("user", min_length=1, max_length=100)
    expected_version: int = Field(..., ge=1)


class AssertionPatch(BaseModel):
    status: Literal["open", "supported", "exception"] | None = None
    title: str | None = Field(None, min_length=1, max_length=200)
    risk_statement: str | None = Field(None, min_length=1, max_length=2000)
    actor: str = Field("user", min_length=1, max_length=100)
    expected_version: int = Field(..., ge=1)


class SourceRefRequest(BaseModel):
    source_asset_id: str = Field("", max_length=100)
    file_hash: str = Field("", max_length=128)
    sheet: str = Field("", max_length=200)
    source_row: int | None = Field(None, ge=1)
    source_column: str = Field("", max_length=100)
    voucher_key: str = Field("", max_length=500)
    line_key: str = Field("", max_length=500)
    locator: dict[str, Any] = Field(default_factory=dict)


class EvidenceRequest(BaseModel):
    source_type: str = Field(..., min_length=1, max_length=100)
    source_ref: SourceRefRequest
    description: str = Field("", max_length=2000)
    status: Literal["active", "verified", "requested", "missing"] = "active"
    assertion_ids: list[str] = Field(default_factory=list, max_length=100)
    procedure_id: str = Field("", max_length=100)
    actor: str = Field("user", min_length=1, max_length=100)
    expected_version: int = Field(..., ge=1)


class EvidencePatch(BaseModel):
    status: Literal["active", "verified", "stale", "requested", "missing"] | None = None
    description: str | None = Field(None, max_length=2000)
    source_ref: SourceRefRequest | None = None
    assertion_ids: list[str] | None = Field(None, max_length=100)
    procedure_id: str | None = Field(None, max_length=100)
    actor: str = Field("user", min_length=1, max_length=100)
    expected_version: int = Field(..., ge=1)


class ProcedureRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., min_length=1, max_length=4000)
    assertion_ids: list[str] = Field(default_factory=list, max_length=100)
    status: Literal["planned", "in_progress", "completed", "not_applicable"] = "planned"
    result: str = Field("", max_length=4000)
    performed_by: str = Field("", max_length=100)
    performed_at: str = Field("", max_length=50)
    actor: str = Field("user", min_length=1, max_length=100)
    expected_version: int = Field(..., ge=1)


class ProcedurePatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, min_length=1, max_length=4000)
    assertion_ids: list[str] | None = Field(None, max_length=100)
    status: Literal["planned", "in_progress", "completed", "not_applicable"] | None = None
    result: str | None = Field(None, max_length=4000)
    performed_by: str | None = Field(None, max_length=100)
    performed_at: str | None = Field(None, max_length=50)
    actor: str = Field("user", min_length=1, max_length=100)
    expected_version: int = Field(..., ge=1)


class ConclusionRequest(BaseModel):
    outcome: Literal[
        "no_exception",
        "reasonable_exception",
        "exception",
        "finding",
        "insufficient_evidence",
        "control_deficiency",
        "misstatement",
        "scope_limitation",
    ]
    summary: str = Field(..., min_length=1, max_length=4000)
    basis_evidence_ids: list[str] = Field(default_factory=list, max_length=200)
    procedure_ids: list[str] = Field(default_factory=list, max_length=200)
    unresolved_assertion_ids: list[str] = Field(default_factory=list, max_length=200)
    override_reason: str = Field("", max_length=2000)
    misstatement_amount: float | None = None
    currency: str = Field("", max_length=12)
    actor: str = Field(..., min_length=1, max_length=100)
    expected_version: int = Field(..., ge=1)


class UpdateCaseRequest(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    risk: str | None = Field(None, max_length=2000)
    materiality: Literal["unassessed", "low", "medium", "high"] | None = None
    owner: str | None = Field(None, max_length=100)
    status: Literal[
        "draft",
        "planned",
        "in_progress",
        "pending_evidence",
        "concluded",
        "closed",
    ] | None = None
    actor: str = Field("user", min_length=1, max_length=100)
    note: str = Field("", max_length=1000)
    expected_version: int = Field(..., ge=1)


class AppendCaseEventRequest(BaseModel):
    event_type: Literal["review", "reviewed", "signoff", "signed_off"]
    actor: str = Field(..., min_length=1, max_length=100)
    note: str = Field("", max_length=2000)
    expected_version: int = Field(..., ge=1)


def _service(store: ProjectStore, project_id: str) -> AuditCaseService:
    _manifest_or_404(store, project_id)
    return AuditCaseService(store)


def _case_call(call: Any) -> Any:
    try:
        return call()
    except CaseVersionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc.args[0])) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _present_event(event: dict[str, Any]) -> dict[str, Any]:
    canonical_type = str(event.get("event_type") or "")
    event_type = {
        "case.created": "created",
        "case.proposed": "created",
        "case.promoted": "status_changed",
        "case.updated": "updated",
        "case.status_changed": "status_changed",
        "case.assertion_added": "updated",
        "case.assertion_updated": "updated",
        "case.evidence_added": "updated",
        "case.evidence_updated": "updated",
        "case.procedure_added": "updated",
        "case.procedure_updated": "updated",
        "case.evidence_stale": "stale",
        "case.pending_evidence": "status_changed",
        "case.concluded": "concluded",
        "case.reviewed": "reviewed",
        "case.signed_off": "reviewed",
    }.get(canonical_type, "updated")
    payload = dict(event.get("payload") or {})
    return {
        **event,
        "canonical_event_type": canonical_type,
        "event_type": event_type,
        "note": str(
            payload.get("note")
            or payload.get("comment")
            or payload.get("reason")
            or ""
        ),
        "from_status": payload.get("from_status"),
        "to_status": payload.get("to_status"),
    }


def _present_case(
    service: AuditCaseService,
    project_id: str,
    case: dict[str, Any],
) -> dict[str, Any]:
    assertions = [
        {
            **assertion,
            "name": assertion.get("title") or "",
            "rationale": assertion.get("risk_statement") or "",
        }
        for assertion in case.get("assertions") or []
        if isinstance(assertion, dict)
    ]
    evidence: list[dict[str, Any]] = []
    for item in case.get("evidence_refs") or []:
        if not isinstance(item, dict):
            continue
        source_type = str(item.get("source_type") or "")
        evidence_type = {
            "candidate_group": "risk_signal",
            "rule_hit": "risk_signal",
            "module_insight": "risk_signal",
            "query_result": "risk_signal",
            "attachment": "attachment",
            "management_explanation": "management_explanation",
            "counter_evidence": "counter_evidence",
        }.get(source_type, "source_coordinate")
        evidence.append({
            **item,
            "evidence_type": evidence_type,
            "title": item.get("description") or source_type or "审计证据",
            "status": item.get("status") or "active",
            "locator": item.get("source_ref") or {},
            "note": item.get("stale_reason") or item.get("description") or "",
        })
    procedures = [
        {
            **procedure,
            "owner": procedure.get("performed_by") or "",
        }
        for procedure in case.get("procedures") or []
        if isinstance(procedure, dict)
    ]
    scope = dict(case.get("version_scope") or {})
    events = [_present_event(event) for event in service.list_events(project_id, case["case_id"])]
    readiness = service.case_readiness(project_id, case["case_id"])
    integrity = service.verify_event_chain(project_id, case["case_id"])
    return {
        **case,
        "risk": case.get("risk_summary") or "",
        "source_candidate_ids": (
            [case["source_candidate_group_id"]]
            if case.get("source_candidate_group_id")
            else []
        ),
        "assertions": assertions,
        "evidence": evidence,
        "procedures": procedures,
        "conclusion": case.get("conclusion")
        or {"outcome": "pending", "summary": "尚未形成审计结论。"},
        "snapshot": {
            **scope,
            "rule_version": scope.get("rule_revision") or "",
        },
        "readiness": readiness,
        "integrity": integrity,
        "events": events,
    }


def _present_result(
    service: AuditCaseService,
    project_id: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    return _present_case(service, project_id, result["case"])


@router.get("/{project_id}/audit-cases")
def list_audit_cases(
    project_id: str,
    status: str = Query(""),
    kind: str = Query("", pattern="^(|proposal|case)$"),
    store: ProjectStore = Depends(get_store),
) -> dict[str, Any]:
    service = _service(store, project_id)
    result = service.list_cases(project_id, status=status, kind=kind)
    cases = [_present_case(service, project_id, case) for case in result["cases"]]
    return {
        **result,
        "cases": cases,
        "count": len(cases),
        "total": len(cases),
    }


@router.post("/{project_id}/audit-cases")
def create_audit_case(
    project_id: str,
    body: CreateCaseRequest,
    store: ProjectStore = Depends(get_store),
) -> dict[str, Any]:
    service = _service(store, project_id)
    result = _case_call(
        lambda: service.create_case(
            project_id,
            title=body.title,
            risk_statement=body.risk or body.risk_statement,
            financial_statement_assertions=body.financial_statement_assertions,
            affected_accounts=body.affected_accounts,
            risk_domain=body.risk_domain,
            materiality=body.materiality,
            proposal=body.proposal,
            owner=body.owner,
            tags=body.tags,
            actor=body.actor,
        )
    )
    return _present_result(service, project_id, result)


@router.post("/{project_id}/audit-cases/from-candidate/{group_id}")
def create_case_from_candidate(
    project_id: str,
    group_id: str,
    body: CandidateCaseRequest,
    store: ProjectStore = Depends(get_store),
) -> dict[str, Any]:
    service = _service(store, project_id)
    result = _case_call(
        lambda: service.create_from_candidate(
            project_id,
            group_id,
            formal=body.formal,
            title=body.title,
            risk_statement=body.risk or body.risk_statement,
            financial_statement_assertions=body.financial_statement_assertions,
            materiality=body.materiality,
            owner=body.owner,
            actor=body.actor,
            expected_version=body.expected_version,
        )
    )
    return _present_result(service, project_id, result)


@router.get("/{project_id}/audit-cases/{case_id}")
def get_audit_case(
    project_id: str,
    case_id: str,
    store: ProjectStore = Depends(get_store),
) -> dict[str, Any]:
    service = _service(store, project_id)
    result = _case_call(lambda: service.get_case(project_id, case_id))
    return _present_result(service, project_id, result)


@router.patch("/{project_id}/audit-cases/{case_id}")
def update_audit_case(
    project_id: str,
    case_id: str,
    body: UpdateCaseRequest,
    store: ProjectStore = Depends(get_store),
) -> dict[str, Any]:
    service = _service(store, project_id)
    patch_fields = body.model_fields_set - {"actor", "note", "expected_version"}
    if not patch_fields:
        raise HTTPException(status_code=400, detail="至少需要提供一个案件更新字段")
    result = _case_call(
        lambda: service.update_case(
            project_id,
            case_id,
            title=body.title if "title" in patch_fields else None,
            risk_summary=body.risk if "risk" in patch_fields else None,
            materiality=body.materiality if "materiality" in patch_fields else None,
            owner=body.owner if "owner" in patch_fields else None,
            status=body.status if "status" in patch_fields else None,
            actor=body.actor,
            note=body.note,
            expected_version=body.expected_version,
        )
    )
    return _present_result(service, project_id, result)


@router.post("/{project_id}/audit-cases/{case_id}/promote")
def promote_audit_case(
    project_id: str,
    case_id: str,
    body: PromoteCaseRequest,
    store: ProjectStore = Depends(get_store),
) -> dict[str, Any]:
    service = _service(store, project_id)
    result = _case_call(
        lambda: service.promote_case(
            project_id,
            case_id,
            actor=body.actor,
            comment=body.comment,
            expected_version=body.expected_version,
        )
    )
    return _present_result(service, project_id, result)


@router.post("/{project_id}/audit-cases/{case_id}/assertions")
def add_case_assertion(
    project_id: str,
    case_id: str,
    body: AssertionRequest,
    store: ProjectStore = Depends(get_store),
) -> dict[str, Any]:
    service = _service(store, project_id)
    result = _case_call(
        lambda: service.add_assertion(
            project_id,
            case_id,
            title=body.title,
            risk_statement=body.risk_statement,
            financial_statement_assertions=body.financial_statement_assertions,
            affected_accounts=body.affected_accounts,
            actor=body.actor,
            expected_version=body.expected_version,
        )
    )
    return _present_result(service, project_id, result)


@router.patch(
    "/{project_id}/audit-cases/{case_id}/assertions/{assertion_id}"
)
def update_case_assertion(
    project_id: str,
    case_id: str,
    assertion_id: str,
    body: AssertionPatch,
    store: ProjectStore = Depends(get_store),
) -> dict[str, Any]:
    service = _service(store, project_id)
    patch_fields = body.model_fields_set - {"actor", "expected_version"}
    if not patch_fields:
        raise HTTPException(status_code=400, detail="至少需要提供一个认定更新字段")
    result = _case_call(
        lambda: service.update_assertion(
            project_id,
            case_id,
            assertion_id,
            status=body.status if "status" in patch_fields else None,
            title=body.title if "title" in patch_fields else None,
            risk_statement=(
                body.risk_statement if "risk_statement" in patch_fields else None
            ),
            actor=body.actor,
            expected_version=body.expected_version,
        )
    )
    return _present_result(service, project_id, result)


@router.post("/{project_id}/audit-cases/{case_id}/evidence")
def add_case_evidence(
    project_id: str,
    case_id: str,
    body: EvidenceRequest,
    store: ProjectStore = Depends(get_store),
) -> dict[str, Any]:
    service = _service(store, project_id)
    result = _case_call(
        lambda: service.add_evidence(
            project_id,
            case_id,
            source_type=body.source_type,
            source_ref=body.source_ref.model_dump(),
            description=body.description,
            status=body.status,
            assertion_ids=body.assertion_ids or None,
            procedure_id=body.procedure_id,
            actor=body.actor,
            expected_version=body.expected_version,
        )
    )
    return _present_result(service, project_id, result)


@router.patch(
    "/{project_id}/audit-cases/{case_id}/evidence/{evidence_id}"
)
def update_case_evidence(
    project_id: str,
    case_id: str,
    evidence_id: str,
    body: EvidencePatch,
    store: ProjectStore = Depends(get_store),
) -> dict[str, Any]:
    service = _service(store, project_id)
    patch_fields = body.model_fields_set - {"actor", "expected_version"}
    if not patch_fields:
        raise HTTPException(status_code=400, detail="至少需要提供一个证据更新字段")
    result = _case_call(
        lambda: service.update_evidence(
            project_id,
            case_id,
            evidence_id,
            status=body.status if "status" in patch_fields else None,
            description=body.description if "description" in patch_fields else None,
            source_ref=(
                body.source_ref.model_dump()
                if "source_ref" in patch_fields and body.source_ref is not None
                else None
            ),
            assertion_ids=(
                body.assertion_ids if "assertion_ids" in patch_fields else None
            ),
            procedure_id=(
                body.procedure_id if "procedure_id" in patch_fields else None
            ),
            actor=body.actor,
            expected_version=body.expected_version,
        )
    )
    return _present_result(service, project_id, result)


@router.post("/{project_id}/audit-cases/{case_id}/procedures")
def add_case_procedure(
    project_id: str,
    case_id: str,
    body: ProcedureRequest,
    store: ProjectStore = Depends(get_store),
) -> dict[str, Any]:
    service = _service(store, project_id)
    result = _case_call(
        lambda: service.add_procedure(
            project_id,
            case_id,
            title=body.title,
            description=body.description,
            assertion_ids=body.assertion_ids or None,
            status=body.status,
            result=body.result,
            performed_by=body.performed_by,
            performed_at=body.performed_at,
            actor=body.actor,
            expected_version=body.expected_version,
        )
    )
    return _present_result(service, project_id, result)


@router.patch(
    "/{project_id}/audit-cases/{case_id}/procedures/{procedure_id}"
)
def update_case_procedure(
    project_id: str,
    case_id: str,
    procedure_id: str,
    body: ProcedurePatch,
    store: ProjectStore = Depends(get_store),
) -> dict[str, Any]:
    service = _service(store, project_id)
    patch_fields = body.model_fields_set - {"actor", "expected_version"}
    if not patch_fields:
        raise HTTPException(status_code=400, detail="至少需要提供一个程序更新字段")
    result = _case_call(
        lambda: service.update_procedure(
            project_id,
            case_id,
            procedure_id,
            title=body.title if "title" in patch_fields else None,
            description=body.description if "description" in patch_fields else None,
            assertion_ids=(
                body.assertion_ids if "assertion_ids" in patch_fields else None
            ),
            status=body.status if "status" in patch_fields else None,
            result=body.result if "result" in patch_fields else None,
            performed_by=(
                body.performed_by if "performed_by" in patch_fields else None
            ),
            performed_at=(
                body.performed_at if "performed_at" in patch_fields else None
            ),
            actor=body.actor,
            expected_version=body.expected_version,
        )
    )
    return _present_result(service, project_id, result)


@router.put("/{project_id}/audit-cases/{case_id}/conclusion")
def set_case_conclusion(
    project_id: str,
    case_id: str,
    body: ConclusionRequest,
    store: ProjectStore = Depends(get_store),
) -> dict[str, Any]:
    service = _service(store, project_id)
    result = _case_call(
        lambda: service.set_conclusion(
            project_id,
            case_id,
            outcome=body.outcome,
            summary=body.summary,
            basis_evidence_ids=(
                body.basis_evidence_ids
                if "basis_evidence_ids" in body.model_fields_set
                else None
            ),
            procedure_ids=(
                body.procedure_ids
                if "procedure_ids" in body.model_fields_set
                else None
            ),
            unresolved_assertion_ids=(
                body.unresolved_assertion_ids
                if "unresolved_assertion_ids" in body.model_fields_set
                else None
            ),
            override_reason=body.override_reason,
            misstatement_amount=body.misstatement_amount,
            currency=body.currency,
            actor=body.actor,
            expected_version=body.expected_version,
        )
    )
    return _present_result(service, project_id, result)


@router.get("/{project_id}/audit-cases/{case_id}/events")
def list_case_events(
    project_id: str,
    case_id: str,
    store: ProjectStore = Depends(get_store),
) -> dict[str, Any]:
    service = _service(store, project_id)
    events = _case_call(lambda: service.list_events(project_id, case_id))
    integrity = service.verify_event_chain(project_id, case_id)
    return {"count": len(events), "events": events, "integrity": integrity}


@router.post("/{project_id}/audit-cases/{case_id}/events")
def append_case_review_event(
    project_id: str,
    case_id: str,
    body: AppendCaseEventRequest,
    store: ProjectStore = Depends(get_store),
) -> dict[str, Any]:
    service = _service(store, project_id)
    result = _case_call(
        lambda: service.record_review(
            project_id,
            case_id,
            action=body.event_type,
            actor=body.actor,
            note=body.note,
            expected_version=body.expected_version,
        )
    )
    event = service.list_events(project_id, case_id)[-1]
    return {
        "case": _present_result(service, project_id, result),
        "event": _present_event(event),
        "integrity": service.verify_event_chain(project_id, case_id),
    }


@router.get("/{project_id}/audit-cases/{case_id}/snapshots")
def list_case_snapshots(
    project_id: str,
    case_id: str,
    store: ProjectStore = Depends(get_store),
) -> dict[str, Any]:
    service = _service(store, project_id)
    snapshots = _case_call(lambda: service.list_snapshots(project_id, case_id))
    return {"count": len(snapshots), "snapshots": snapshots}


@router.get("/{project_id}/audit-cases/{case_id}/integrity")
def verify_case_integrity(
    project_id: str,
    case_id: str,
    store: ProjectStore = Depends(get_store),
) -> dict[str, Any]:
    service = _service(store, project_id)
    return _case_call(lambda: service.verify_event_chain(project_id, case_id))
