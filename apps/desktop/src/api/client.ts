function resolveApiBase(): string {
  if (import.meta.env.VITE_API_BASE) {
    return import.meta.env.VITE_API_BASE;
  }
  // Packaged Tauri has no Vite /api proxy — talk to the local sidecar directly.
  const isTauri =
    typeof window !== "undefined" &&
    ("__TAURI_INTERNALS__" in window || "__TAURI__" in window);
  if (isTauri) {
    return "http://127.0.0.1:29180";
  }
  return "/api";
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const base = resolveApiBase();
  const res = await fetch(`${base}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return res.json() as Promise<T>;
}

async function upload<T>(path: string, form: FormData): Promise<T> {
  const base = resolveApiBase();
  const res = await fetch(`${base}${path}`, { method: "POST", body: form });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res.json() as Promise<T>;
}

export type Health = {
  status: string;
  version: string;
  storage_root: string;
};

export type ProjectSummary = {
  project_id: string;
  project_name: string;
  years: number[];
  total_rows: number;
  updated_at: string;
};

export type DetectResponse = {
  file_label: string;
  source_columns: string[];
  suggested_mapping: Record<string, string>;
  mapping_matches: Record<string, { source: string; score: number; method: string }>;
  sample_rows: Record<string, unknown>[];
};

export type YearSummary = {
  年份: number;
  行数: number;
  凭证数: number;
  Period13行数?: number;
  金额合计?: number;
  日期范围?: string;
};

export type IngestResponse = {
  project_id: string;
  total_rows: number;
  years: number[];
  missing_columns: string[];
  year_summary: YearSummary[];
  aliases_recorded: number;
};

export type AnalysisQuality = {
  amount_source: string;
  amount_sign_mode: string;
  amount_sign_confidence: number;
  currency_basis: string;
  currencies: string[];
  mixed_document_currency: boolean;
  amounts_comparable: boolean;
  currency_distribution: Array<{
    currency: string;
    row_count: number;
    voucher_count: number;
    absolute_entry_amount: number;
  }>;
  total_absolute_entry_amount: number;
  unclassified_amount: number;
  unclassified_amount_ratio: number;
  review_required_amount: number;
  review_required_amount_ratio: number;
  excluded_amount: number;
  excluded_amount_ratio: number;
  reason_breakdown: Record<string, {
    label: string;
    amount: number;
    amount_ratio: number;
    row_count: number;
    account_count: number;
  }>;
  review_accounts: QualityReviewAccount[];
};

export type QualityReviewAccount = {
  account_code: string;
  account_name: string;
  amount: number;
  amount_ratio: number;
  row_count: number;
  reason: string;
  reason_label: string;
  mapping_allowed: boolean;
  decision?: "map" | "exclude" | "defer" | null;
  decision_category?: string | null;
  recommended_category?: string | null;
  effective_category?: string | null;
  system_corrected?: boolean;
  rationale: string;
};

export type ClassificationDecision = {
  account_code: string;
  account_name?: string;
  decision: "map" | "exclude" | "defer" | "reset";
  category?: string | null;
  rationale?: string;
  decided_at?: string;
};

export type AnalysisQualityResponse = {
  data_version: string;
  classification_revision: string;
  analysis_currency_scope?: string | null;
  analysis_scope_revision?: string;
  allowed_categories: string[];
  classification_decisions: ClassificationDecision[];
  years: Record<string, AnalysisQuality>;
  currency_overview?: Record<string, AnalysisQuality>;
  recalculation?: {
    applied_count: number;
    classification_revision: string;
    invalidated: string[];
  };
};

export type MonthlyRow = {
  月份: number;
  净收入: number;
  净成本?: number;
  净成本影响: number;
  净费用: number;
  毛利: number;
  营业利润: number;
};

export type CustomerRow = {
  客户: string;
  净收入: number;
  占比: number;
};

export type DrilldownResponse = {
  year: number;
  category: string;
  row_count: number;
  rows: Record<string, unknown>[];
  month?: number;
  metric?: string;
  metric_label?: string;
  customer?: string;
};

export type AuditSelection = {
  label: string;
  source_module: string;
  source_view: string;
  selector: Record<string, unknown>;
  summary?: Record<string, unknown>;
};

export type FinanceModuleId =
  | "income"
  | "expense"
  | "other_pnl"
  | "cost_variance"
  | "working_capital"
  | "balance_sheet"
  | "adjustment"
  | "profile"
  | "cross";

export type AgentUiAction =
  | { type: "navigate_main"; tab: "finance" | "suspects" | "cases" | "sampling" }
  | { type: "finance_module"; module: FinanceModuleId }
  | { type: "set_finance_year"; year: number }
  | { type: "pin_selection"; context: AuditSelection }
  | { type: "invalidate_queries"; queryKey: unknown[] }
  | { type: "invalidate_project_analysis"; project_id: string };

export type AgentChatResponse = {
  reply: string;
  tool_calls: Array<{ tool: string; args: Record<string, unknown>; result: Record<string, unknown> }>;
  pinned_context?: AuditSelection | null;
  session_id?: string;
  suggestions: string[];
  needs_api_key?: boolean;
  ui_actions?: AgentUiAction[];
};

export type AgentSessionSummary = {
  id: string;
  title: string;
  created_at?: string;
  updated_at?: string;
  message_count: number;
  active: boolean;
};

export type AgentStateResponse = {
  messages: Array<{ role: string; content: string; at?: string; tool_calls?: AgentChatResponse["tool_calls"] }>;
  pinned_context: AuditSelection | null;
  suggestions?: string[];
  updated_at?: string;
  active_session_id?: string;
  sessions?: AgentSessionSummary[];
  audit_events?: Array<Record<string, unknown>>;
  pending_actions?: Array<Record<string, unknown>>;
};

export type LlmProfile = {
  profile_id: string;
  profile_name: string;
  base_url: string;
  model: string;
  keychain_account: string;
  is_default: boolean;
  updated_at?: string;
  key_configured?: boolean;
  secret_backend?: string;
};

export type LlmPreset = {
  id: string;
  label: string;
  base_url: string;
  default_model: string;
};

export type LlmModelsResponse = {
  models: string[];
  source: string;
  count: number;
  warning?: string;
};

export type InsightStage = { id: string; label: string; percent: number };

export type InsightJob = {
  job_id: string;
  module_key: string;
  stage: string;
  stage_label: string;
  percent: number;
  status: "queued" | "running" | "done" | "error";
  created_at?: string;
  started_at?: string | null;
  updated_at?: string;
  completed_at?: string | null;
  error?: string | null;
  reused?: boolean;
  stages?: InsightStage[];
};

export type InsightJobsResponse = {
  stages: InsightStage[];
  jobs: InsightJob[];
  running_count: number;
  overall_percent: number;
};

export type ModuleInsight = {
  executive_summary?: string;
  findings?: Array<Record<string, string>>;
  recommendations?: Array<Record<string, unknown>>;
  module?: string;
  input_signature?: string;
};

export type CandidateGroup = {
  group_id: string;
  title: string;
  source_module: string;
  source_view: string;
  selector: Record<string, unknown>;
  tags: string[];
  reason: string;
  status: string;
  created_at: string;
  voucher_ids: string[];
  row_count: number;
  voucher_count: number;
  amount_total: number;
};

export type CandidateStats = {
  groups: number;
  active_groups: number;
  active_vouchers: number;
  amount_total: number;
};

export type ProfileOverview = {
  total_rows: number;
  total_vouchers: number;
  avg_rows_per_voucher: number;
  period13_rows?: number;
  date_range?: { start: string; end: string };
};

export type YearProfile = {
  schema_version?: number;
  year: number;
  overview: ProfileOverview;
  amount_distribution?: {
    voucher_level?: {
      p25?: number;
      p50?: number;
      p75?: number;
      p90?: number;
      p95?: number;
      p99?: number;
      max?: number;
      mean?: number;
    };
    top_1pct_voucher_count?: number;
    top_1pct_amount_ratio?: number;
  };
  benford_first_digit?: {
    sample_size: number;
    observed: Record<string, number>;
    expected: Record<string, number>;
    deviation: Record<string, number>;
    mean_absolute_deviation?: number;
    top_deviation_digit?: number | null;
    order_magnitude_count?: number;
    applicable?: boolean;
    conformity?: string;
    basis?: string;
  };
  temporal_patterns?: {
    monthly_count?: Record<string, number>;
    monthly_amount?: Record<string, number>;
    month_end_concentration?: Record<string, number>;
    dec_vs_avg_multiplier?: number;
  };
  manual_entry_ratio?: {
    manual_ratio?: number;
    manual_voucher_ratio?: number;
    manual_voucher_count?: number;
  };
};

export type CrossYearFinding = {
  category: string;
  description: string;
  years_involved: number[];
  voucher_ids: string[];
  amount: number;
  severity: string;
};

export type RuleSummary = { rule_name: string; count: number };

/** 项目规则配置（与 config/default_rules.json 同构）。 */
export type RulesConfig = Record<string, unknown>;

export type SampleRow = {
  凭证编号: string;
  过账日期: string;
  凭证类型: string;
  文本: string;
  总账科目: string;
  科目名称: string;
  借方金额: number;
  贷方金额: number;
  来源模块: string;
  是否为人工直入: boolean;
  抽样总体?: string;
  风险分数?: number;
  入样理由?: string;
  抽中概率?: number;
};

export type SamplingPopulationScope = "full_population" | "risk_signals";

export type SamplingStrategy =
  | "risk_directed"
  | "random"
  | "monetary_unit"
  | "stratified"
  | "unpredictable";

export type SamplingCoverageConstraints = {
  min_per_month?: number;
  min_per_account_category?: number;
  max_same_risk_signal_ratio?: number;
};

/**
 * 抽样计划写入契约。
 * method 为旧抽样端点的传输字段；其余字段用于受信抽样计划与可重放底稿。
 */
export type SamplingPlanRequest = {
  plan_name?: string;
  population_scope: SamplingPopulationScope;
  strategy: SamplingStrategy;
  method: "by_rule" | "random" | "monetary_unit" | "stratified";
  size: number;
  seed: number;
  years?: number[];
  coverage_constraints: SamplingCoverageConstraints;
  stratify_by?: "account_category" | "month" | "voucher_type";
  stratify_mode?: "proportional" | "equal";
  unpredictable?: boolean;
};

export type SamplingPopulationSnapshot = {
  population_id?: string;
  data_version?: string;
  classification_revision?: string;
  analysis_scope_revision?: string;
  currency_scope?: {
    selected_currency?: string | null;
    currencies?: string[];
    basis?: string;
  };
  rule_version?: string;
  population_membership_revision?: string;
  years?: number[];
  row_count: number;
  voucher_count: number;
  amount_absolute?: number | null;
  amount_basis?: string;
  dimension_counts?: Record<string, Record<string, number>>;
};

export type SamplingCoverageCheck = {
  id: string;
  label: string;
  target: string | number;
  actual?: string | number;
  status: "met" | "unmet" | "unknown";
};

export type SamplingSelectionTrace = {
  selection_id?: string;
  strategy: SamplingStrategy;
  seed?: number;
  generated_at?: string;
  reproducible?: boolean;
  inclusion_probability_available?: boolean;
  uniform_inclusion_probability?: number | null;
  statistical_projection_allowed?: boolean;
  projection_boundary?: string;
  coverage_checks?: SamplingCoverageCheck[];
  selected_voucher_count?: number;
  selected_voucher_keys?: string[];
  selected_voucher_keys_digest?: string;
};

export type SamplesResponse = {
  method?: string;
  sample_rows: number;
  voucher_count: number;
  samples: SampleRow[];
  requested_plan?: SamplingPlanRequest;
  population_snapshot?: SamplingPopulationSnapshot;
  selection_trace?: SamplingSelectionTrace;
};

export type AuditCaseStatus =
  | "draft"
  | "planned"
  | "in_progress"
  | "pending_evidence"
  | "concluded"
  | "closed";

export type AuditAssertion = {
  assertion_id: string;
  name: string;
  rationale?: string;
  status?: "open" | "supported" | "exception";
  title?: string;
  risk_statement?: string;
  financial_statement_assertions?: string[];
  affected_accounts?: string[];
  created_by?: string;
  created_at?: string;
};

export type EvidenceRef = {
  evidence_id: string;
  evidence_type:
    | "source_coordinate"
    | "risk_signal"
    | "attachment"
    | "management_explanation"
    | "counter_evidence";
  title: string;
  status: "active" | "missing" | "requested" | "verified" | "stale";
  locator?: Record<string, unknown>;
  note?: string;
  source_type?: string;
  source_ref?: AuditEvidenceSourceRef;
  description?: string;
  stale_reason?: string;
  stale_at?: string;
  assertion_ids?: string[];
  procedure_id?: string;
  scope?: CaseSnapshot;
  created_by?: string;
  created_at?: string;
};

export type AuditProcedure = {
  procedure_id: string;
  title: string;
  status: "planned" | "in_progress" | "completed" | "not_applicable";
  description?: string;
  owner?: string;
  result?: string;
  assertion_ids?: string[];
  performed_by?: string;
  performed_at?: string;
  created_by?: string;
  created_at?: string;
};

export type AuditConclusion = {
  outcome:
    | "pending"
    | "no_exception"
    | "reasonable_exception"
    | "exception"
    | "finding"
    | "insufficient_evidence"
    | "control_deficiency"
    | "misstatement"
    | "scope_limitation";
  summary?: string;
  misstatement_amount?: number;
  currency?: string;
  concluded_by?: string;
  concluded_at?: string;
  basis_evidence_ids?: string[];
  procedure_ids?: string[];
  unresolved_assertion_ids?: string[];
  override_reason?: string;
  status?: "active" | "stale";
  stale_reason?: string;
  stale_at?: string;
};

export type CaseSnapshot = {
  data_version?: string;
  ingest_run_id?: string;
  classification_revision?: string;
  analysis_scope_revision?: string;
  currency_scope?: string;
  rule_version?: string;
  rule_revision?: string;
  rule_run_id?: string;
  engine_revision?: string;
  result_hash?: string;
  years?: number[];
};

export type AuditCaseReadiness = {
  ready: boolean;
  blockers: Array<{
    code: string;
    message: string;
    assertion_ids?: string[];
    hard?: boolean;
  }>;
  assertion_count: number;
  verified_evidence_count: number;
  completed_procedure_count: number;
  unresolved_assertion_ids: string[];
  stale_evidence_ids: string[];
  pending_evidence_ids: string[];
  evidence_coverage?: Record<string, string[]>;
  procedure_coverage?: Record<string, string[]>;
};

export type AuditCaseIntegrity = {
  valid: boolean;
  checks: Record<string, boolean>;
  errors: Array<Record<string, unknown>>;
  event_count: number;
  snapshot_count: number;
  head_hash?: string;
};

export type CaseEvent = {
  event_id: string;
  event_type: "created" | "updated" | "stale" | "status_changed" | "concluded" | "reviewed";
  at: string;
  actor?: string;
  note?: string;
  from_status?: AuditCaseStatus;
  to_status?: AuditCaseStatus;
  canonical_event_type?: string;
  case_id?: string;
  case_version?: number;
  payload?: Record<string, unknown>;
  prev_hash?: string;
  event_hash?: string;
};

export type AuditCase = {
  case_id: string;
  title: string;
  risk: string;
  materiality: "unassessed" | "low" | "medium" | "high";
  owner?: string;
  status: AuditCaseStatus;
  kind?: "proposal" | "case";
  risk_domain?: string;
  tags?: string[];
  version?: number;
  source_candidate_ids: string[];
  source_candidate_group_id?: string;
  assertions: AuditAssertion[];
  evidence: EvidenceRef[];
  procedures: AuditProcedure[];
  conclusion: AuditConclusion;
  snapshot: CaseSnapshot;
  events: CaseEvent[];
  readiness: AuditCaseReadiness;
  integrity: AuditCaseIntegrity;
  signoffs?: Array<{
    signoff_id: string;
    actor: string;
    note: string;
    signed_at: string;
    status: "active" | "stale";
    stale_reason?: string;
  }>;
  created_at: string;
  updated_at: string;
  created_by?: string;
};

export type AuditCaseListResponse = {
  cases: AuditCase[];
  count: number;
  schema_version?: number;
  current_scope?: CaseSnapshot;
};

export type AuditEvidenceSourceRef = {
  source_asset_id?: string;
  file_hash?: string;
  sheet?: string;
  source_row?: number | null;
  source_column?: string;
  voucher_key?: string;
  line_key?: string;
  locator?: Record<string, unknown>;
};

export type AuditCaseMutationResponse = AuditCase;


export type ExpenseRow = { 年份: number; 费用类别: string; 金额: number; 占比: number };
export type OtherPnlMonthlyRow = {
  月份: number;
  投资收益: number;
  公允价值变动损益: number;
  其他收益: number;
  资产处置收益: number;
  营业外收入: number;
  营业外支出: number;
  信用减值损失: number;
  资产减值损失: number;
  所得税费用: number;
  净影响: number;
};
export type CostVarianceMonthlyRow = {
  月份: number;
  差异科目净额: number;
  差异绝对发生额: number;
  结转营业成本: number;
  结转存货: number;
  制造归集: number;
  期末五日占比: number;
  异常波动: boolean;
};
export type CostVarianceSummary = {
  variance_absolute_amount: number;
  variance_net_amount: number;
  cogs_impact: number;
  cogs_impact_ratio: number;
  inventory_impact: number;
  period_end_five_day_ratio: number;
  year_end_amount_ratio: number;
  active_month_count: number;
  recurring_monthly: boolean;
  largest_cogs_impact_month: number | null;
  largest_cogs_impact_amount: number;
  outlier_months: number[];
  interpretation: string;
};
export type ApMonthlyRow = { 月份: number; 暂估贷方增加: number; 暂估借方减少: number; 暂估净额: number };
export type ApSupplierRow = { 供应商: string; 暂估贷方增加: number; 暂估借方减少: number; 暂估净额: number };
export type OrMonthlyRow = { 月份: number; 其他应收S发生额: number; 其他应收H发生额: number; 其他应收净额: number };
export type OpMonthlyRow = { 月份: number; 其他应付预提H: number; 其他应付核销S: number; 其他应付净值: number };
export type BsMonthlyRow = { 月份: number; 借方发生额: number; 贷方发生额: number; 净变动: number };
export type BsAccountRow = { 科目编号: string; 科目名称: string; 借方发生额: number; 贷方发生额: number; 净变动: number; 占比: number };
export type AdjustmentSummaryRow = {
  凭证编号: string; 过账日期: string; 行数: number; 凭证类型: string; 命中关键词: string;
  反记账标识: string; 借方金额: number; 贷方金额: number; 最大行金额: number;
};

export const api = {
  health: () => request<Health>("/health"),
  listProjects: () => request<ProjectSummary[]>("/projects"),
  createProject: (name: string) =>
    request<ProjectSummary>("/projects", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  detectColumns: (projectId: string, files: File[]) => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    return upload<DetectResponse>(`/projects/${projectId}/ingest/detect`, form);
  },
  commitIngest: (projectId: string, files: File[], columnMapping: Record<string, string>) => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    form.append("column_mapping", JSON.stringify(columnMapping));
    return upload<IngestResponse>(`/projects/${projectId}/ingest/commit`, form);
  },

  getInsightJobs: (projectId: string) =>
    request<InsightJobsResponse>(`/projects/${projectId}/analysis/modules/insight/jobs`),
  getAnalysisQuality: (projectId: string) =>
    request<AnalysisQualityResponse>(
      `/projects/${projectId}/analysis/quality`,
    ),
  setAnalysisCurrencyScope: (projectId: string, currency: string) =>
    request<AnalysisQualityResponse>(
      `/projects/${projectId}/analysis/currency-scope`,
      { method: "POST", body: JSON.stringify({ currency }) },
    ),
  applyAnalysisQualityDecisions: (
    projectId: string,
    decisions: ClassificationDecision[],
  ) =>
    request<AnalysisQualityResponse>(
      `/projects/${projectId}/analysis/quality/decisions`,
      {
        method: "POST",
        body: JSON.stringify({ decisions }),
      },
    ),

  getModuleInsight: (projectId: string, moduleKey: string) =>
    request<{ module: string; cached: boolean; insight: ModuleInsight | null }>(
      `/projects/${projectId}/analysis/modules/${encodeURIComponent(moduleKey)}/insight`,
    ),
  generateModuleInsight: (
    projectId: string,
    moduleKey: string,
    opts?: { profileId?: string | null; apiKey?: string },
  ) =>
    request<{ module: string; insight: ModuleInsight }>(
      `/projects/${projectId}/analysis/modules/${encodeURIComponent(moduleKey)}/insight`,
      {
        method: "POST",
        body: JSON.stringify({
          profile_id: opts?.profileId ?? "",
          api_key: opts?.apiKey ?? "",
        }),
      },
    ),
  startModuleInsightJob: (
    projectId: string,
    moduleKey: string,
    opts?: { profileId?: string | null; apiKey?: string },
  ) =>
    request<{ module: string; job: InsightJob }>(
      `/projects/${projectId}/analysis/modules/${encodeURIComponent(moduleKey)}/insight/jobs`,
      {
        method: "POST",
        body: JSON.stringify({
          profile_id: opts?.profileId ?? "",
          api_key: opts?.apiKey ?? "",
        }),
      },
    ),
  applyModuleInsight: (projectId: string, moduleKey: string, indices?: number[]) =>
    request<{ added: number; skipped: number; errors: string[] }>(
      `/projects/${projectId}/analysis/modules/${encodeURIComponent(moduleKey)}/insight/apply`,
      { method: "POST", body: JSON.stringify({ indices: indices ?? null }) },
    ),
  moduleQuestions: (projectId: string, moduleKey: string) =>
    request<{ module: string; questions: { id: string; text: string }[] }>(
      `/projects/${projectId}/analysis/modules/${encodeURIComponent(moduleKey)}/questions`,
    ),
  incomeCostCategories: (projectId: string, year: number) =>
    request<{ year: number; categories: string[] }>(
      `/projects/${projectId}/analysis/income-cost/categories?year=${year}`,
    ),
  incomeCostMonthly: (projectId: string, year: number, category: string) =>
    request<{ year: number; category: string; rows: MonthlyRow[] }>(
      `/projects/${projectId}/analysis/income-cost/monthly?year=${year}&category=${encodeURIComponent(category)}`,
    ),
  incomeCostCustomers: (projectId: string, year: number, category: string) =>
    request<{ year: number; category: string; rows: CustomerRow[] }>(
      `/projects/${projectId}/analysis/income-cost/customers?year=${year}&category=${encodeURIComponent(category)}`,
    ),
  incomeCostDrilldownMonthly: (
    projectId: string,
    year: number,
    month: number,
    metric: "revenue" | "cost" | "gross",
    category: string,
  ) =>
    request<DrilldownResponse>(
      `/projects/${projectId}/analysis/income-cost/drilldown/monthly?year=${year}&month=${month}&metric=${metric}&category=${encodeURIComponent(category)}`,
    ),
  incomeCostDrilldownCustomer: (projectId: string, year: number, customer: string, category: string) =>
    request<DrilldownResponse>(
      `/projects/${projectId}/analysis/income-cost/drilldown/customer?year=${year}&customer=${encodeURIComponent(customer)}&category=${encodeURIComponent(category)}`,
    ),
  listCandidates: (projectId: string) =>
    request<{ groups: CandidateGroup[]; stats: CandidateStats }>(`/projects/${projectId}/candidates`),
  addCandidate: (
    projectId: string,
    body: {
      title: string;
      source_module: string;
      source_view: string;
      selector: Record<string, unknown>;
      reason?: string;
      tags?: string[];
      voucher_ids?: string[];
    },
  ) =>
    request<{ group: CandidateGroup; stats: CandidateStats }>(`/projects/${projectId}/candidates`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  deleteCandidate: (projectId: string, groupId: string) =>
    request<{ stats: CandidateStats }>(`/projects/${projectId}/candidates/${groupId}`, {
      method: "DELETE",
    }),
  updateCandidateStatus: (projectId: string, groupId: string, status: string) =>
    request<{ group: CandidateGroup; stats: CandidateStats }>(
      `/projects/${projectId}/candidates/${groupId}`,
      { method: "PATCH", body: JSON.stringify({ status }) },
    ),
  updateCandidate: (
    projectId: string,
    groupId: string,
    body: { status?: string; reason?: string; tags?: string[]; title?: string },
  ) =>
    request<{ group: CandidateGroup; stats: CandidateStats }>(
      `/projects/${projectId}/candidates/${groupId}`,
      { method: "PATCH", body: JSON.stringify(body) },
    ),
  getCandidateEntries: (projectId: string, groupId: string, limit = 200) =>
    request<{ group_id: string; row_count: number; voucher_count: number; rows: Record<string, unknown>[] }>(
      `/projects/${projectId}/candidates/${groupId}/entries?limit=${limit}`,
    ),
  buildProfiles: (projectId: string) =>
    request<{ years: number[]; profiles: Record<string, YearProfile>; financials: Record<string, unknown> }>(
      `/projects/${projectId}/pipeline/profiles`,
      { method: "POST" },
    ),
  getProfiles: (projectId: string) =>
    request<{ profiles: Record<string, YearProfile>; financials: Record<string, unknown> }>(
      `/projects/${projectId}/pipeline/profiles`,
    ),
  runCrossYear: (projectId: string) =>
    request<{ count: number; findings: CrossYearFinding[] }>(
      `/projects/${projectId}/pipeline/cross-year`,
      { method: "POST" },
    ),
  getCrossYear: (projectId: string) =>
    request<{ count: number; findings: CrossYearFinding[] }>(`/projects/${projectId}/pipeline/cross-year`),
  getRules: (projectId: string) =>
    request<RulesConfig>(`/projects/${projectId}/rules`),
  putRules: (projectId: string, rules: RulesConfig) =>
    request<RulesConfig>(`/projects/${projectId}/rules`, {
      method: "PUT",
      body: JSON.stringify(rules),
    }),
  getDefaultRules: (projectId: string) =>
    request<RulesConfig>(`/projects/${projectId}/rules/defaults`),
  runRules: (projectId: string) =>
    request<{ rules: RuleSummary[]; total_hits: number }>(
      `/projects/${projectId}/pipeline/rules/run`,
      { method: "POST" },
    ),
  getRuleResults: (projectId: string) =>
    request<{ rules: RuleSummary[]; total_hits: number }>(`/projects/${projectId}/pipeline/rules/results`),
  extractSamples: (
    projectId: string,
    body: SamplingPlanRequest,
  ) =>
    request<SamplesResponse>(
      `/projects/${projectId}/pipeline/samples`,
      { method: "POST", body: JSON.stringify(body) },
    ),
  getAgentState: (projectId: string) =>
    request<AgentStateResponse>(`/projects/${projectId}/agent/state`),
  setAgentContext: (projectId: string, pinned_context: AuditSelection | null) =>
    request<{ pinned_context: AuditSelection | null; suggestions: string[] }>(
      `/projects/${projectId}/agent/context`,
      { method: "PUT", body: JSON.stringify({ pinned_context }) },
    ),
  agentChat: (
    projectId: string,
    message: string,
    pinned_context?: AuditSelection | null,
    opts?: { profileId?: string | null; apiKey?: string },
  ) =>
    request<AgentChatResponse>(`/projects/${projectId}/agent/chat`, {
      method: "POST",
      body: JSON.stringify({
        message,
        pinned_context: pinned_context ?? null,
        profile_id: opts?.profileId ?? "",
        api_key: opts?.apiKey ?? "",
      }),
    }),
  createAgentSession: (projectId: string) =>
    request<AgentStateResponse>(`/projects/${projectId}/agent/sessions`, { method: "POST" }),
  activateAgentSession: (projectId: string, sessionId: string) =>
    request<AgentStateResponse>(
      `/projects/${projectId}/agent/sessions/${encodeURIComponent(sessionId)}/activate`,
      { method: "POST" },
    ),
  renameAgentSession: (projectId: string, sessionId: string, title: string) =>
    request<AgentStateResponse>(
      `/projects/${projectId}/agent/sessions/${encodeURIComponent(sessionId)}`,
      { method: "PATCH", body: JSON.stringify({ title }) },
    ),
  deleteAgentSession: (projectId: string, sessionId: string) =>
    request<AgentStateResponse>(
      `/projects/${projectId}/agent/sessions/${encodeURIComponent(sessionId)}`,
      { method: "DELETE" },
    ),
  /** @deprecated 等价于 createAgentSession；保留给旧调用方 */
  clearAgentThread: (projectId: string) =>
    request<{ ok: boolean }>(`/projects/${projectId}/agent/thread`, { method: "DELETE" }),
  resolveAgentAction: (projectId: string, actionId: string, decision: "approve" | "reject") =>
    request<{ action_id: string; status: string; result: Record<string, unknown>; ui_actions?: AgentUiAction[] }>(
      `/projects/${projectId}/agent/actions/${encodeURIComponent(actionId)}/${decision}`,
      { method: "POST" },
    ),

  getSamples: (projectId: string) =>
    request<SamplesResponse>(
      `/projects/${projectId}/pipeline/samples`,
    ),
  listAuditCases: (projectId: string) =>
    request<AuditCaseListResponse>(`/projects/${projectId}/audit-cases`),
  sourceDownloadUrl: (projectId: string, assetId: string) =>
    `${resolveApiBase()}/projects/${encodeURIComponent(projectId)}/sources/${encodeURIComponent(assetId)}/download`,
  getAuditCase: (projectId: string, caseId: string) =>
    request<AuditCaseMutationResponse>(
      `/projects/${projectId}/audit-cases/${encodeURIComponent(caseId)}`,
    ),
  createAuditCaseFromCandidate: (
    projectId: string,
    groupId: string,
    body: {
      formal?: boolean;
      title?: string;
      risk?: string;
      financial_statement_assertions?: string[];
      materiality?: AuditCase["materiality"];
      owner?: string;
      actor?: string;
      expected_version?: number;
    },
  ) =>
    request<AuditCaseMutationResponse>(
      `/projects/${projectId}/audit-cases/from-candidate/${encodeURIComponent(groupId)}`,
      { method: "POST", body: JSON.stringify(body) },
    ),
  promoteAuditCase: (
    projectId: string,
    caseId: string,
    body: { actor?: string; comment?: string; expected_version: number },
  ) =>
    request<AuditCaseMutationResponse>(
      `/projects/${projectId}/audit-cases/${encodeURIComponent(caseId)}/promote`,
      { method: "POST", body: JSON.stringify(body) },
    ),
  updateAuditCase: (
    projectId: string,
    caseId: string,
    patch: Partial<Pick<AuditCase, "title" | "risk" | "materiality" | "owner" | "status">> & {
      actor?: string;
      note?: string;
      expected_version: number;
    },
  ) =>
    request<AuditCaseMutationResponse>(`/projects/${projectId}/audit-cases/${encodeURIComponent(caseId)}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),
  addAuditCaseAssertion: (
    projectId: string,
    caseId: string,
    body: {
      title: string;
      risk_statement: string;
      financial_statement_assertions?: string[];
      affected_accounts?: string[];
      actor?: string;
      expected_version: number;
    },
  ) =>
    request<AuditCaseMutationResponse>(
      `/projects/${projectId}/audit-cases/${encodeURIComponent(caseId)}/assertions`,
      { method: "POST", body: JSON.stringify(body) },
    ),
  updateAuditCaseAssertion: (
    projectId: string,
    caseId: string,
    assertionId: string,
    body: {
      status?: AuditAssertion["status"];
      title?: string;
      risk_statement?: string;
      actor?: string;
      expected_version: number;
    },
  ) =>
    request<AuditCaseMutationResponse>(
      `/projects/${projectId}/audit-cases/${encodeURIComponent(caseId)}/assertions/${encodeURIComponent(assertionId)}`,
      { method: "PATCH", body: JSON.stringify(body) },
    ),
  addAuditCaseEvidence: (
    projectId: string,
    caseId: string,
    body: {
      source_type: string;
      source_ref: AuditEvidenceSourceRef;
      description?: string;
      status?: EvidenceRef["status"];
      assertion_ids?: string[];
      procedure_id?: string;
      actor?: string;
      expected_version: number;
    },
  ) =>
    request<AuditCaseMutationResponse>(
      `/projects/${projectId}/audit-cases/${encodeURIComponent(caseId)}/evidence`,
      { method: "POST", body: JSON.stringify(body) },
    ),
  updateAuditCaseEvidence: (
    projectId: string,
    caseId: string,
    evidenceId: string,
    body: {
      status?: EvidenceRef["status"];
      description?: string;
      source_ref?: AuditEvidenceSourceRef;
      assertion_ids?: string[];
      procedure_id?: string;
      actor?: string;
      expected_version: number;
    },
  ) =>
    request<AuditCaseMutationResponse>(
      `/projects/${projectId}/audit-cases/${encodeURIComponent(caseId)}/evidence/${encodeURIComponent(evidenceId)}`,
      { method: "PATCH", body: JSON.stringify(body) },
    ),
  addAuditCaseProcedure: (
    projectId: string,
    caseId: string,
    body: {
      title: string;
      description: string;
      assertion_ids?: string[];
      status?: "planned" | "in_progress" | "completed" | "not_applicable";
      result?: string;
      performed_by?: string;
      performed_at?: string;
      actor?: string;
      expected_version: number;
    },
  ) =>
    request<AuditCaseMutationResponse>(
      `/projects/${projectId}/audit-cases/${encodeURIComponent(caseId)}/procedures`,
      { method: "POST", body: JSON.stringify(body) },
    ),
  updateAuditCaseProcedure: (
    projectId: string,
    caseId: string,
    procedureId: string,
    body: {
      title?: string;
      description?: string;
      assertion_ids?: string[];
      status?: "planned" | "in_progress" | "completed" | "not_applicable";
      result?: string;
      performed_by?: string;
      performed_at?: string;
      actor?: string;
      expected_version: number;
    },
  ) =>
    request<AuditCaseMutationResponse>(
      `/projects/${projectId}/audit-cases/${encodeURIComponent(caseId)}/procedures/${encodeURIComponent(procedureId)}`,
      { method: "PATCH", body: JSON.stringify(body) },
    ),
  setAuditCaseConclusion: (
    projectId: string,
    caseId: string,
    body: {
      outcome: Exclude<AuditConclusion["outcome"], "pending">;
      summary: string;
      basis_evidence_ids?: string[];
      procedure_ids?: string[];
      unresolved_assertion_ids?: string[];
      override_reason?: string;
      misstatement_amount?: number;
      currency?: string;
      actor: string;
      expected_version: number;
    },
  ) =>
    request<AuditCaseMutationResponse>(
      `/projects/${projectId}/audit-cases/${encodeURIComponent(caseId)}/conclusion`,
      { method: "PUT", body: JSON.stringify(body) },
    ),
  appendAuditCaseEvent: (
    projectId: string,
    caseId: string,
    event: {
      event_type: "review" | "reviewed" | "signoff" | "signed_off";
      actor: string;
      note?: string;
      expected_version: number;
    },
  ) =>
    request<{
      case: AuditCaseMutationResponse;
      event: CaseEvent;
      integrity: { valid: boolean; event_count: number; head_hash?: string };
    }>(
      `/projects/${projectId}/audit-cases/${encodeURIComponent(caseId)}/events`,
      { method: "POST", body: JSON.stringify(event) },
    ).then((result) => result.case),
  getVerifyStatus: (projectId: string) =>
    request<{
      summary: {
        total: number;
        confirmed: number;
        high: number;
        medium: number;
        fallback: number;
        rules: number;
      };
      has_judgments: boolean;
    }>(`/projects/${projectId}/pipeline/verify`),
  runVerify: (
    projectId: string,
    body?: { profile_id?: string; api_key?: string; max_verify?: number },
  ) =>
    request<{
      summary: {
        total: number;
        confirmed: number;
        high: number;
        medium: number;
        fallback: number;
        rules: number;
      };
      judgments: Record<string, unknown[]>;
    }>(`/projects/${projectId}/pipeline/verify`, {
      method: "POST",
      body: JSON.stringify(body ?? {}),
    }),

  expenseCrossYear: (projectId: string) =>
    request<{ years: number[]; rows: ExpenseRow[] }>(`/projects/${projectId}/analysis/expense/cross-year`),
  expenseDrilldown: (projectId: string, year: number, category: string) =>
    request<DrilldownResponse>(
      `/projects/${projectId}/analysis/expense/drilldown?year=${year}&category=${encodeURIComponent(category)}`,
    ),
  otherPnlMonthly: (projectId: string, year: number) =>
    request<{ year: number; rows: OtherPnlMonthlyRow[] }>(
      `/projects/${projectId}/analysis/other-pnl/monthly?year=${year}`,
    ),
  costVarianceMonthly: (projectId: string, year: number) =>
    request<{ year: number; summary: CostVarianceSummary; rows: CostVarianceMonthlyRow[] }>(
      `/projects/${projectId}/analysis/cost-variance/monthly?year=${year}`,
    ),
  wcApMonthly: (projectId: string, year: number) =>
    request<{ year: number; rows: ApMonthlyRow[] }>(
      `/projects/${projectId}/analysis/working-capital/ap-accrual/monthly?year=${year}`,
    ),
  wcApSuppliers: (projectId: string, year: number, month: number) =>
    request<{ year: number; month: number; rows: ApSupplierRow[] }>(
      `/projects/${projectId}/analysis/working-capital/ap-accrual/suppliers?year=${year}&month=${month}`,
    ),
  wcOrMonthly: (projectId: string, year: number) =>
    request<{ year: number; rows: OrMonthlyRow[] }>(
      `/projects/${projectId}/analysis/working-capital/other-receivable/monthly?year=${year}`,
    ),
  wcOpMonthly: (projectId: string, year: number) =>
    request<{ year: number; rows: OpMonthlyRow[] }>(
      `/projects/${projectId}/analysis/working-capital/other-payable/monthly?year=${year}`,
    ),
  drilldown: (projectId: string, selector: Record<string, unknown>) =>
    request<DrilldownResponse>(`/projects/${projectId}/analysis/drilldown`, {
      method: "POST",
      body: JSON.stringify({ selector }),
    }),
  bsCategories: (projectId: string, year: number) =>
    request<{ year: number; categories: string[] }>(
      `/projects/${projectId}/analysis/balance-sheet/categories?year=${year}`,
    ),
  bsMonthly: (projectId: string, year: number, category: string) =>
    request<{ year: number; category: string; rows: BsMonthlyRow[] }>(
      `/projects/${projectId}/analysis/balance-sheet/monthly?year=${year}&category=${encodeURIComponent(category)}`,
    ),
  bsAccounts: (projectId: string, year: number, category: string) =>
    request<{ year: number; category: string; rows: BsAccountRow[] }>(
      `/projects/${projectId}/analysis/balance-sheet/accounts?year=${year}&category=${encodeURIComponent(category)}`,
    ),
  adjustmentSummary: (projectId: string, year: number) =>
    request<{ year: number; row_count: number; rows: AdjustmentSummaryRow[] }>(
      `/projects/${projectId}/analysis/adjustment/summary?year=${year}`,
    ),
  adjustmentDrilldown: (projectId: string, year: number, voucherId: string, date?: string) =>
    request<DrilldownResponse>(
      `/projects/${projectId}/analysis/adjustment/drilldown?year=${year}&voucher_id=${encodeURIComponent(voucherId)}${date ? `&date=${encodeURIComponent(date)}` : ""}`,
    ),
  listLlmProfiles: () =>
    request<{ profiles: LlmProfile[]; secret_backend: string }>("/llm/profiles"),
  getLlmPresets: () => request<{ presets: LlmPreset[]; secret_backend: string }>("/llm/presets"),
  saveLlmProfile: (body: {
    profile_id?: string;
    profile_name: string;
    base_url: string;
    model: string;
    set_default?: boolean;
  }) =>
    request<{ profile: LlmProfile }>("/llm/profiles", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  deleteLlmProfile: (profileId: string) =>
    request<{ ok: boolean }>(`/llm/profiles/${encodeURIComponent(profileId)}`, { method: "DELETE" }),
  saveLlmKey: (profileId: string, apiKey: string) =>
    request<{ ok: boolean }>(`/llm/profiles/${encodeURIComponent(profileId)}/key`, {
      method: "POST",
      body: JSON.stringify({ api_key: apiKey }),
    }),
  forgetLlmKey: (profileId: string) =>
    request<{ ok: boolean }>(`/llm/profiles/${encodeURIComponent(profileId)}/key`, { method: "DELETE" }),
  fetchLlmModels: (body: { profile_id?: string; base_url?: string; api_key?: string }) =>
    request<LlmModelsResponse>("/llm/models", { method: "POST", body: JSON.stringify(body) }),
  pingLlm: (body: { profile_id?: string; base_url?: string; model?: string; api_key?: string }) =>
    request<{ ok: boolean; model: string; reply_preview: string; key_source: string }>("/llm/ping", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  getActiveLlm: (profileId?: string) =>
    request<{
      profile_id: string;
      profile_name: string;
      model: string;
      base_url: string;
      key_configured: boolean;
      key_source: string;
    }>(`/llm/active${profileId ? `?profile_id=${encodeURIComponent(profileId)}` : ""}`),

  exportExcelUrl: (projectId: string) =>
    `${resolveApiBase()}/projects/${projectId}/pipeline/export`,

  /** Fetch Excel as blob — avoids SPA navigation hang from raw `<a href>` downloads. */
  exportExcel: async (projectId: string): Promise<{ blob: Blob; filename: string }> => {
    const res = await fetch(
      `${resolveApiBase()}/projects/${projectId}/pipeline/export`,
    );
    if (!res.ok) {
      const text = await res.text();
      throw new Error(text || res.statusText);
    }
    const blob = await res.blob();
    const disposition = res.headers.get("content-disposition") ?? "";
    const utf8 = disposition.match(/filename\*=UTF-8''([^;]+)/i);
    const plain = disposition.match(/filename="?([^";]+)"?/i);
    let filename = `audit_sample_${projectId.slice(0, 8)}.xlsx`;
    if (utf8?.[1]) {
      try {
        filename = decodeURIComponent(utf8[1]);
      } catch {
        filename = utf8[1];
      }
    } else if (plain?.[1]) {
      filename = plain[1];
    }
    return { blob, filename };
  },
};
