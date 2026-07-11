import { useMutation, useQuery } from "@tanstack/react-query";
import { api, type ProjectSummary } from "@/api/client";

type Props = { project: ProjectSummary | null };

const METHODS = [
  { id: "by_rule", label: "按规则筛选" },
  { id: "random", label: "随机抽样" },
  { id: "all", label: "全量（不抽样）" },
];

export function SamplingPage({ project }: Props) {
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

  const runRules = useMutation({
    mutationFn: () => api.runRules(project!.project_id),
    onSuccess: () => rules.refetch(),
  });

  const extract = useMutation({
    mutationFn: (body: { method: string; size?: number }) =>
      api.extractSamples(project!.project_id, body),
    onSuccess: () => samples.refetch(),
  });

  if (!project) {
    return (
      <section className="page">
        <h2>抽样底稿</h2>
        <p className="lead">请先在左侧选择项目并上传序时账。</p>
      </section>
    );
  }

  const rows = samples.data?.samples ?? [];

  return (
    <section className="page">
      <h2>抽样底稿</h2>
      <p className="lead">
        <strong>{project.project_name}</strong> — 规则筛选 → 抽样 → Excel 导出
      </p>

      <div className="pipeline-actions">
        <button type="button" className="btn-primary" onClick={() => runRules.mutate()} disabled={runRules.isPending}>
          {runRules.isPending ? "执行中…" : "1. 运行规则引擎"}
        </button>
        {rules.data && (
          <span className="muted">已命中 {rules.data.total_hits} 条规则结果</span>
        )}
      </div>

      {rules.data && rules.data.rules.length > 0 && (
        <div className="rule-summary">
          {rules.data.rules
            .filter((r) => r.count > 0)
            .slice(0, 8)
            .map((r) => (
              <span key={r.rule_name} className="tag">
                {r.rule_name}: {r.count}
              </span>
            ))}
        </div>
      )}

      <div className="sampling-controls">
        {METHODS.map((m) => (
          <button
            key={m.id}
            type="button"
            className="btn-ghost"
            disabled={extract.isPending}
            onClick={() => extract.mutate({ method: m.id, size: 50 })}
          >
            2. {m.label}
          </button>
        ))}
        <a className="btn-primary" href={api.exportExcelUrl(project.project_id)} download>
          3. 下载 Excel 底稿
        </a>
      </div>

      {extract.isError && <p className="error">{String(extract.error)}</p>}

      {samples.data && (
        <p className="muted">
          当前样本 {samples.data.voucher_count} 凭证 · {samples.data.sample_rows} 行
        </p>
      )}

      {rows.length > 0 ? (
        <div className="detail-table-wrap">
          <table className="detail-table">
            <thead>
              <tr>
                <th>凭证编号</th>
                <th>过账日期</th>
                <th>科目</th>
                <th>借方</th>
                <th>贷方</th>
                <th>来源</th>
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 100).map((r, i) => (
                <tr key={i}>
                  <td>{r.凭证编号}</td>
                  <td>{r.过账日期}</td>
                  <td>{r.科目名称 || r.总账科目}</td>
                  <td>{r.借方金额 ? r.借方金额.toLocaleString() : "—"}</td>
                  <td>{r.贷方金额 ? r.贷方金额.toLocaleString() : "—"}</td>
                  <td>{r.来源模块}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="placeholder-card">运行规则并选择抽样方式后，样本清单将显示于此。</div>
      )}
    </section>
  );
}
