import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type ProjectSummary } from "@/api/client";

const FINAL_STATUS = "人工直入最终样本";

type Props = {
  project: ProjectSummary | null;
};

function formatMoney(v: number) {
  return v.toLocaleString("zh-CN", { maximumFractionDigits: 0 });
}

export function SuspectsPage({ project }: Props) {
  const queryClient = useQueryClient();

  const candidates = useQuery({
    queryKey: ["candidates", project?.project_id],
    queryFn: () => api.listCandidates(project!.project_id),
    enabled: !!project?.project_id,
  });

  const markFinal = useMutation({
    mutationFn: (groupId: string) =>
      api.updateCandidateStatus(project!.project_id, groupId, FINAL_STATUS),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["candidates", project?.project_id] });
    },
  });

  const remove = useMutation({
    mutationFn: (groupId: string) => api.deleteCandidate(project!.project_id, groupId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["candidates", project?.project_id] });
    },
  });

  if (!project) {
    return (
      <section className="page">
        <h2>疑点工作台</h2>
        <p className="lead">请先在左侧选择项目；从财务画像图表点选后可加入疑点库。</p>
      </section>
    );
  }

  const groups = candidates.data?.groups ?? [];
  const stats = candidates.data?.stats;

  return (
    <section className="page">
      <h2>疑点工作台</h2>
      <p className="lead">
        <strong>{project.project_name}</strong>
        {stats && (
          <>
            {" "}
            — {stats.active_groups} 组疑点 · {stats.active_vouchers} 凭证 · 金额合计{" "}
            {formatMoney(stats.amount_total)}
          </>
        )}
      </p>

      {candidates.isLoading && <p className="muted">加载疑点库…</p>}
      {candidates.isError && <p className="error">加载失败</p>}

      {!candidates.isLoading && groups.length === 0 && (
        <div className="placeholder-card">暂无疑点。在「财务画像」中点击图表数据点后可加入。</div>
      )}

      {groups.length > 0 && (
        <div className="candidate-list">
          {groups.map((g) => (
            <article key={g.group_id} className="candidate-card">
              <header>
                <h3>{g.title}</h3>
                <span className="candidate-status">{g.status}</span>
              </header>
              <p className="muted candidate-meta">
                {g.source_module} / {g.source_view} · {g.voucher_count} 凭证 · {g.row_count} 行 ·{" "}
                {formatMoney(g.amount_total)}
              </p>
              {g.reason && <p className="candidate-reason">{g.reason}</p>}
              {g.tags.length > 0 && (
                <div className="candidate-tags">
                  {g.tags.map((t) => (
                    <span key={t} className="tag">
                      {t}
                    </span>
                  ))}
                </div>
              )}
              <footer>
                <span className="muted">{g.created_at}</span>
                <div className="selection-bar__actions">
                  {g.status !== FINAL_STATUS && (
                    <button
                      type="button"
                      className="btn-ghost"
                      onClick={() => markFinal.mutate(g.group_id)}
                      disabled={markFinal.isPending}
                    >
                      直入最终样本
                    </button>
                  )}
                  <button
                    type="button"
                    className="btn-ghost danger"
                    onClick={() => remove.mutate(g.group_id)}
                    disabled={remove.isPending}
                  >
                    移除
                  </button>
                </div>
              </footer>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
