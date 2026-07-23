const base = import.meta.env.VITE_API_BASE ?? "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
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
  unclassified_amount: number;
  unclassified_amount_ratio: number;
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
  | "working_capital"
  | "balance_sheet"
  | "adjustment"
  | "profile"
  | "cross";

export type AgentUiAction =
  | { type: "navigate_main"; tab: "finance" | "suspects" | "sampling" }
  | { type: "finance_module"; module: FinanceModuleId }
  | { type: "set_finance_year"; year: number }
  | { type: "pin_selection"; context: AuditSelection }
  | { type: "invalidate_queries"; queryKey: unknown[] };

export type AgentChatResponse = {
  reply: string;
  tool_calls: Array<{ tool: string; args: Record<string, unknown>; result: Record<string, unknown> }>;
  pinned_context?: AuditSelection | null;
  suggestions: string[];
  needs_api_key?: boolean;
  ui_actions?: AgentUiAction[];
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
  module_key: string;
  stage: string;
  stage_label: string;
  percent: number;
  status: "running" | "done" | "error";
  started_at?: string;
  updated_at?: string;
  error?: string | null;
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
};


export type ExpenseRow = { 年份: number; 费用类别: string; 金额: number; 占比: number };
export type OtherPnlMonthlyRow = {
  月份: number;
  投资收益: number;
  营业外收入: number;
  营业外支出: number;
  净影响: number;
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
    request<{ data_version: string; years: Record<string, AnalysisQuality> }>(
      `/projects/${projectId}/analysis/quality`,
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
  runRules: (projectId: string) =>
    request<{ rules: RuleSummary[]; total_hits: number }>(
      `/projects/${projectId}/pipeline/rules/run`,
      { method: "POST" },
    ),
  getRuleResults: (projectId: string) =>
    request<{ rules: RuleSummary[]; total_hits: number }>(`/projects/${projectId}/pipeline/rules/results`),
  extractSamples: (
    projectId: string,
    body: { method?: string; size?: number; seed?: number },
  ) =>
    request<{ method: string; sample_rows: number; voucher_count: number; samples: SampleRow[] }>(
      `/projects/${projectId}/pipeline/samples`,
      { method: "POST", body: JSON.stringify(body) },
    ),
  getAgentState: (projectId: string) =>
    request<{
      messages: Array<{ role: string; content: string; at?: string; tool_calls?: AgentChatResponse["tool_calls"] }>;
      pinned_context: AuditSelection | null;
      suggestions?: string[];
      updated_at?: string;
      audit_events?: Array<Record<string, unknown>>;
    }>(
      `/projects/${projectId}/agent/state`,
    ),
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
  clearAgentThread: (projectId: string) =>
    request<{ ok: boolean }>(`/projects/${projectId}/agent/thread`, { method: "DELETE" }),
  resolveAgentAction: (projectId: string, actionId: string, decision: "approve" | "reject") =>
    request<{ action_id: string; status: string; result: Record<string, unknown>; ui_actions?: AgentUiAction[] }>(
      `/projects/${projectId}/agent/actions/${encodeURIComponent(actionId)}/${decision}`,
      { method: "POST" },
    ),

  getSamples: (projectId: string) =>
    request<{ sample_rows: number; voucher_count: number; samples: SampleRow[] }>(
      `/projects/${projectId}/pipeline/samples`,
    ),

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

  exportExcelUrl: (projectId: string) => `${base}/projects/${projectId}/pipeline/export`,

  /** Fetch Excel as blob — avoids SPA navigation hang from raw `<a href>` downloads. */
  exportExcel: async (projectId: string): Promise<{ blob: Blob; filename: string }> => {
    const res = await fetch(`${base}/projects/${projectId}/pipeline/export`);
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
