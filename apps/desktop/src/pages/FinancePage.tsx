import type { ProjectSummary } from "@/api/client";
import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useEnsureProfiles } from "@/hooks/useEnsureProfiles";
import { useModuleInsightStatus } from "@/hooks/useModuleInsight";
import { AnalysisProgressBar } from "@/components/AnalysisProgressBar";
import { AdjustmentPanel } from "@/components/AdjustmentPanel";
import { BalanceSheetPanel } from "@/components/BalanceSheetPanel";
import { CrossYearPanel } from "@/components/CrossYearPanel";
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
  { id: "other_pnl", label: "营业外与投资收益" },
  { id: "working_capital", label: "暂估往来" },
  { id: "balance_sheet", label: "资产负债" },
  { id: "adjustment", label: "调账冲销" },
  { id: "profile", label: "统计画像" },
  { id: "cross", label: "跨年稽核" },
];

export function FinancePage({ project }: Props) {
  const { financeModule, setFinanceModule } = useWorkspace();
  const { pinSelection } = useAgent();
  useEnsureProfiles(project);
  const insightStatus = useModuleInsightStatus(project?.project_id);
  const quality = useQuery({
    queryKey: ["analysis-quality", ...(project ? projectDataKey(project) : [null, null])],
    queryFn: () => api.getAnalysisQuality(project!.project_id),
    enabled: !!project?.project_id && project.years.length > 0,
    ...financialAnalysisQueryOptions,
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
  const mixedCurrencyBlocked = Object.values(quality.data?.years ?? {}).some(
    (item) => item.mixed_document_currency,
  );
  const hasQualityReview = Object.values(quality.data?.years ?? {}).some(
    (item) => item.unclassified_amount > 0,
  ) || Boolean(quality.data?.classification_decisions?.length);

  useEffect(() => {
    if (!project || (financeModule !== "profile" && financeModule !== "cross")) return;
    const label = financeModule === "profile" ? "统计画像" : "跨年稽核";
    pinSelection(moduleOverviewSelection(financeModule, label));
  }, [financeModule, project?.project_id, pinSelection]);

  if (project && mixedCurrencyBlocked) {
    return (
      <section className="page">
        <h2>财务画像</h2>
        <p className="lead"><strong>{project.project_name}</strong></p>
        <div className="placeholder-card">
          检测到多种凭证币且未提供公司代码货币金额。为避免把不同币种直接相加，分析图表已暂停；请上传本位币金额列或按币种拆分项目。
        </div>
      </section>
    );
  }

  return (
    <section className="page">
      <h2>财务画像</h2>
      {!project ? (
        <p className="lead">请先在左侧选择或创建项目，并上传序时账。</p>
      ) : (
        <>
          <p className="lead">
            <strong>{project.project_name}</strong> — {project.total_rows.toLocaleString()} 行
            {project.years.length > 0 ? `（${project.years.join("、")} 年）` : ""}
          </p>
          <AnalysisProgressBar
            projectId={project.project_id}
            phase={insightStatus.phase}
          />
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
                <DataQualityReview project={project} quality={quality.data} />
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
          {financeModule === "income" && <IncomeCostPanel project={project} />}
          {financeModule === "expense" && <ExpensePanel project={project} />}
          {financeModule === "other_pnl" && <OtherPnlPanel project={project} />}
          {financeModule === "working_capital" && (
            <WorkingCapitalPanel project={project} />
          )}
          {financeModule === "balance_sheet" && (
            <BalanceSheetPanel project={project} />
          )}
          {financeModule === "adjustment" && <AdjustmentPanel project={project} />}
          {financeModule === "profile" && <ProfilePanel project={project} />}
          {financeModule === "cross" && <CrossYearPanel project={project} />}
        </>
      )}
    </section>
  );
}
