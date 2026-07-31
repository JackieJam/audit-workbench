import type { ProjectSummary } from "@/api/client";
import { useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { EmptyState } from "@/components/EmptyState";
import { AdjustmentPanel } from "@/components/AdjustmentPanel";
import { BalanceSheetPanel } from "@/components/BalanceSheetPanel";
import { CrossYearPanel } from "@/components/CrossYearPanel";
import { CostVariancePanel } from "@/components/CostVariancePanel";
import { DataQualityReview } from "@/components/DataQualityReview";
import { ExpensePanel } from "@/components/ExpensePanel";
import { IncomeCostPanel } from "@/components/IncomeCostPanel";
import { OtherPnlPanel } from "@/components/OtherPnlPanel";
import { ProfilePanel } from "@/components/ProfilePanel";
import { WorkingCapitalPanel } from "@/components/WorkingCapitalPanel";
import { useAgent } from "@/context/AgentContext";
import { useWorkspace, type FinanceModule } from "@/context/WorkspaceContext";
import { moduleOverviewSelection } from "@/lib/agentContext";
import { financialAnalysisQueryOptions, projectDataKey } from "@/lib/queryPolicy";

type Props = { project: ProjectSummary | null };

const MODULES: { id: FinanceModule; label: string }[] = [
  { id: "income", label: "收入成本" },
  { id: "expense", label: "费用" },
  { id: "other_pnl", label: "其他损益" },
  { id: "cost_variance", label: "成本差异" },
  { id: "working_capital", label: "暂估往来" },
  { id: "balance_sheet", label: "资产负债" },
  { id: "adjustment", label: "调账冲销" },
  { id: "profile", label: "统计画像" },
  { id: "cross", label: "跨年稽核" },
];

export function FinancePage({ project }: Props) {
  const { financeModule, setFinanceModule } = useWorkspace();
  const { pinSelection } = useAgent();
  const queryClient = useQueryClient();
  const qualityKey = ["analysis-quality", ...(project ? projectDataKey(project) : [null, null])];
  const quality = useQuery({
    queryKey: qualityKey,
    queryFn: () => api.getAnalysisQuality(project!.project_id),
    enabled: !!project?.project_id && project.years.length > 0,
    ...financialAnalysisQueryOptions,
  });
  const currencyMutation = useMutation({
    mutationFn: (currency: string) => api.setAnalysisCurrencyScope(project!.project_id, currency),
    onSuccess: async (next) => {
      queryClient.setQueryData(qualityKey, next);
      await queryClient.invalidateQueries({
        predicate: (query) =>
          query.queryKey.includes(project!.project_id) &&
          query.queryKey[0] !== "analysis-quality",
      });
    },
  });
  const qualityWarnings = Object.entries(quality.data?.years ?? {}).flatMap(([year, item]) => {
    const warnings: { key: string; text: string }[] = [];
    if (item.review_required_amount_ratio > 0.05) {
      warnings.push({
        key: `${year}-unclassified`,
        text: `${year}年待决策金额占比 ${(item.review_required_amount_ratio * 100).toFixed(1)}%`,
      });
    }
    if (item.amount_sign_confidence < 0.2) {
      warnings.push({
        key: `${year}-sign`,
        text: `${year}年金额方向识别置信度较低`,
      });
    }
    return warnings;
  });
  const currencyOverview = quality.data?.currency_overview ?? quality.data?.years ?? {};
  const mixedCurrencyProject = Object.values(currencyOverview).some(
    (item) => item.mixed_document_currency,
  );
  const selectedCurrency = quality.data?.analysis_currency_scope ?? null;
  const mixedCurrencyBlocked = mixedCurrencyProject && !selectedCurrency;
  const currencyOptions = Array.from(
    new Set(Object.values(currencyOverview).flatMap((item) => item.currencies)),
  ).sort();
  const scopedProject = project && selectedCurrency
    ? {
        ...project,
        years: project.years.filter((year) =>
          currencyOverview[String(year)]?.currency_distribution.some(
            (row) => row.currency === selectedCurrency && row.row_count > 0,
          ),
        ),
      }
    : project;
  const hasQualityReview = Object.values(quality.data?.years ?? {}).some(
    (item) => item.unclassified_amount > 0,
  ) || Boolean(quality.data?.classification_decisions?.length);

  useEffect(() => {
    if (!project || (financeModule !== "profile" && financeModule !== "cross")) return;
    const label = financeModule === "profile" ? "统计画像" : "跨年稽核";
    pinSelection(moduleOverviewSelection(financeModule, label));
  }, [financeModule, project?.project_id, pinSelection]);

  if (project && quality.isLoading) {
    return (
      <section className="page">
        <h2>财务画像</h2>
        <p className="lead"><strong>{project.project_name}</strong></p>
        <p className="muted">正在识别金额与币种口径…</p>
      </section>
    );
  }

  if (project && mixedCurrencyBlocked) {
    return (
      <section className="page">
        <h2>财务画像</h2>
        <p className="lead">
          <strong>{project.project_name}</strong> — 检测到 {currencyOptions.join("、")} 多币种
        </p>
        <div className="chart-card currency-scope-card">
          <h3>选择财务画像的分析币种</h3>
          <p className="muted">
            当前未提供公司代码货币金额，系统不会把不同币种直接相加。请选择一个币种独立生成全部图表；
            下表仅用于对比各币种的数据规模，不包含汇率折算。
          </p>
          <div className="currency-scope-table">
            <div className="currency-scope-row currency-scope-row--header">
              <span>年度</span><span>币种</span><span>分录行</span><span>凭证数</span><span>绝对发生额（原币）</span>
            </div>
            {Object.entries(currencyOverview).flatMap(([year, item]) =>
              item.currency_distribution.map((row) => (
                <div className="currency-scope-row" key={`${year}-${row.currency}`}>
                  <span>{year}</span>
                  <strong>{row.currency}</strong>
                  <span>{row.row_count.toLocaleString()}</span>
                  <span>{row.voucher_count.toLocaleString()}</span>
                  <span>{row.absolute_entry_amount.toLocaleString("zh-CN", { maximumFractionDigits: 2 })} {row.currency}</span>
                </div>
              )),
            )}
          </div>
          <div className="year-segmented currency-scope-actions" aria-label="选择分析币种">
            {currencyOptions.map((currency) => (
              <button
                key={currency}
                type="button"
                disabled={currencyMutation.isPending}
                onClick={() => currencyMutation.mutate(currency)}
              >
                分析 {currency}
              </button>
            ))}
          </div>
          {currencyMutation.isError ? (
            <p className="error">{String(currencyMutation.error)}</p>
          ) : null}
          <p className="chart-hint muted">
            如需合并口径，请重新导入“本位币金额 / 公司代码货币价值”列；系统不会自动猜测汇率。
          </p>
        </div>
      </section>
    );
  }

  return (
    <section className="page">
      <h2>财务画像</h2>
      {!project ? (
        <EmptyState
          kind="project"
          title="尚未选择项目"
          description="在左侧选择或创建一个项目，并上传年度序时账后，这里将呈现完整的财务画像与风险分析。"
        />
      ) : (
        <>
          <p className="lead">
            <strong>{project.project_name}</strong> — {project.total_rows.toLocaleString()} 行
            {project.years.length > 0 ? `（${project.years.join("、")} 年）` : ""}
          </p>
          {mixedCurrencyProject && selectedCurrency ? (
            <details className="data-quality-strip currency-scope-strip">
              <summary>
                <span className="data-quality-strip__status" aria-hidden>¥</span>
                <span className="data-quality-strip__summary">
                  <strong>当前按 {selectedCurrency} 单币种分析</strong>
                  <span>其他币种未计入图表，也未执行隐式汇率折算</span>
                </span>
                <span className="data-quality-strip__action">切换币种</span>
              </summary>
              <div className="data-quality-strip__details">
                <div className="year-segmented" aria-label="切换分析币种">
                  {currencyOptions.map((currency) => (
                    <button
                      key={currency}
                      type="button"
                      className={selectedCurrency === currency ? "active" : ""}
                      disabled={currencyMutation.isPending}
                      onClick={() => currencyMutation.mutate(currency)}
                    >
                      {currency}
                    </button>
                  ))}
                </div>
                <p className="muted">切换后会重新计算画像、规则结果和模块 AI 分析；已加入疑点库的凭证不会被删除。</p>
                {currencyMutation.isError ? <p className="error">{String(currencyMutation.error)}</p> : null}
              </div>
            </details>
          ) : null}
          {quality.isError ? (
            <div className="data-quality-strip data-quality-strip--error" role="alert">
              <span>数据质量信息读取失败，当前图表仍可查看，但请先确认分析口径。</span>
              <button type="button" className="btn-ghost" onClick={() => quality.refetch()}>
                重试
              </button>
            </div>
          ) : quality.data && (qualityWarnings.length > 0 || hasQualityReview) ? (
            <details className="data-quality-strip">
              <summary>
                <span className="data-quality-strip__status" aria-hidden>!</span>
                <span className="data-quality-strip__summary">
                  <strong>{qualityWarnings.length ? "数据质量需复核" : "数据口径已识别"}</strong>
                  <span>
                    {qualityWarnings.length
                      ? `${qualityWarnings.length} 项待决策提示，图表未阻断`
                      : "当前没有高占比待决策项"}
                  </span>
                </span>
                <span className="data-quality-strip__action">查看口径</span>
              </summary>
              <div className="data-quality-strip__details">
                {qualityWarnings.length ? (
                  <ul>
                    {qualityWarnings.map((warning) => (
                      <li key={warning.key}>{warning.text}</li>
                    ))}
                  </ul>
                ) : null}
                <DataQualityReview project={scopedProject ?? project} quality={quality.data} />
              </div>
            </details>
          ) : null}
          <nav className="module-tabs module-tabs-scroll">
            {MODULES.map((m) => (
              <button
                key={m.id}
                type="button"
                className={financeModule === m.id ? "module-tab active" : "module-tab"}
                onClick={() => setFinanceModule(m.id)}
              >
                {m.label}
              </button>
            ))}
          </nav>
          {financeModule === "income" && <IncomeCostPanel project={scopedProject ?? project} />}
          {financeModule === "expense" && <ExpensePanel project={scopedProject ?? project} />}
          {financeModule === "other_pnl" && <OtherPnlPanel project={scopedProject ?? project} />}
          {financeModule === "cost_variance" && (
            <CostVariancePanel project={scopedProject ?? project} />
          )}
          {financeModule === "working_capital" && (
            <WorkingCapitalPanel project={scopedProject ?? project} />
          )}
          {financeModule === "balance_sheet" && (
            <BalanceSheetPanel project={scopedProject ?? project} />
          )}
          {financeModule === "adjustment" && <AdjustmentPanel project={scopedProject ?? project} />}
          {financeModule === "profile" && <ProfilePanel project={scopedProject ?? project} />}
          {financeModule === "cross" && <CrossYearPanel project={scopedProject ?? project} />}
        </>
      )}
    </section>
  );
}
