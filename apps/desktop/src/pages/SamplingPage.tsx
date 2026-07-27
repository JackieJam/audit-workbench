import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type ProjectSummary } from "@/api/client";
import { RulesPanel } from "@/components/RulesPanel";
import { useLlm } from "@/context/LlmContext";

type Props = { project: ProjectSummary | null };

const METHODS = [
  { id: "by_rule", label: "按规则筛选" },
  { id: "random", label: "随机抽样" },
  { id: "all", label: "全量（不抽样）" },
  { id: "by_account_weight", label: "按科目金额加权" },
  { id: "monetary_unit", label: "货币单位抽样" },
  { id: "stratified", label: "分层抽样" },
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
  // Delay revoke so Safari / Chrome finish reading the blob
  window.setTimeout(() => URL.revokeObjectURL(url), 2_000);
}

export function SamplingPage({ project }: Props) {
  const queryClient = useQueryClient();
  const { selectedProfileId } = useLlm();

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
    mutationFn: (body: { method: string; size?: number }) =>
      api.extractSamples(project!.project_id, body),
    onSuccess: () => samples.refetch(),
  });

  const runVerify = useMutation({
    mutationFn: () =>
      api.runVerify(project!.project_id, {
        profile_id: selectedProfileId ?? "",
        max_verify: 50,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["verify-status", project?.project_id] });
      queryClient.invalidateQueries({ queryKey: ["rule-results", project?.project_id] });
    },
  });

  const downloadExcel = useMutation({
    mutationFn: () => api.exportExcel(project!.project_id),
    onSuccess: ({ blob, filename }) => triggerBlobDownload(blob, filename),
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
  const needsCrossYear =
    project.years.length >= 2 && (crossYear.data?.count ?? 0) === 0 && !crossYear.isLoading;
  const verifySummary = verifyStatus.data?.summary;

  return (
    <section className="page">
      <h2>抽样底稿</h2>
      <p className="lead">
        <strong>{project.project_name}</strong> — 规则配置 → 规则筛选 → 抽样 →（可选）LLM 核验 → Excel 导出
      </p>

      <RulesPanel projectId={project.project_id} />

      {needsCrossYear && (
        <div className="pipeline-hint">
          <p className="muted">尚未运行跨年稽核。部分跨年规则依赖该结果，建议先运行。</p>
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
            {extract.isPending ? "抽样中…" : `2. ${m.label}`}
          </button>
        ))}
      </div>

      <div className="pipeline-actions">
        <button
          type="button"
          className="btn-ghost"
          disabled={runVerify.isPending}
          onClick={() => runVerify.mutate()}
        >
          {runVerify.isPending ? "核验中（可能需数分钟）…" : "3. LLM 核验命中凭证"}
        </button>
        {verifySummary && verifyStatus.data?.has_judgments && (
          <span className="muted">
            核验 {verifySummary.confirmed} 条确认 · 高风险 {verifySummary.high} · fallback{" "}
            {verifySummary.fallback}
          </span>
        )}
        <button
          type="button"
          className="btn-primary"
          disabled={downloadExcel.isPending}
          onClick={() => downloadExcel.mutate()}
        >
          {downloadExcel.isPending ? "生成中…" : "4. 下载 Excel 底稿"}
        </button>
      </div>

      {extract.isError && <p className="error">{String(extract.error)}</p>}
      {runVerify.isError && <p className="error">核验失败：{String(runVerify.error)}</p>}
      {runCrossYear.isError && <p className="error">跨年失败：{String(runCrossYear.error)}</p>}
      {downloadExcel.isError && <p className="error">下载失败：{String(downloadExcel.error)}</p>}
      {downloadExcel.isSuccess && !downloadExcel.isPending && (
        <p className="muted">Excel 已开始下载；若浏览器弹出「另存为」，请选本机文件夹（避开 iCloud Drive）。</p>
      )}

      {samples.data && (
        <p className="muted">
          当前样本 {samples.data.voucher_count} 凭证 · {samples.data.sample_rows} 行
          {!verifyStatus.data?.has_judgments && " · 未跑 LLM 核验时导出核验列为空"}
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
