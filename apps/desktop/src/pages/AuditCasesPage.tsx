import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  type AuditCase,
  type AuditCaseListResponse,
  type AuditCaseStatus,
  type AuditConclusion,
  type CandidateGroup,
  type ProjectSummary,
} from "@/api/client";
import { EmptyState } from "@/components/EmptyState";

type Props = { project: ProjectSummary | null };

const STATUS_LABELS: Record<AuditCaseStatus, string> = {
  draft: "草稿",
  planned: "已计划",
  in_progress: "执行中",
  pending_evidence: "待证据",
  concluded: "已结论",
  closed: "已关闭",
};

const MATERIALITY_LABELS: Record<AuditCase["materiality"], string> = {
  unassessed: "待评估",
  low: "低",
  medium: "中",
  high: "高",
};

const OUTCOME_LABELS: Record<AuditConclusion["outcome"], string> = {
  pending: "尚未结论",
  no_exception: "未发现异常",
  reasonable_exception: "合理例外",
  exception: "审计例外",
  finding: "审计发现",
  insufficient_evidence: "证据不足",
  control_deficiency: "控制缺陷",
  misstatement: "错报",
  scope_limitation: "范围受限",
};

const STATUS_TRANSITIONS: Record<AuditCaseStatus, AuditCaseStatus[]> = {
  draft: [],
  planned: ["in_progress", "pending_evidence"],
  in_progress: ["pending_evidence"],
  pending_evidence: ["in_progress"],
  concluded: ["in_progress", "closed"],
  closed: [],
};

const EVIDENCE_STATUS_LABELS = {
  active: "待核验",
  verified: "已核验",
  requested: "待获取",
  missing: "缺失",
  stale: "已过期",
} as const;

const ASSERTION_STATUS_LABELS = {
  open: "待判断",
  supported: "已支持",
  exception: "存在例外",
} as const;

const PROCEDURE_STATUS_LABELS = {
  planned: "待执行",
  in_progress: "执行中",
  completed: "已完成",
  not_applicable: "不适用",
} as const;

const RISK_SIGNAL_SOURCE_TYPES = new Set([
  "candidate_group",
  "chart_selection",
  "module_insight",
  "query_result",
  "rule_hit",
]);
const NON_SUBSTANTIVE_EVIDENCE_TYPES = new Set([
  ...RISK_SIGNAL_SOURCE_TYPES,
  "management_explanation",
]);

const PENDING_CONCLUSION_OUTCOMES = new Set(["pending", "insufficient_evidence", "scope_limitation"]);

function suggestedAssertions(moduleName: string): string[] {
  if (moduleName.includes("收入") || moduleName.includes("成本")) return ["发生", "截止", "准确性"];
  if (moduleName.includes("费用")) return ["发生", "分类", "准确性"];
  if (moduleName.includes("营业外") || moduleName.includes("投资")) return ["发生", "分类", "列报"];
  if (moduleName.includes("暂估") || moduleName.includes("往来")) return ["完整性", "截止", "计价"];
  if (moduleName.includes("资产") || moduleName.includes("负债")) return ["存在", "权利和义务", "计价"];
  return ["发生", "准确性"];
}

function csvValues(value: string) {
  return value
    .split(/[,，、]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function formatDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value || "未知时间" : date.toLocaleString("zh-CN");
}

function formatMoney(value?: number) {
  return value === undefined
    ? "待确认"
    : value.toLocaleString("zh-CN", { maximumFractionDigits: 0 });
}

function shortHash(value?: string) {
  if (!value) return "";
  return value.length > 18 ? `${value.slice(0, 10)}…${value.slice(-6)}` : value;
}

function evidenceSourceRef(evidence: AuditCase["evidence"][number]) {
  return evidence.source_ref ?? evidence.locator ?? {};
}

function evidenceSourceSummary(evidence: AuditCase["evidence"][number]) {
  const ref = evidenceSourceRef(evidence);
  const parts = [
    ref.voucher_key ? `凭证键 ${shortHash(String(ref.voucher_key))}` : "",
    ref.line_key ? `行项目键 ${shortHash(String(ref.line_key))}` : "",
    ref.sheet ? `${String(ref.sheet)}!${ref.source_row ?? "?"}` : "",
    ref.source_asset_id ? `来源资产 ${shortHash(String(ref.source_asset_id))}` : "",
    ref.file_hash ? `SHA-256 ${shortHash(String(ref.file_hash))}` : "",
  ].filter(Boolean);
  return parts.join(" · ");
}

function MetadataEditor({
  item,
  pending,
  onSave,
}: {
  item: AuditCase;
  pending: boolean;
  onSave: (body: {
    title: string;
    risk: string;
    materiality: AuditCase["materiality"];
    owner: string;
    status: AuditCaseStatus;
    note: string;
  }) => void;
}) {
  const [title, setTitle] = useState(item.title);
  const [risk, setRisk] = useState(item.risk);
  const [materiality, setMateriality] = useState(item.materiality);
  const [owner, setOwner] = useState(item.owner ?? "");
  const [status, setStatus] = useState(item.status);
  const [note, setNote] = useState("");

  useEffect(() => {
    setTitle(item.title);
    setRisk(item.risk);
    setMateriality(item.materiality);
    setOwner(item.owner ?? "");
    setStatus(item.status);
    setNote("");
  }, [item]);

  const statusOptions = [item.status, ...STATUS_TRANSITIONS[item.status]];

  return (
    <details className="case-editor">
      <summary>编辑案件负责人、重要程度与状态</summary>
      <div className="case-editor__body case-editor__grid">
        <label className="case-editor__wide">
          案件标题
          <input value={title} onChange={(event) => setTitle(event.target.value)} />
        </label>
        <label className="case-editor__wide">
          风险陈述（什么可能出错）
          <textarea rows={2} value={risk} onChange={(event) => setRisk(event.target.value)} />
        </label>
        <label>
          重要程度
          <select value={materiality} onChange={(event) => setMateriality(event.target.value as AuditCase["materiality"])}>
            {Object.entries(MATERIALITY_LABELS).map(([value, label]) => (
              <option key={value} value={value}>{label}</option>
            ))}
          </select>
        </label>
        <label>
          负责人
          <input value={owner} onChange={(event) => setOwner(event.target.value)} placeholder="姓名或角色" />
        </label>
        <label>
          状态
          <select
            value={status}
            disabled={item.kind === "proposal"}
            onChange={(event) => setStatus(event.target.value as AuditCaseStatus)}
          >
            {statusOptions.map((value) => (
              <option key={value} value={value}>{STATUS_LABELS[value]}</option>
            ))}
          </select>
        </label>
        <label>
          变更说明
          <input value={note} onChange={(event) => setNote(event.target.value)} placeholder="为什么调整" />
        </label>
        <div className="case-editor__actions case-editor__wide">
          <button
            type="button"
            className="btn-primary"
            disabled={pending || !title.trim()}
            onClick={() => onSave({ title, risk, materiality, owner, status, note })}
          >
            {pending ? "保存中…" : "保存并记录事件"}
          </button>
        </div>
      </div>
    </details>
  );
}

function AssertionEditor({
  pending,
  onAdd,
}: {
  pending: boolean;
  onAdd: (body: {
    title: string;
    risk_statement: string;
    financial_statement_assertions: string[];
    affected_accounts: string[];
  }) => void;
}) {
  const [title, setTitle] = useState("");
  const [riskStatement, setRiskStatement] = useState("");
  const [assertions, setAssertions] = useState("");
  const [accounts, setAccounts] = useState("");

  return (
    <details className="case-editor">
      <summary>新增审计认定</summary>
      <div className="case-editor__body case-editor__grid">
        <label>
          认定标题
          <input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="例如：收入提前确认" />
        </label>
        <label>
          财务报表认定
          <input value={assertions} onChange={(event) => setAssertions(event.target.value)} placeholder="发生、截止、准确性" />
        </label>
        <label className="case-editor__wide">
          风险陈述
          <textarea rows={2} value={riskStatement} onChange={(event) => setRiskStatement(event.target.value)} />
        </label>
        <label className="case-editor__wide">
          受影响科目（逗号分隔）
          <input value={accounts} onChange={(event) => setAccounts(event.target.value)} />
        </label>
        <div className="case-editor__actions case-editor__wide">
          <button
            type="button"
            className="btn-primary"
            disabled={pending || !title.trim() || !riskStatement.trim()}
            onClick={() => {
              onAdd({
                title: title.trim(),
                risk_statement: riskStatement.trim(),
                financial_statement_assertions: csvValues(assertions),
                affected_accounts: csvValues(accounts),
              });
              setTitle("");
              setRiskStatement("");
              setAssertions("");
              setAccounts("");
            }}
          >
            添加认定
          </button>
        </div>
      </div>
    </details>
  );
}

function EvidenceEditor({
  item,
  pending,
  onAdd,
}: {
  item: AuditCase;
  pending: boolean;
  onAdd: (body: {
    source_type: string;
    source_ref: { voucher_key?: string; locator?: Record<string, unknown> };
    description: string;
    status: "active" | "verified" | "requested" | "missing";
    assertion_ids: string[];
  }) => void;
}) {
  const [sourceType, setSourceType] = useState("journal");
  const [status, setStatus] = useState<"active" | "verified" | "requested" | "missing">("active");
  const [reference, setReference] = useState("");
  const [description, setDescription] = useState("");
  const [assertionIds, setAssertionIds] = useState<string[]>(
    item.assertions.map((assertion) => assertion.assertion_id),
  );

  useEffect(() => {
    setAssertionIds(item.assertions.map((assertion) => assertion.assertion_id));
  }, [item.case_id, item.assertions.length]);

  useEffect(() => {
    if (sourceType === "attachment" && !["requested", "missing"].includes(status)) {
      setStatus("requested");
    }
  }, [sourceType, status]);

  const needsReference = !["requested", "missing"].includes(status);
  const referenceLabel = sourceType === "journal" ? "凭证唯一键" : "资料索引";
  const canSubmit =
    assertionIds.length > 0
    && description.trim().length > 0
    && (!needsReference || reference.trim().length > 0);

  return (
    <details className="case-editor">
      <summary>登记证据引用</summary>
      <div className="case-editor__body case-editor__grid">
        <label>
          证据类型
          <select value={sourceType} onChange={(event) => setSourceType(event.target.value)}>
            <option value="journal">序时账凭证</option>
            <option value="management_explanation">管理层解释</option>
            <option value="counter_evidence">反证</option>
            <option value="attachment">待取得的合同 / 发票 / 附件</option>
          </select>
        </label>
        <label>
          证据状态
          <select value={status} onChange={(event) => setStatus(event.target.value as typeof status)}>
            <option value="active" disabled={sourceType === "attachment"}>已取得，待核验</option>
            <option value="verified" disabled={sourceType === "attachment"}>已取得并核验</option>
            <option value="requested">已向对方索取</option>
            <option value="missing">未能取得</option>
          </select>
        </label>
        {needsReference && (
          <label className="case-editor__wide">
            {referenceLabel}
            <input
              value={reference}
              onChange={(event) => setReference(event.target.value)}
              placeholder={sourceType === "journal" ? "从序时账明细复制“凭证唯一键”" : "会议纪要、函件或资料编号"}
            />
          </label>
        )}
        <fieldset className="case-editor__wide case-link-fieldset">
          <legend>关联认定（至少一项）</legend>
          {item.assertions.map((assertion) => (
            <label key={assertion.assertion_id}>
              <input
                type="checkbox"
                checked={assertionIds.includes(assertion.assertion_id)}
                onChange={(event) => setAssertionIds((current) => (
                  event.target.checked
                    ? [...current, assertion.assertion_id]
                    : current.filter((value) => value !== assertion.assertion_id)
                ))}
              />
              {assertion.name || assertion.title}
            </label>
          ))}
        </fieldset>
        <label className="case-editor__wide">
          证据说明
          <textarea rows={2} value={description} onChange={(event) => setDescription(event.target.value)} placeholder="说明取得了什么、由谁核验、支持或反驳哪项认定" />
        </label>
        <div className="case-editor__actions case-editor__wide">
          <button
            type="button"
            className="btn-primary"
            disabled={pending || !canSubmit}
            onClick={() => {
              onAdd({
                source_type: sourceType,
                source_ref:
                  !needsReference
                    ? {}
                    : sourceType === "journal"
                    ? { voucher_key: reference.trim() }
                    : { locator: { reference: reference.trim() } },
                description: description.trim(),
                status,
                assertion_ids: assertionIds,
              });
              setReference("");
              setDescription("");
            }}
          >
            登记证据
          </button>
        </div>
      </div>
    </details>
  );
}

function ProcedureEditor({
  item,
  pending,
  onAdd,
}: {
  item: AuditCase;
  pending: boolean;
  onAdd: (body: {
    title: string;
    description: string;
    assertion_ids: string[];
    status: "planned" | "in_progress" | "completed" | "not_applicable";
    result: string;
    performed_by: string;
  }) => void;
}) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [status, setStatus] = useState<"planned" | "in_progress" | "completed" | "not_applicable">("planned");
  const [result, setResult] = useState("");
  const [performedBy, setPerformedBy] = useState("");
  const [assertionIds, setAssertionIds] = useState<string[]>(
    item.assertions.map((assertion) => assertion.assertion_id),
  );

  useEffect(() => {
    setAssertionIds(item.assertions.map((assertion) => assertion.assertion_id));
  }, [item.case_id, item.assertions.length]);

  const completionReady =
    status !== "completed" || (result.trim().length > 0 && performedBy.trim().length > 0);

  return (
    <details className="case-editor">
      <summary>新增或记录审计程序</summary>
      <div className="case-editor__body case-editor__grid">
        <label>
          程序标题
          <input value={title} onChange={(event) => setTitle(event.target.value)} />
        </label>
        <label>
          执行状态
          <select value={status} onChange={(event) => setStatus(event.target.value as typeof status)}>
            <option value="planned">待执行</option>
            <option value="in_progress">执行中</option>
            <option value="completed">已完成</option>
            <option value="not_applicable">不适用</option>
          </select>
        </label>
        <label className="case-editor__wide">
          程序描述
          <textarea rows={2} value={description} onChange={(event) => setDescription(event.target.value)} />
        </label>
        <fieldset className="case-editor__wide case-link-fieldset">
          <legend>覆盖认定（至少一项）</legend>
          {item.assertions.map((assertion) => (
            <label key={assertion.assertion_id}>
              <input
                type="checkbox"
                checked={assertionIds.includes(assertion.assertion_id)}
                onChange={(event) => setAssertionIds((current) => (
                  event.target.checked
                    ? [...current, assertion.assertion_id]
                    : current.filter((value) => value !== assertion.assertion_id)
                ))}
              />
              {assertion.name || assertion.title}
            </label>
          ))}
        </fieldset>
        <label>
          执行人
          <input value={performedBy} onChange={(event) => setPerformedBy(event.target.value)} />
        </label>
        <label>
          执行结果
          <input value={result} onChange={(event) => setResult(event.target.value)} />
        </label>
        <div className="case-editor__actions case-editor__wide">
          <button
            type="button"
            className="btn-primary"
            disabled={pending || !title.trim() || !description.trim() || assertionIds.length === 0 || !completionReady}
            onClick={() => {
              onAdd({
                title: title.trim(),
                description: description.trim(),
                assertion_ids: assertionIds,
                status,
                result: result.trim(),
                performed_by: performedBy.trim(),
              });
              setTitle("");
              setDescription("");
              setResult("");
            }}
          >
            保存程序
          </button>
        </div>
      </div>
    </details>
  );
}

function ConclusionEditor({
  item,
  pending,
  onSave,
}: {
  item: AuditCase;
  pending: boolean;
  onSave: (body: {
    outcome: Exclude<AuditConclusion["outcome"], "pending">;
    summary: string;
    basis_evidence_ids: string[];
    procedure_ids: string[];
    unresolved_assertion_ids: string[];
    misstatement_amount?: number;
    currency?: string;
    actor: string;
  }) => void;
}) {
  const [outcome, setOutcome] = useState<Exclude<AuditConclusion["outcome"], "pending">>("no_exception");
  const [summary, setSummary] = useState("");
  const [amount, setAmount] = useState("");
  const [currency, setCurrency] = useState("");
  const [preparedBy, setPreparedBy] = useState("");
  const verifiedEvidence = item.evidence.filter(
    (evidence) =>
      evidence.status === "verified"
      && !NON_SUBSTANTIVE_EVIDENCE_TYPES.has(evidence.source_type ?? ""),
  );
  const completedProcedures = item.procedures.filter((procedure) => procedure.status === "completed");
  const [basisEvidenceIds, setBasisEvidenceIds] = useState<string[]>([]);
  const [procedureIds, setProcedureIds] = useState<string[]>([]);

  useEffect(() => {
    setBasisEvidenceIds(verifiedEvidence.map((evidence) => evidence.evidence_id));
    setProcedureIds(completedProcedures.map((procedure) => procedure.procedure_id));
    setPreparedBy("");
  }, [item.case_id, item.version]);

  const pendingOutcome = ["insufficient_evidence", "scope_limitation"].includes(outcome);
  const disabled =
    item.kind === "proposal"
    || item.status === "closed"
    || !item.integrity.valid
    || item.assertions.length === 0
    || (!pendingOutcome && !item.readiness.ready);
  const unresolvedAssertionIds = item.assertions
    .filter((assertion) => (assertion.status ?? "open") === "open")
    .map((assertion) => assertion.assertion_id);
  const amountInvalid =
    (outcome === "misstatement" && (!amount.trim() || !currency.trim()))
    || (Boolean(amount.trim()) && !currency.trim());

  return (
    <details className="case-editor">
      <summary>形成或更新审计结论</summary>
      <div className="case-editor__body case-editor__grid">
        {disabled && (
          <div className="case-editor__notice case-editor__wide">
            <strong>当前不能形成实质性结论</strong>
            <ul>
              {item.readiness.blockers.map((blocker) => (
                <li key={blocker.code}>{blocker.message}</li>
              ))}
            </ul>
            <small>如果确实无法取得证据，请选择“证据不足”或“范围受限”，系统会保留待解决认定而不会伪装成已结论。</small>
          </div>
        )}
        <label>
          结论类型
          <select value={outcome} onChange={(event) => setOutcome(event.target.value as typeof outcome)}>
            {Object.entries(OUTCOME_LABELS)
              .filter(([value]) => value !== "pending")
              .map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>
        <label>
          错报金额（如适用）
          <input type="number" value={amount} onChange={(event) => setAmount(event.target.value)} />
        </label>
        <label>
          币种
          <input value={currency} onChange={(event) => setCurrency(event.target.value)} placeholder="CNY" />
        </label>
        <label>
          结论编制人
          <input value={preparedBy} onChange={(event) => setPreparedBy(event.target.value)} placeholder="请输入真实姓名或工号" />
        </label>
        <fieldset className="case-editor__wide case-link-fieldset">
          <legend>结论所依据的已核验证据</legend>
          {verifiedEvidence.length === 0 ? (
            <span>暂无可作为结论依据的已核验证据。风险信号和模型回答不会出现在此处。</span>
          ) : verifiedEvidence.map((evidence) => (
            <label key={evidence.evidence_id}>
              <input
                type="checkbox"
                checked={basisEvidenceIds.includes(evidence.evidence_id)}
                onChange={(event) => setBasisEvidenceIds((current) => (
                  event.target.checked
                    ? [...current, evidence.evidence_id]
                    : current.filter((value) => value !== evidence.evidence_id)
                ))}
              />
              {evidence.title}
            </label>
          ))}
        </fieldset>
        <fieldset className="case-editor__wide case-link-fieldset">
          <legend>结论所依据的已完成程序</legend>
          {completedProcedures.length === 0 ? (
            <span>暂无已完成且记录结果的审计程序。</span>
          ) : completedProcedures.map((procedure) => (
            <label key={procedure.procedure_id}>
              <input
                type="checkbox"
                checked={procedureIds.includes(procedure.procedure_id)}
                onChange={(event) => setProcedureIds((current) => (
                  event.target.checked
                    ? [...current, procedure.procedure_id]
                    : current.filter((value) => value !== procedure.procedure_id)
                ))}
              />
              {procedure.title}
            </label>
          ))}
        </fieldset>
        <label className="case-editor__wide">
          结论摘要
          <textarea rows={3} value={summary} onChange={(event) => setSummary(event.target.value)} />
        </label>
        <div className="case-editor__actions case-editor__wide">
          <button
            type="button"
            className="btn-primary"
            disabled={
              pending
              || disabled
              || !summary.trim()
              || !preparedBy.trim()
              || amountInvalid
              || (pendingOutcome && unresolvedAssertionIds.length === 0)
              || (!pendingOutcome && (basisEvidenceIds.length === 0 || procedureIds.length === 0))
            }
            onClick={() => onSave({
              outcome,
              summary: summary.trim(),
              basis_evidence_ids: basisEvidenceIds,
              procedure_ids: procedureIds,
              unresolved_assertion_ids: pendingOutcome ? unresolvedAssertionIds : [],
              ...(amount.trim() ? { misstatement_amount: Number(amount) } : {}),
              ...(currency.trim() ? { currency: currency.trim() } : {}),
              actor: preparedBy.trim(),
            })}
          >
            {pendingOutcome ? "记录证据不足 / 范围受限" : "保存结论并记录事件"}
          </button>
        </div>
      </div>
    </details>
  );
}

function ProcedureCompletionEditor({
  pending,
  onSave,
}: {
  pending: boolean;
  onSave: (result: string, performedBy: string) => void;
}) {
  const [result, setResult] = useState("");
  const [performedBy, setPerformedBy] = useState("");

  return (
    <details className="case-inline-decision">
      <summary>记录执行结果</summary>
      <div>
        <input value={performedBy} onChange={(event) => setPerformedBy(event.target.value)} placeholder="执行人姓名或工号" />
        <textarea rows={2} value={result} onChange={(event) => setResult(event.target.value)} placeholder="执行过程、取得证据与结果" />
        <button
          type="button"
          className="btn-primary"
          disabled={pending || !performedBy.trim() || !result.trim()}
          onClick={() => onSave(result.trim(), performedBy.trim())}
        >
          标记程序已完成
        </button>
      </div>
    </details>
  );
}

function ReviewEditor({
  item,
  pending,
  onRecord,
}: {
  item: AuditCase;
  pending: boolean;
  onRecord: (eventType: "reviewed" | "signed_off", actor: string, note: string) => void;
}) {
  const [actor, setActor] = useState("");
  const [note, setNote] = useState("");
  const canSignoff =
    item.kind === "case"
    && item.status === "concluded"
    && item.readiness.ready
    && item.integrity.valid
    && item.conclusion.status !== "stale";

  return (
    <details className="case-editor">
      <summary>记录复核与签署</summary>
      <div className="case-editor__body case-editor__grid">
        <div className="case-editor__notice case-editor__wide">
          签署人必须独立于案件创建人（{item.created_by || "未记录"}）和结论编制人（{item.conclusion.concluded_by || "未记录"}），并填写复核意见。
        </div>
        <label>
          复核人
          <input value={actor} onChange={(event) => setActor(event.target.value)} placeholder="请输入真实姓名或工号" />
        </label>
        <label className="case-editor__wide">
          复核意见
          <textarea rows={2} value={note} onChange={(event) => setNote(event.target.value)} />
        </label>
        <div className="case-editor__actions case-editor__wide">
          <button type="button" className="btn-ghost" disabled={pending || item.kind === "proposal" || !actor.trim() || !note.trim()} onClick={() => onRecord("reviewed", actor.trim(), note.trim())}>
            记录已复核
          </button>
          <button type="button" className="btn-primary" disabled={pending || !canSignoff || !actor.trim() || !note.trim()} onClick={() => onRecord("signed_off", actor.trim(), note.trim())}>
            复核签署
          </button>
        </div>
      </div>
    </details>
  );
}

export function AuditCasesPage({ project }: Props) {
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<AuditCaseStatus | "all">("all");

  const casesQuery = useQuery({
    queryKey: ["audit-cases", project?.project_id],
    queryFn: () => api.listAuditCases(project!.project_id),
    enabled: !!project?.project_id,
  });

  const candidatesQuery = useQuery({
    queryKey: ["candidates", project?.project_id],
    queryFn: () => api.listCandidates(project!.project_id),
    enabled: !!project?.project_id,
  });

  const cases = casesQuery.data?.cases ?? [];
  const filteredCases = useMemo(
    () => cases.filter((item) => statusFilter === "all" || item.status === statusFilter),
    [cases, statusFilter],
  );

  const migratedCandidateIds = useMemo(
    () => new Set(cases.flatMap((item) => [
      ...(item.source_candidate_ids ?? []),
      ...(item.source_candidate_group_id ? [item.source_candidate_group_id] : []),
    ])),
    [cases],
  );
  const migrationCandidates = (candidatesQuery.data?.groups ?? []).filter(
    (group) => !migratedCandidateIds.has(group.group_id) && group.status !== "排除",
  );

  useEffect(() => {
    if (filteredCases.length === 0) {
      setSelectedId(null);
      return;
    }
    if (!filteredCases.some((item) => item.case_id === selectedId)) {
      setSelectedId(filteredCases[0].case_id);
    }
  }, [filteredCases, selectedId]);

  const refreshCase = (updated?: AuditCase) => {
    if (updated) {
      queryClient.setQueryData<AuditCaseListResponse>(
        ["audit-cases", project?.project_id],
        (current) => current
          ? {
              ...current,
              cases: [
                updated,
                ...current.cases.filter((item) => item.case_id !== updated.case_id),
              ],
              count: current.cases.some((item) => item.case_id === updated.case_id)
                ? current.count
                : current.count + 1,
            }
          : current,
      );
      setSelectedId(updated.case_id);
    }
    queryClient.invalidateQueries({ queryKey: ["audit-cases", project?.project_id] });
  };

  const caseAction = useMutation({
    mutationFn: (action: () => Promise<AuditCase>) => action(),
    onSuccess: refreshCase,
  });

  const migrateCandidate = useMutation({
    mutationFn: ({ group, formal }: { group: CandidateGroup; formal: boolean }) =>
      api.createAuditCaseFromCandidate(project!.project_id, group.group_id, {
        formal,
        financial_statement_assertions: suggestedAssertions(group.source_module),
        materiality: "unassessed",
      }),
    onSuccess: refreshCase,
  });

  if (!project) {
    return (
      <section className="page">
        <h2>审计事项</h2>
        <EmptyState kind="shield" title="尚未选择项目" description="选择项目后，可将疑点推进为认定、证据、程序、结论和复核历史。" />
      </section>
    );
  }

  const selectedCase = filteredCases.find((item) => item.case_id === selectedId) ?? null;
  const sourceGroup = candidatesQuery.data?.groups.find(
    (group) => selectedCase?.source_candidate_ids?.includes(group.group_id),
  );
  const activeSignoff = selectedCase?.signoffs?.find((signoff) => signoff.status === "active");
  const statusCounts = cases.reduce<Partial<Record<AuditCaseStatus, number>>>((counts, item) => {
    counts[item.status] = (counts[item.status] ?? 0) + 1;
    return counts;
  }, {});

  const runCaseAction = (action: () => Promise<AuditCase>) => {
    caseAction.mutate(action);
  };

  return (
    <section className="page audit-cases-page">
      <h2>审计事项</h2>
      <p className="lead">
        <strong>{project.project_name}</strong> — 从疑点立项，围绕认定取得证据、执行程序并形成可复核结论。
      </p>

      <div className="case-trust-banner">
        <div>
          <strong>正式 AuditCase 工作区</strong>
          <p>案件、证据和结论保留数据/分类/币种/规则快照；重导后旧证据会标记过期而不会被删除。</p>
        </div>
        <span className="sampling-basis-status is-confirmed">追加式事件历史</span>
      </div>

      <div className="case-overview-grid">
        <div><span>全部事项</span><strong>{cases.length}</strong></div>
        <div><span>案件建议</span><strong>{cases.filter((item) => item.kind === "proposal").length}</strong></div>
        <div><span>执行中 / 待证据</span><strong>{(statusCounts.in_progress ?? 0) + (statusCounts.pending_evidence ?? 0)}</strong></div>
        <div><span>已形成结论</span><strong>{statusCounts.concluded ?? 0}</strong></div>
      </div>

      <details className="case-migration-panel">
        <summary>
          <span><strong>从疑点库创建正式案件</strong><small>候选疑点只是迁移入口，不是审计结论</small></span>
          <span className="candidate-status">{migrationCandidates.length} 项待处理</span>
        </summary>
        <div className="case-migration-list">
          {migrationCandidates.length === 0 ? (
            <p className="muted">当前没有尚未建案的有效疑点。</p>
          ) : migrationCandidates.map((group) => (
            <article key={group.group_id}>
              <div>
                <strong>{group.title}</strong>
                <span>{group.source_module} / {group.source_view} · {group.voucher_count} 凭证 · {formatMoney(group.amount_total)}</span>
              </div>
              <div>
                <button type="button" className="btn-ghost" disabled={migrateCandidate.isPending} onClick={() => migrateCandidate.mutate({ group, formal: false })}>创建案件草稿</button>
                <button type="button" className="btn-primary" disabled={migrateCandidate.isPending} onClick={() => migrateCandidate.mutate({ group, formal: true })}>直接立项</button>
              </div>
            </article>
          ))}
        </div>
      </details>

      {cases.length > 0 && (
        <div className="case-status-filter" role="group" aria-label="按案件状态筛选">
          <button type="button" className={statusFilter === "all" ? "active" : ""} onClick={() => setStatusFilter("all")}>全部 {cases.length}</button>
          {(["draft", "planned", "in_progress", "pending_evidence", "concluded", "closed"] as const)
            .filter((status) => (statusCounts[status] ?? 0) > 0)
            .map((status) => (
              <button type="button" key={status} className={statusFilter === status ? "active" : ""} onClick={() => setStatusFilter(status)}>
                {STATUS_LABELS[status]} {statusCounts[status]}
              </button>
            ))}
        </div>
      )}

      {(casesQuery.isLoading || candidatesQuery.isLoading) && <p className="muted">正在加载审计事项…</p>}
      {casesQuery.isError && <p className="error">审计事项加载失败：{String(casesQuery.error)}</p>}
      {migrateCandidate.isError && <p className="error">疑点建案失败：{String(migrateCandidate.error)}</p>}
      {caseAction.isError && <p className="error">案件操作失败：{String(caseAction.error)}</p>}

      {!casesQuery.isLoading && cases.length === 0 && (
        <EmptyState
          kind="shield"
          title="尚无正式审计事项"
          description={migrationCandidates.length > 0 ? "展开上方“从疑点库创建正式案件”开始建案。" : "先在财务画像或疑点工作台形成候选疑点。"}
        />
      )}

      {filteredCases.length > 0 && (
        <div className="case-workspace">
          <aside className="case-list-panel" aria-label="案件列表">
            <div className="case-list-panel__head"><strong>案件列表</strong><span>{filteredCases.length} 项</span></div>
            <div className="case-list-panel__items">
              {filteredCases.map((item) => (
                <button type="button" key={item.case_id} className={`case-list-item${item.case_id === selectedId ? " is-selected" : ""}`} onClick={() => setSelectedId(item.case_id)}>
                  <span className="case-list-item__top"><strong>{item.title}</strong><em>{item.kind === "proposal" ? "建议" : STATUS_LABELS[item.status]}</em></span>
                  <span>{item.risk || "风险陈述待补充"}</span>
                  <small>{item.assertions.flatMap((assertion) => assertion.financial_statement_assertions ?? [assertion.name]).filter(Boolean).join(" · ") || "认定待补充"}</small>
                </button>
              ))}
            </div>
          </aside>

          {selectedCase && (
            <article className="case-detail-panel">
              <header className="case-detail-head">
                <div>
                  <span className="sampling-eyebrow">{selectedCase.kind === "proposal" ? "Case proposal" : "AuditCase"} · {selectedCase.case_id} · v{selectedCase.version ?? 1}</span>
                  <h3>{selectedCase.title}</h3>
                  <p>{selectedCase.risk || "尚未记录风险陈述。"}</p>
                </div>
                <div className="case-detail-head__badges">
                  <span className="candidate-status">{selectedCase.kind === "proposal" ? "案件建议" : STATUS_LABELS[selectedCase.status]}</span>
                  <span className={`case-materiality case-materiality--${selectedCase.materiality}`}>重要程度 {MATERIALITY_LABELS[selectedCase.materiality]}</span>
                </div>
              </header>

              <div className="case-detail-meta">
                <span>负责人：{selectedCase.owner || "待指派"}</span>
                <span>风险领域：{selectedCase.risk_domain || sourceGroup?.source_module || "待确认"}</span>
                <span>来源金额：{formatMoney(sourceGroup?.amount_total)}</span>
                <span>最后更新：{formatDate(selectedCase.updated_at)}</span>
              </div>

              <div className={`case-readiness${selectedCase.readiness.ready && selectedCase.integrity.valid ? " is-ready" : " is-blocked"}`}>
                <div className="case-readiness__summary">
                  <div>
                    <strong>
                      {selectedCase.readiness.ready ? "证据链已满足结论条件" : "证据链尚未满足结论条件"}
                    </strong>
                    <span>
                      {selectedCase.readiness.assertion_count} 项认定 · {selectedCase.readiness.verified_evidence_count} 项已核验证据 · {selectedCase.readiness.completed_procedure_count} 项已完成程序
                    </span>
                  </div>
                  <span className={selectedCase.integrity.valid ? "is-valid" : "is-invalid"}>
                    {selectedCase.integrity.valid
                      ? `内部事件链一致（${selectedCase.integrity.event_count} 项）`
                      : `完整性异常（${selectedCase.integrity.errors.length} 项）`}
                  </span>
                </div>
                {(!selectedCase.readiness.ready || !selectedCase.integrity.valid) && (
                  <details>
                    <summary>查看阻断原因</summary>
                    <ul>
                      {selectedCase.readiness.blockers.map((blocker) => (
                        <li key={`${blocker.code}-${(blocker.assertion_ids ?? []).join("-")}`}>{blocker.message}</li>
                      ))}
                      {!selectedCase.integrity.valid && <li>事件、快照或当前案件内容的校验链不一致，需先复核数据完整性。</li>}
                    </ul>
                  </details>
                )}
                <small>“内部事件链一致”仅表示本项目内的记录可重放且未发现篡改，不等同于外部时间戳、公证或电子签名。</small>
              </div>

              {selectedCase.kind === "proposal" && (
                <div className="case-promotion-bar">
                  <span>这是可撤回的案件建议；立项后进入正式程序与状态流转。</span>
                  <button type="button" className="btn-primary" disabled={caseAction.isPending} onClick={() => runCaseAction(() => api.promoteAuditCase(project.project_id, selectedCase.case_id, { comment: "用户在审计事项工作区确认立项", expected_version: selectedCase.version ?? 1 }))}>
                    确认立项
                  </button>
                </div>
              )}

              <MetadataEditor
                item={selectedCase}
                pending={caseAction.isPending}
                onSave={(body) => runCaseAction(() => api.updateAuditCase(project.project_id, selectedCase.case_id, { ...body, expected_version: selectedCase.version ?? 1 }))}
              />

              <div className="case-section-grid">
                <section className="case-section">
                  <div className="case-section__head"><h4>审计认定</h4><span>{selectedCase.assertions.length} 项</span></div>
                  {selectedCase.assertions.length > 0 ? (
                    <div className="case-assertions">
                      {selectedCase.assertions.map((assertion) => (
                        <div key={assertion.assertion_id}>
                          <div className="case-assertion-title">
                            <strong>{assertion.name || assertion.title}</strong>
                            <em className={`case-assertion-status is-${assertion.status ?? "open"}`}>
                              {ASSERTION_STATUS_LABELS[assertion.status ?? "open"]}
                            </em>
                          </div>
                          <span>{(assertion.financial_statement_assertions ?? []).join("、") || assertion.rationale}</span>
                          <div className="case-assertion-actions" role="group" aria-label={`判断认定：${assertion.name || assertion.title}`}>
                            {(["supported", "exception", "open"] as const).map((status) => (
                              <button
                                type="button"
                                key={status}
                                className={(assertion.status ?? "open") === status ? "is-active" : ""}
                                disabled={
                                  caseAction.isPending
                                  || selectedCase.kind === "proposal"
                                  || selectedCase.status === "closed"
                                  || (assertion.status ?? "open") === status
                                }
                                onClick={() => runCaseAction(() => api.updateAuditCaseAssertion(
                                  project.project_id,
                                  selectedCase.case_id,
                                  assertion.assertion_id,
                                  { status, expected_version: selectedCase.version ?? 1 },
                                ))}
                              >
                                {ASSERTION_STATUS_LABELS[status]}
                              </button>
                            ))}
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : <p className="muted">尚未记录审计认定。</p>}
                  <AssertionEditor pending={caseAction.isPending} onAdd={(body) => runCaseAction(() => api.addAuditCaseAssertion(project.project_id, selectedCase.case_id, { ...body, expected_version: selectedCase.version ?? 1 }))} />
                </section>

                <section className="case-section">
                  <div className="case-section__head"><h4>证据清单</h4><span>模型回答不能替代证据</span></div>
                  {selectedCase.evidence.length > 0 ? (
                    <div className="case-evidence-list">
                      {selectedCase.evidence.map((evidence) => {
                        const sourceRef = evidenceSourceRef(evidence);
                        const sourceSummary = evidenceSourceSummary(evidence);
                        const isRiskSignal = RISK_SIGNAL_SOURCE_TYPES.has(evidence.source_type ?? "");
                        const isCorroborativeOnly = evidence.source_type === "management_explanation";
                        return (
                          <div key={evidence.evidence_id}>
                            <span className={`case-evidence-status case-evidence-status--${evidence.status}`}>
                              {EVIDENCE_STATUS_LABELS[evidence.status]}
                            </span>
                            <span>
                              <strong>{evidence.title}</strong>
                              {isRiskSignal && <em className="case-evidence-kind">风险信号，不可单独支持结论</em>}
                              {isCorroborativeOnly && (
                                <em className="case-evidence-kind">
                                  辅助说明，须与其他审计证据相互印证
                                </em>
                              )}
                              {(evidence.note || evidence.description) && <small>{evidence.note || evidence.description}</small>}
                              {sourceSummary && <small className="case-source-coordinate">{sourceSummary}</small>}
                              <span className="case-evidence-actions">
                                {Boolean(sourceRef.source_asset_id) && (
                                  <a href={api.sourceDownloadUrl(project.project_id, String(sourceRef.source_asset_id))}>
                                    下载并核对原始来源
                                  </a>
                                )}
                                {!isRiskSignal && evidence.status === "active" && (
                                  <button
                                    type="button"
                                    disabled={caseAction.isPending || selectedCase.status === "closed"}
                                    onClick={() => runCaseAction(() => api.updateAuditCaseEvidence(
                                      project.project_id,
                                      selectedCase.case_id,
                                      evidence.evidence_id,
                                      { status: "verified", expected_version: selectedCase.version ?? 1 },
                                    ))}
                                  >
                                    核验通过
                                  </button>
                                )}
                                {evidence.status === "verified" && (
                                  <button
                                    type="button"
                                    disabled={caseAction.isPending || selectedCase.status === "closed"}
                                    onClick={() => runCaseAction(() => api.updateAuditCaseEvidence(
                                      project.project_id,
                                      selectedCase.case_id,
                                      evidence.evidence_id,
                                      { status: "active", expected_version: selectedCase.version ?? 1 },
                                    ))}
                                  >
                                    撤回核验
                                  </button>
                                )}
                                {evidence.status === "requested" && (
                                  <button
                                    type="button"
                                    disabled={caseAction.isPending || selectedCase.status === "closed"}
                                    onClick={() => runCaseAction(() => api.updateAuditCaseEvidence(
                                      project.project_id,
                                      selectedCase.case_id,
                                      evidence.evidence_id,
                                      { status: "missing", expected_version: selectedCase.version ?? 1 },
                                    ))}
                                  >
                                    确认未能取得
                                  </button>
                                )}
                                {evidence.status === "missing" && (
                                  <button
                                    type="button"
                                    disabled={caseAction.isPending || selectedCase.status === "closed"}
                                    onClick={() => runCaseAction(() => api.updateAuditCaseEvidence(
                                      project.project_id,
                                      selectedCase.case_id,
                                      evidence.evidence_id,
                                      { status: "requested", expected_version: selectedCase.version ?? 1 },
                                    ))}
                                  >
                                    重新索取
                                  </button>
                                )}
                              </span>
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  ) : <p className="muted">尚未登记证据。</p>}
                  <EvidenceEditor item={selectedCase} pending={caseAction.isPending} onAdd={(body) => runCaseAction(() => api.addAuditCaseEvidence(project.project_id, selectedCase.case_id, { ...body, expected_version: selectedCase.version ?? 1 }))} />
                </section>

                <section className="case-section case-section--wide">
                  <div className="case-section__head"><h4>审计程序</h4><span>{selectedCase.procedures.length} 项</span></div>
                  {selectedCase.procedures.length > 0 ? (
                    <ol className="case-procedure-list">
                      {selectedCase.procedures.map((procedure) => (
                        <li key={procedure.procedure_id}>
                          <span className={`case-procedure-index${procedure.status === "completed" ? " is-done" : ""}`} aria-hidden />
                          <div>
                            <strong>{procedure.title}</strong>
                            <small>{procedure.description} · {PROCEDURE_STATUS_LABELS[procedure.status]}</small>
                            {procedure.result && <span>{procedure.result}</span>}
                            {procedure.performed_by && (
                              <small>执行人：{procedure.performed_by}{procedure.performed_at ? ` · ${formatDate(procedure.performed_at)}` : ""}</small>
                            )}
                            {procedure.status === "planned" && (
                              <button
                                type="button"
                                className="case-procedure-start"
                                disabled={caseAction.isPending || selectedCase.status === "closed"}
                                onClick={() => runCaseAction(() => api.updateAuditCaseProcedure(
                                  project.project_id,
                                  selectedCase.case_id,
                                  procedure.procedure_id,
                                  { status: "in_progress", expected_version: selectedCase.version ?? 1 },
                                ))}
                              >
                                开始执行
                              </button>
                            )}
                            {["planned", "in_progress"].includes(procedure.status) && (
                              <ProcedureCompletionEditor
                                pending={caseAction.isPending || selectedCase.status === "closed"}
                                onSave={(result, performedBy) => runCaseAction(() => api.updateAuditCaseProcedure(
                                  project.project_id,
                                  selectedCase.case_id,
                                  procedure.procedure_id,
                                  {
                                    status: "completed",
                                    result,
                                    performed_by: performedBy,
                                    expected_version: selectedCase.version ?? 1,
                                  },
                                ))}
                              />
                            )}
                          </div>
                        </li>
                      ))}
                    </ol>
                  ) : <p className="muted">尚未计划审计程序。</p>}
                  <ProcedureEditor item={selectedCase} pending={caseAction.isPending} onAdd={(body) => runCaseAction(() => api.addAuditCaseProcedure(project.project_id, selectedCase.case_id, { ...body, expected_version: selectedCase.version ?? 1 }))} />
                </section>

                <section className="case-section">
                  <div className="case-section__head"><h4>结论</h4><span>{OUTCOME_LABELS[selectedCase.conclusion.outcome]}</span></div>
                  <div className={PENDING_CONCLUSION_OUTCOMES.has(selectedCase.conclusion.outcome) || selectedCase.conclusion.status === "stale" ? "case-conclusion-pending" : "case-conclusion-final"}>
                    <strong>{OUTCOME_LABELS[selectedCase.conclusion.outcome]}</strong>
                    <p>{selectedCase.conclusion.summary || "尚未形成审计结论。"}</p>
                    {selectedCase.conclusion.misstatement_amount !== undefined && <small>错报金额：{formatMoney(selectedCase.conclusion.misstatement_amount)} {selectedCase.conclusion.currency}</small>}
                    {selectedCase.conclusion.concluded_by && <small>编制人：{selectedCase.conclusion.concluded_by} · {selectedCase.conclusion.concluded_at ? formatDate(selectedCase.conclusion.concluded_at) : "时间未记录"}</small>}
                    {selectedCase.conclusion.outcome !== "pending" && (
                      <small>
                        依据 {selectedCase.conclusion.basis_evidence_ids?.length ?? 0} 项证据 / {selectedCase.conclusion.procedure_ids?.length ?? 0} 项程序
                      </small>
                    )}
                    {selectedCase.conclusion.status === "stale" && <small>原结论已失效：{selectedCase.conclusion.stale_reason || "案件事实或证据发生变化"}</small>}
                  </div>
                  <ConclusionEditor item={selectedCase} pending={caseAction.isPending} onSave={(body) => runCaseAction(() => api.setAuditCaseConclusion(project.project_id, selectedCase.case_id, { ...body, expected_version: selectedCase.version ?? 1 }))} />
                </section>

                <section className="case-section">
                  <div className="case-section__head"><h4>案件快照</h4><span>用于重放</span></div>
                  <dl className="case-snapshot">
                    <div><dt>数据版本</dt><dd>{selectedCase.snapshot.data_version || "未回传"}</dd></div>
                    <div><dt>导入批次</dt><dd>{selectedCase.snapshot.ingest_run_id || "未回传"}</dd></div>
                    <div><dt>分类版本</dt><dd>{selectedCase.snapshot.classification_revision || "未回传"}</dd></div>
                    <div><dt>币种范围</dt><dd>{selectedCase.snapshot.currency_scope || "未指定"}</dd></div>
                    <div><dt>规则版本</dt><dd>{selectedCase.snapshot.rule_version || selectedCase.snapshot.rule_revision || "未回传"}</dd></div>
                    <div><dt>规则运行</dt><dd>{selectedCase.snapshot.rule_run_id || "未运行"}</dd></div>
                    <div><dt>引擎版本</dt><dd>{selectedCase.snapshot.engine_revision || "未回传"}</dd></div>
                    <div><dt>规则结果哈希</dt><dd>{shortHash(selectedCase.snapshot.result_hash) || "未回传"}</dd></div>
                  </dl>
                </section>

                <section className="case-section case-section--wide">
                  <div className="case-section__head"><h4>状态与复核历史</h4><span>{selectedCase.events.length} 个追加式事件</span></div>
                  {selectedCase.events.length > 0 ? (
                    <ol className="case-event-list">
                      {selectedCase.events.map((event) => (
                        <li key={event.event_id}>
                          <time>{formatDate(event.at)}</time>
                          <strong>{event.canonical_event_type || event.event_type}</strong>
                          <span>{event.actor || "system"}{event.note ? ` · ${event.note}` : ""}</span>
                        </li>
                      ))}
                    </ol>
                  ) : <p className="muted">事件历史尚未回传。</p>}
                  <div className="case-signoff-summary">
                    {activeSignoff ? (
                      <span className="is-signed">
                        当前有效签署：{activeSignoff.actor} · {formatDate(activeSignoff.signed_at)} · {activeSignoff.note}
                      </span>
                    ) : (
                      <span>尚无与当前结论及案件版本一致的独立签署。</span>
                    )}
                  </div>
                  <ReviewEditor item={selectedCase} pending={caseAction.isPending} onRecord={(eventType, actor, note) => runCaseAction(() => api.appendAuditCaseEvent(project.project_id, selectedCase.case_id, { event_type: eventType, actor, note, expected_version: selectedCase.version ?? 1 }))} />
                </section>
              </div>
            </article>
          )}
        </div>
      )}
    </section>
  );
}
