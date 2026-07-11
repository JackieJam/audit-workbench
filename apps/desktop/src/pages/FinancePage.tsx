import type { ProjectSummary } from "@/api/client";
import { useEnsureProfiles } from "@/hooks/useEnsureProfiles";
import { useModuleInsightStatus } from "@/hooks/useModuleInsight";
import { AnalysisProgressBar } from "@/components/AnalysisProgressBar";
import { AdjustmentPanel } from "@/components/AdjustmentPanel";
import { BalanceSheetPanel } from "@/components/BalanceSheetPanel";
import { CrossYearPanel } from "@/components/CrossYearPanel";
import { ExpensePanel } from "@/components/ExpensePanel";
import { IncomeCostPanel } from "@/components/IncomeCostPanel";
import { ProfilePanel } from "@/components/ProfilePanel";
import { WorkingCapitalPanel } from "@/components/WorkingCapitalPanel";
import { useWorkspace, type FinanceModule } from "@/context/WorkspaceContext";

type Props = { project: ProjectSummary | null };

const MODULES: { id: FinanceModule; label: string }[] = [
  { id: "income", label: "收入成本" },
  { id: "expense", label: "费用" },
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
