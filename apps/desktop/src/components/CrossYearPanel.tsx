import { useMutation, useQuery } from "@tanstack/react-query";
import { api, type CrossYearFinding, type ProjectSummary } from "@/api/client";

type Props = { project: ProjectSummary };

function severityClass(s: string) {
  if (s === "高") return "severity-high";
  if (s === "中") return "severity-med";
  return "severity-low";
}

export function CrossYearPanel({ project }: Props) {
  const findings = useQuery({
    queryKey: ["cross-year", project.project_id],
    queryFn: () => api.getCrossYear(project.project_id),
    enabled: project.years.length >= 2,
  });

  const run = useMutation({
    mutationFn: () => api.runCrossYear(project.project_id),
    onSuccess: () => findings.refetch(),
  });

  if (project.years.length < 2) {
    return <p className="muted">跨年稽核需要至少上传两个年度的序时账。</p>;
  }

  const list: CrossYearFinding[] = findings.data?.findings ?? [];

  return (
    <div className="cross-year-panel">
      <div className="pipeline-actions">
        <button type="button" className="btn-primary" onClick={() => run.mutate()} disabled={run.isPending}>
          {run.isPending ? "稽核中…" : "运行跨年交叉稽核"}
        </button>
        {run.isError && <span className="error">{String(run.error)}</span>}
      </div>

      {findings.isLoading && <p className="muted">加载稽核结果…</p>}

      {!findings.isLoading && list.length === 0 && (
        <div className="placeholder-card">暂无异常发现。点击上方按钮执行七类跨年检测。</div>
      )}

      {list.length > 0 && (
        <div className="finding-list">
          {list.map((f, i) => (
            <article key={`${f.category}-${i}`} className="finding-card">
              <header>
                <h3>{f.category}</h3>
                <span className={`severity-badge ${severityClass(f.severity)}`}>{f.severity}</span>
              </header>
              <p>{f.description}</p>
              <p className="muted">
                涉及年份 {f.years_involved.join("、")} · {f.voucher_ids.length} 凭证 · 金额{" "}
                {f.amount.toLocaleString("zh-CN", { maximumFractionDigits: 0 })}
              </p>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}
