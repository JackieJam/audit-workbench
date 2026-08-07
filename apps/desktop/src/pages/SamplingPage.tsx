import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  type ProjectSummary,
  type SamplingPlanRequest,
  type SamplingStrategy,
} from "@/api/client";
import { RulesPanel } from "@/components/RulesPanel";
import { EmptyState } from "@/components/EmptyState";
import { useLlm } from "@/context/LlmContext";

type Props = { project: ProjectSummary | null };

const STRATEGIES: Array<{
  id: SamplingStrategy;
  label: string;
  description: string;
  inference: string;
}> = [
  {
    id: "risk_directed",
    label: "风险定向",
    description: "从规则、模型或人工识别的高风险项目中选择。",
    inference: "不能据此统计推断总体",
  },
  {
    id: "random",
    label: "随机抽样",
    description: "每个抽样单位使用相同随机机制选取。",
    inference: "服务端回传抽中概率后可评估推断",
  },
  {
    id: "monetary_unit",
    label: "货币单元抽样（MUS）",
    description: "金额越大的项目被选中概率越高。",
    inference: "适合高估风险，需单一可比币种",
  },
  {
    id: "stratified",
    label: "分层抽样",
    description: "按科目、月份或凭证类型分层后分别选择。",
    inference: "需披露各层总体与样本分配",
  },
  {
    id: "unpredictable",
    label: "不可预测样本",
    description: "使用临时随机种子补充常规计划难以覆盖的项目。",
    inference: "探索性补充，不替代统计样本",
  },
];

function triggerBlobDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.rel = "noopener";
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 2_000);
}

function nextSeed() {
  const fallback = Math.max(1, Date.now() % 2_147_483_647);
  if (typeof crypto === "undefined" || !crypto.getRandomValues) return fallback;
  const value = crypto.getRandomValues(new Uint32Array(1))[0] % 2_147_483_647;
  return Math.max(1, value);
}

function numericConstraint(value: string) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : undefined;
}

function strategyMethod(
  strategy: SamplingStrategy,
): SamplingPlanRequest["method"] {
  if (strategy === "risk_directed") return "by_rule";
  if (strategy === "unpredictable") return "random";
  return strategy;
}

function strategyLabel(strategy?: SamplingStrategy) {
  return STRATEGIES.find((item) => item.id === strategy)?.label ?? "历史样本";
}

function currencyScopeLabel(
  scope?: {
    selected_currency?: string | null;
    currencies?: string[];
    basis?: string;
  },
) {
  if (!scope) return "未回传";
  const currencies = scope.selected_currency
    ? [scope.selected_currency]
    : scope.currencies ?? [];
  const basis = scope.basis === "company" ? "本位币金额" : scope.basis === "document" ? "凭证币金额" : "";
  return [currencies.join("、") || "未维护币种", basis].filter(Boolean).join(" · ");
}

export function SamplingPage({ project }: Props) {
  const queryClient = useQueryClient();
  const { selectedProfileId } = useLlm();
  const [strategy, setStrategy] = useState<SamplingStrategy>("risk_directed");
  const [populationScope, setPopulationScope] =
    useState<SamplingPlanRequest["population_scope"]>("risk_signals");
  const [sampleSize, setSampleSize] = useState(50);
  const [seed, setSeed] = useState(20260730);
  const [minPerMonth, setMinPerMonth] = useState("");
  const [minPerAccount, setMinPerAccount] = useState("");
  const [maxRiskRatio, setMaxRiskRatio] = useState("");
  const [stratifyBy, setStratifyBy] =
    useState<NonNullable<SamplingPlanRequest["stratify_by"]>>("account_category");
  const [stratifyMode, setStratifyMode] =
    useState<NonNullable<SamplingPlanRequest["stratify_mode"]>>("proportional");
  const [lastSubmittedPlan, setLastSubmittedPlan] =
    useState<SamplingPlanRequest | null>(null);

  const rules = useQuery({
    queryKey: ["rule-results", project?.project_id],
    queryFn: () => api.getRuleResults(project!.project_id),
    enabled: !!project?.project_id,
  });

  const samples = useQuery({
    queryKey: ["samples", project?.project_id],
    queryFn: () => api.getSamples(project!.project_id),
    enabled: !!project?.project_id,
  });

  const crossYear = useQuery({
    queryKey: ["cross-year-status", project?.project_id],
    queryFn: () => api.getCrossYear(project!.project_id),
    enabled: !!project?.project_id && (project?.years.length ?? 0) >= 2,
  });

  const verifyStatus = useQuery({
    queryKey: ["verify-status", project?.project_id],
    queryFn: () => api.getVerifyStatus(project!.project_id),
    enabled: !!project?.project_id,
  });

  const runCrossYear = useMutation({
    mutationFn: () => api.runCrossYear(project!.project_id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["cross-year"] });
      queryClient.invalidateQueries({ queryKey: ["cross-year-status", project?.project_id] });
    },
  });

  const runRules = useMutation({
    mutationFn: () => api.runRules(project!.project_id),
    onSuccess: () => {
      rules.refetch();
      queryClient.invalidateQueries({ queryKey: ["samples", project?.project_id] });
      queryClient.invalidateQueries({ queryKey: ["verify-status", project?.project_id] });
    },
  });

  const extract = useMutation({
    mutationFn: (plan: SamplingPlanRequest) =>
      api.extractSamples(project!.project_id, plan),
    onSuccess: (data, plan) => {
      setLastSubmittedPlan(data.requested_plan ?? plan);
      queryClient.setQueryData(["samples", project?.project_id], data);
      queryClient.invalidateQueries({ queryKey: ["verify-status", project?.project_id] });
    },
  });

  const [boundaryPreview, setBoundaryPreview] = useState<{
    endpoint: string;
    model: string;
    fields: string[];
    redaction: string;
    redaction_note?: string;
    plaintext_fields?: string[];
    pseudonym_fields?: string[];
    policy_version?: string;
    boundary_hash?: string;
    warning: string;
  } | null>(null);
  const [boundaryLoading, setBoundaryLoading] = useState(false);
  const [boundaryError, setBoundaryError] = useState<string | null>(null);
  const [pendingBoundaryHash, setPendingBoundaryHash] = useState<string | null>(null);

  const consentKey = (endpoint: string, policyVersion: string, boundaryHash: string) =>
    `audit-llm-boundary-consent:${endpoint}|${policyVersion}|${boundaryHash}`;

  const runVerify = useMutation({
    mutationFn: (opts: { boundary_hash: string }) => {
      const hash = (opts.boundary_hash || pendingBoundaryHash || "").trim();
      if (!hash) {
        throw new Error("缺少 boundary_hash：请先预览并确认数据外发边界");
      }
      const sampleCount =
        (extract.data ?? samples.data)?.samples?.length
        ?? (extract.data ?? samples.data)?.voucher_count
        ?? 50;
      return api.runVerify(project!.project_id, {
        profile_id: selectedProfileId ?? "",
        max_verify: Math.max(50, Number(sampleCount) || 50),
        redaction: "pseudonym",
        confirm_data_boundary: true,
        verification_scope: "current_sample",
        boundary_hash: hash,
      });
    },
    onSuccess: (data) => {
      const boundary = data.data_boundary;
      if (boundary?.endpoint && boundary.policy_version && boundary.boundary_hash) {
        try {
          localStorage.setItem(
            consentKey(boundary.endpoint, boundary.policy_version, boundary.boundary_hash),
            new Date().toISOString(),
          );
        } catch {
          /* ignore storage failures */
        }
      }
      setBoundaryPreview(null);
      setBoundaryError(null);
      setPendingBoundaryHash(null);
      queryClient.invalidateQueries({ queryKey: ["verify-status", project?.project_id] });
      queryClient.invalidateQueries({ queryKey: ["rule-results", project?.project_id] });
    },
  });

  const requestVerify = async () => {
    if (!project) return;
    setBoundaryLoading(true);
    setBoundaryPreview(null);
    setBoundaryError(null);
    try {
      const { data_boundary: boundary } = await api.getVerifyBoundary(project.project_id, {
        profile_id: selectedProfileId ?? "",
        redaction: "pseudonym",
      });
      const hash = boundary.boundary_hash || "";
      setPendingBoundaryHash(hash || null);
      const remembered =
        !!boundary.endpoint &&
        !!boundary.policy_version &&
        !!hash &&
        !!localStorage.getItem(consentKey(boundary.endpoint, boundary.policy_version, hash));
      if (remembered) {
        runVerify.mutate({ boundary_hash: hash });
        return;
      }
      setBoundaryPreview(boundary);
    } catch (err) {
      setBoundaryError(err instanceof Error ? err.message : String(err));
    } finally {
      setBoundaryLoading(false);
    }
  };

  const downloadExcel = useMutation({
    mutationFn: () => api.exportExcel(project!.project_id),
    onSuccess: ({ blob, filename }) => triggerBlobDownload(blob, filename),
  });

  if (!project) {
    return (
      <section className="page">
        <h2>抽样底稿</h2>
        <EmptyState
          kind="clipboard"
          title="尚未选择项目"
          description="选择项目并上传序时账后，即可定义总体、配置抽样计划并留存可复核底稿。"
        />
      </section>
    );
  }

  const result = extract.data ?? samples.data;
  const rows = result?.samples ?? [];
  const needsCrossYear =
    project.years.length >= 2 && (crossYear.data?.count ?? 0) === 0 && !crossYear.isLoading;
  const verifySummary = verifyStatus.data?.summary;
  const activePlan = result?.requested_plan ?? lastSubmittedPlan;
  const population = result?.population_snapshot;
  const trace = result?.selection_trace;
  const hasTrustedBasis = !!population && !!trace;
  const riskSignalCount = rules.data?.total_hits;

  const submitPlan = () => {
    const size = Math.max(1, Math.min(500, Math.round(sampleSize || 1)));
    setSampleSize(size);
    const maxRatio = numericConstraint(maxRiskRatio);
    const plan: SamplingPlanRequest = {
      plan_name: `${project.project_name}-${strategyLabel(strategy)}`,
      population_scope: populationScope,
      strategy,
      method: strategyMethod(strategy),
      size,
      seed,
      years: project.years,
      coverage_constraints: {
        min_per_month: numericConstraint(minPerMonth),
        min_per_account_category: numericConstraint(minPerAccount),
        max_same_risk_signal_ratio:
          strategy !== "risk_directed" || maxRatio === undefined
            ? undefined
            : Math.min(maxRatio, 100) / 100,
      },
      ...(strategy === "stratified" ? { stratify_by: stratifyBy, stratify_mode: stratifyMode } : {}),
      ...(strategy === "unpredictable" ? { unpredictable: true } : {}),
    };
    extract.mutate(plan);
  };

  const needsRiskSignals =
    populationScope === "risk_signals" && rules.data === undefined;

  return (
    <section className="page sampling-page">
      <h2>审计抽样计划</h2>
      <p className="lead">
        <strong>{project.project_name}</strong> — 先冻结总体与风险信号，再选择方法；风险定向与统计抽样分别披露。
      </p>

      <ol className="sampling-layer-flow" aria-label="抽样三层口径">
        <li>
          <span>1</span>
          <div>
            <strong>Population · 完整总体</strong>
            <small>{project.years.join("、")} 年序时账 · {project.total_rows.toLocaleString()} 行</small>
          </div>
        </li>
        <li>
          <span>2</span>
          <div>
            <strong>RiskSignal · 完整风险命中</strong>
            <small>{riskSignalCount === undefined ? "尚未运行规则" : `${riskSignalCount.toLocaleString()} 条命中`}</small>
          </div>
        </li>
        <li>
          <span>3</span>
          <div>
            <strong>SampleSelection · 最终选择</strong>
            <small>{strategyLabel(strategy)} · 计划 {sampleSize} 个凭证</small>
          </div>
        </li>
      </ol>

      <details className="sampling-rules-disclosure">
        <summary>
          <span>
            <strong>风险信号规则</strong>
            <small>规则命中必须完整保留，不受最终样本量截断</small>
          </span>
          <span className="candidate-status">
            {riskSignalCount === undefined ? "待运行" : `${riskSignalCount} 条`}
          </span>
        </summary>
        <div className="sampling-rules-disclosure__body">
          <RulesPanel projectId={project.project_id} />
          {needsCrossYear && (
            <div className="pipeline-hint">
              <p>部分规则依赖跨年结果，建议先完成跨年稽核。</p>
              <button
                type="button"
                className="btn-ghost"
                disabled={runCrossYear.isPending}
                onClick={() => runCrossYear.mutate()}
              >
                {runCrossYear.isPending ? "跨年分析中…" : "运行跨年稽核"}
              </button>
            </div>
          )}
          <div className="pipeline-actions">
            <button
              type="button"
              className="btn-primary"
              onClick={() => runRules.mutate()}
              disabled={runRules.isPending}
            >
              {runRules.isPending ? "执行中…" : "运行规则并冻结风险信号"}
            </button>
            <span className="muted">
              {riskSignalCount === undefined
                ? "运行后才可从“风险信号”总体抽样"
                : `当前完整命中 ${riskSignalCount.toLocaleString()} 条`}
            </span>
          </div>
          {rules.data && rules.data.rules.length > 0 && (
            <div className="rule-summary">
              {rules.data.rules
                .filter((rule) => rule.count > 0)
                .slice(0, 8)
                .map((rule) => (
                  <span key={rule.rule_name} className="tag">
                    {rule.rule_name}: {rule.count}
                  </span>
                ))}
            </div>
          )}
        </div>
      </details>

      <div className="sampling-plan-grid">
        <section className="sampling-plan-card sampling-plan-card--wide">
          <div className="sampling-card-head">
            <div>
              <span className="sampling-eyebrow">01 · 选择目的</span>
              <h3>抽样方法</h3>
            </div>
            <span className="muted">不同目的不可混称</span>
          </div>
          <div className="sampling-strategy-grid">
            {STRATEGIES.map((item) => (
              <label
                key={item.id}
                className={`sampling-strategy${strategy === item.id ? " is-selected" : ""}`}
              >
                <input
                  type="radio"
                  name="sampling-strategy"
                  value={item.id}
                  checked={strategy === item.id}
                  onChange={() => {
                    setStrategy(item.id);
                    if (item.id === "unpredictable") setSeed(nextSeed());
                  }}
                />
                <strong>{item.label}</strong>
                <span>{item.description}</span>
                <small>{item.inference}</small>
              </label>
            ))}
          </div>
        </section>

        <section className="sampling-plan-card">
          <div className="sampling-card-head">
            <div>
              <span className="sampling-eyebrow">02 · 冻结范围</span>
              <h3>抽样总体</h3>
            </div>
          </div>
          <div className="sampling-choice-list">
            <label className={populationScope === "full_population" ? "is-selected" : ""}>
              <input
                type="radio"
                name="population-scope"
                checked={populationScope === "full_population"}
                onChange={() => setPopulationScope("full_population")}
              />
              <span>
                <strong>完整序时账总体</strong>
                <small>{project.total_rows.toLocaleString()} 行；需服务端冻结数据/币种版本</small>
              </span>
            </label>
            <label className={populationScope === "risk_signals" ? "is-selected" : ""}>
              <input
                type="radio"
                name="population-scope"
                checked={populationScope === "risk_signals"}
                onChange={() => setPopulationScope("risk_signals")}
              />
              <span>
                <strong>当前完整风险命中</strong>
                <small>{riskSignalCount === undefined ? "尚未运行规则" : `${riskSignalCount} 条命中`}</small>
              </span>
            </label>
          </div>
        </section>

        <section className="sampling-plan-card">
          <div className="sampling-card-head">
            <div>
              <span className="sampling-eyebrow">03 · 可复现参数</span>
              <h3>样本量与随机种子</h3>
            </div>
          </div>
          <div className="sampling-field-grid">
            <label>
              样本量（凭证）
              <input
                type="number"
                min={1}
                max={500}
                value={sampleSize}
                onChange={(event) =>
                  setSampleSize(Math.min(500, Math.max(0, Number(event.target.value))))
                }
              />
            </label>
            <label>
              随机种子
              <input
                type="number"
                min={1}
                value={seed}
                readOnly={strategy === "unpredictable"}
                onChange={(event) => setSeed(Math.max(1, Number(event.target.value)))}
              />
            </label>
          </div>
          {strategy === "unpredictable" && (
            <button type="button" className="btn-ghost sampling-seed-button" onClick={() => setSeed(nextSeed())}>
              重新生成临时种子
            </button>
          )}
          {strategy === "stratified" && (
            <div className="sampling-field-grid sampling-field-grid--secondary">
              <label>
                分层维度
                <select value={stratifyBy} onChange={(event) => setStratifyBy(event.target.value as typeof stratifyBy)}>
                  <option value="account_category">科目大类</option>
                  <option value="month">月份</option>
                  <option value="voucher_type">凭证类型</option>
                </select>
              </label>
              <label>
                分配方式
                <select value={stratifyMode} onChange={(event) => setStratifyMode(event.target.value as typeof stratifyMode)}>
                  <option value="proportional">按总体比例</option>
                  <option value="equal">各层等量</option>
                </select>
              </label>
            </div>
          )}
        </section>

        <section className="sampling-plan-card sampling-plan-card--wide">
          <div className="sampling-card-head">
            <div>
              <span className="sampling-eyebrow">04 · 防止样本扎堆</span>
              <h3>覆盖约束</h3>
            </div>
            <span className="muted">留空表示不设置</span>
          </div>
          <div className="sampling-constraint-grid">
            <label>
              <span>每月至少</span>
              <span><input type="number" min={0} value={minPerMonth} onChange={(event) => setMinPerMonth(event.target.value)} /> 个</span>
            </label>
            <label>
              <span>每个科目大类至少</span>
              <span><input type="number" min={0} value={minPerAccount} onChange={(event) => setMinPerAccount(event.target.value)} /> 个</span>
            </label>
            <label>
              <span>同一风险信号最多</span>
              <span>
                <input
                  type="number"
                  min={1}
                  max={100}
                  value={maxRiskRatio}
                  disabled={strategy !== "risk_directed"}
                  placeholder={strategy === "risk_directed" ? "不设置" : "仅风险定向"}
                  onChange={(event) => setMaxRiskRatio(event.target.value)}
                /> %
              </span>
            </label>
          </div>
          <p className="muted">
            留空时保持所选方法的原始概率机制；设置月份或科目覆盖后，系统会明确标记为不可直接统计推断。
            单一风险信号占比只用于风险定向抽样，未达成的约束不会静默保存。
          </p>
        </section>
      </div>

      {needsRiskSignals && (
        <div className="sampling-notice sampling-notice--warning">
          当前选择“风险信号”总体，但规则尚未运行。请先在上方运行规则并确认完整命中数。
        </div>
      )}

      <div className="sampling-submit">
        <div>
          <strong>{strategyLabel(strategy)}</strong>
          <span>
            {populationScope === "full_population" ? "完整序时账总体" : "完整风险命中"} · {sampleSize} 个凭证 · seed {seed}
          </span>
        </div>
        <button
          type="button"
          className="btn-primary"
          disabled={extract.isPending || needsRiskSignals || sampleSize < 1}
          onClick={submitPlan}
        >
          {extract.isPending ? "生成样本中…" : "执行抽样计划"}
        </button>
      </div>

      {extract.isError && <p className="error">抽样失败：{String(extract.error)}</p>}
      {runCrossYear.isError && <p className="error">跨年失败：{String(runCrossYear.error)}</p>}

      {result && (
        <section className="sampling-result-basis">
          <div className="sampling-card-head">
            <div>
              <span className="sampling-eyebrow">结果口径</span>
              <h3>{hasTrustedBasis ? "总体与选择轨迹已确认" : "兼容模式：关键口径待服务端确认"}</h3>
            </div>
            <span className={`sampling-basis-status ${hasTrustedBasis ? "is-confirmed" : "is-warning"}`}>
              {hasTrustedBasis
                ? `可重放 · ${trace?.statistical_projection_allowed ? "可统计推断" : "不可统计推断"}`
                : "不可统计推断"}
            </span>
          </div>
          {!hasTrustedBasis && (
            <p className="sampling-result-warning">
              服务端尚未回传总体快照、币种/数据版本、抽中概率和覆盖约束达成情况。当前结果可作为工作样本，
              但不能仅凭本页声称总体已冻结或进行统计推断。
            </p>
          )}
          {hasTrustedBasis && !trace?.statistical_projection_allowed && trace?.projection_boundary && (
            <p className="sampling-result-warning">{trace.projection_boundary}</p>
          )}
          <dl className="sampling-basis-grid">
            <div>
              <dt>抽样目的</dt>
              <dd>{strategyLabel(activePlan?.strategy)}</dd>
            </div>
            <div>
              <dt>总体</dt>
              <dd>
                {population
                  ? `${population.voucher_count.toLocaleString()} 凭证`
                  : activePlan
                    ? activePlan.population_scope === "full_population" ? "完整序时账（待确认）" : "风险信号（待确认）"
                    : "历史结果未回传"}
              </dd>
            </div>
            <div>
              <dt>样本</dt>
              <dd>{result.voucher_count.toLocaleString()} 凭证 · {result.sample_rows.toLocaleString()} 行</dd>
            </div>
            <div>
              <dt>随机种子</dt>
              <dd>{trace?.seed ?? activePlan?.seed ?? "未回传"}</dd>
            </div>
            <div>
              <dt>数据 / 分类版本</dt>
              <dd>{population?.data_version ?? "未回传"} / {population?.classification_revision ?? "未回传"}</dd>
            </div>
            <div>
              <dt>币种口径</dt>
              <dd>{currencyScopeLabel(population?.currency_scope)}</dd>
            </div>
          </dl>
          {trace?.coverage_checks && trace.coverage_checks.length > 0 && (
            <div className="sampling-coverage-checks">
              {trace.coverage_checks.map((check) => (
                <span key={check.id} className={`sampling-check sampling-check--${check.status}`}>
                  {check.label}：{check.status === "met" ? "达成" : check.status === "unmet" ? "未达成" : "待确认"}
                </span>
              ))}
            </div>
          )}
        </section>
      )}

      <div className="sampling-post-actions">
        <button
          type="button"
          className="btn-ghost"
          disabled={runVerify.isPending || boundaryLoading || rows.length === 0}
          onClick={() => {
            void requestVerify();
          }}
        >
          {runVerify.isPending || boundaryLoading
            ? "辅助核验中（可能需数分钟）…"
            : "LLM 核验当前样本（非审计结论）"}
        </button>
        {verifySummary && verifyStatus.data?.has_judgments && (
          <span className="muted">
            已确认 {verifySummary.confirmed} · 待核验 {verifySummary.pending_review ?? verifySummary.fallback} · 高风险 {verifySummary.high}
            {typeof verifySummary.confirmation_rate === "number"
              ? ` · 确认率 ${(verifySummary.confirmation_rate * 100).toFixed(0)}%`
              : ""}
            （当前样本核验 · 明细已伪名化外发）
          </span>
        )}
        {verifyStatus.data?.verification_freshness?.status === "stale" && (
          <span className="muted">
            历史 LLM 核验已过期（与当前抽样不一致），请重新核验当前样本
          </span>
        )}
        <button
          type="button"
          className="btn-primary"
          disabled={downloadExcel.isPending || rows.length === 0}
          onClick={() => downloadExcel.mutate()}
        >
          {downloadExcel.isPending ? "生成中…" : "下载 Excel 抽样底稿"}
        </button>
      </div>

      {boundaryPreview && (
        <div className="llm-boundary-dialog" role="dialog" aria-modal="true" aria-labelledby="llm-boundary-title">
          <h3 id="llm-boundary-title">确认数据外发边界</h3>
          <p className="muted">发送前请确认以下内容；取消则不会调用模型。</p>
          <dl className="llm-boundary-meta">
            <div>
              <dt>即将调用</dt>
              <dd><code>{boundaryPreview.endpoint}</code></dd>
            </div>
            <div>
              <dt>模型</dt>
              <dd><code>{boundaryPreview.model}</code></dd>
            </div>
            <div>
              <dt>发送字段</dt>
              <dd>
                {boundaryPreview.fields.map((field) => {
                  const starred = (boundaryPreview.pseudonym_fields ?? []).includes(field);
                  return (
                    <span key={field} className="llm-boundary-field">
                      {field}{starred ? "*" : ""}
                    </span>
                  );
                })}
              </dd>
            </div>
          </dl>
          <p className="muted">
            * 已伪名化。注意：「{(boundaryPreview.plaintext_fields ?? ["文本"]).join("、")}」仍为原文外发。
          </p>
          <p className="muted">{boundaryPreview.redaction_note ?? boundaryPreview.warning}</p>
          <div className="upload-actions">
            <button type="button" className="btn-ghost" onClick={() => setBoundaryPreview(null)}>
              取消
            </button>
            <button
              type="button"
              className="btn-primary"
              disabled={runVerify.isPending}
              onClick={() =>
                runVerify.mutate({
                  boundary_hash: boundaryPreview.boundary_hash || pendingBoundaryHash || "",
                })
              }
            >
              确认并发送
            </button>
          </div>
        </div>
      )}

      {boundaryError && <p className="error">预览数据边界失败：{boundaryError}</p>}
      {runVerify.isError && <p className="error">辅助核验失败：{String(runVerify.error)}</p>}
      {downloadExcel.isError && <p className="error">下载失败：{String(downloadExcel.error)}</p>}
      {downloadExcel.isSuccess && !downloadExcel.isPending && (
        <p className="muted">Excel 已开始下载；底稿中的统计结论仍应以服务端回传口径为准。</p>
      )}

      {rows.length > 0 ? (
        <div className="detail-table-wrap sampling-result-table">
          <table className="detail-table">
            <thead>
              <tr>
                <th>凭证编号</th>
                <th>过账日期</th>
                <th>科目</th>
                <th>借方</th>
                <th>贷方</th>
                <th>风险分数</th>
                <th>入样理由</th>
                <th>来源</th>
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 100).map((row, index) => (
                <tr key={`${row.凭证编号}-${index}`}>
                  <td>{row.凭证编号}</td>
                  <td>{row.过账日期}</td>
                  <td>{row.科目名称 || row.总账科目}</td>
                  <td>{row.借方金额 ? row.借方金额.toLocaleString() : "—"}</td>
                  <td>{row.贷方金额 ? row.贷方金额.toLocaleString() : "—"}</td>
                  <td>{row.风险分数 ?? "未回传"}</td>
                  <td>{row.入样理由 || "未回传"}</td>
                  <td>{row.来源模块}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState
          kind="clipboard"
          title="尚无样本"
          description="确认总体、方法、样本量和覆盖约束后执行抽样，结果及口径将在此显示。"
        />
      )}
    </section>
  );
}
