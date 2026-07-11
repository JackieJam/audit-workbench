import type { ProjectSummary } from "@/api/client";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useEnsureProfiles } from "@/hooks/useEnsureProfiles";
import { useModuleInsightStatus } from "@/hooks/useModuleInsight";
import { AnalysisProgressBar } from "@/components/AnalysisProgressBar";
import { AdjustmentPanel } from "@/components/AdjustmentPanel";
import { BalanceSheetPanel } from "@/components/BalanceSheetPanel";
import { CrossYearPanel } from "@/components/CrossYearPanel";
import { ExpensePanel } from "@/components/ExpensePanel";
import { IncomeCostPanel } from "@/components/IncomeCostPanel";
import { OtherPnlPanel } from "@/components/OtherPnlPanel";
import { ProfilePanel } from "@/components/ProfilePanel";
import { WorkingCapitalPanel } from "@/components/WorkingCapitalPanel";
import { useWorkspace, type FinanceModule } from "@/context/WorkspaceContext";

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
  useEnsureProfiles(project);
  const insightStatus = useModuleInsightStatus(project?.project_id);
  const quality = useQuery({
    queryKey: ["analysis-quality", project?.project_id],
    queryFn: () => api.getAnalysisQuality(project!.project_id),
    enabled: !!project?.project_id && project.years.length > 0,
  });
  const qualityWarnings = Object.entries(quality.data?.years ?? {}).flatMap(([year, item]) => {
    const warnings: string[] = [];
    if (item.mixed_document_currency) warnings.push(`${year}年存在多种凭证币，未提供本位币金额时不可直接汇总`);
    if (item.unclassified_amount_ratio > 0.05) warnings.push(`${year}年未分类金额占比 ${(item.unclassified_amount_ratio * 100).toFixed(1)}%`);
    if (item.amount_sign_confidence < 0.2) warnings.push(`${year}年金额方向识别置信度较低`);
    return warnings;
  });
  const mixedCurrencyBlocked = Object.values(quality.data?.years ?? {}).some(
    (item) => item.mixed_document_currency,
  );

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
          {qualityWarnings.length > 0 && (
            <div className="placeholder-card">
              <strong>数据口径提示：</strong>{qualityWarnings.join("；")}
            </div>
          )}
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
