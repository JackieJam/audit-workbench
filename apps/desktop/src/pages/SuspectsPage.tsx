import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type CandidateGroup, type ProjectSummary } from "@/api/client";
import { DetailTable } from "@/components/DetailTable";

const FINAL_STATUS = "人工直入最终样本";
const DEFAULT_STATUS = "候选";
const EXCLUDED_STATUS = "排除";
const STATUS_OPTIONS = [DEFAULT_STATUS, FINAL_STATUS, EXCLUDED_STATUS] as const;

type Props = {
  project: ProjectSummary | null;
};

function formatMoney(v: number) {
  return v.toLocaleString("zh-CN", { maximumFractionDigits: 0 });
}

function CandidateDetail({
  projectId,
  group,
  onSaved,
}: {
  projectId: string;
  group: CandidateGroup;
  onSaved: () => void;
}) {
  const [reason, setReason] = useState(group.reason || "");
  const [tagsText, setTagsText] = useState(group.tags.join(", "));
  const [status, setStatus] = useState(group.status || DEFAULT_STATUS);

  const entries = useQuery({
    queryKey: ["candidate-entries", projectId, group.group_id],
    queryFn: () => api.getCandidateEntries(projectId, group.group_id),
  });

  const save = useMutation({
    mutationFn: () =>
      api.updateCandidate(projectId, group.group_id, {
        status,
        reason,
        tags: tagsText
          .split(/[,，]/)
          .map((t) => t.trim())
          .filter(Boolean),
      }),
    onSuccess: () => onSaved(),
  });

  return (
    <div className="candidate-detail">
      <div className="candidate-detail__edit">
        <label>
          <span>状态</span>
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            {STATUS_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label className="candidate-detail__wide">
          <span>理由</span>
          <textarea value={reason} rows={2} onChange={(e) => setReason(e.target.value)} />
        </label>
        <label className="candidate-detail__wide">
          <span>标签（逗号分隔）</span>
          <input type="text" value={tagsText} onChange={(e) => setTagsText(e.target.value)} />
        </label>
        <button
          type="button"
          className="btn-primary"
          disabled={save.isPending}
          onClick={() => save.mutate()}
        >
          {save.isPending ? "保存中…" : "保存修改"}
        </button>
        {save.isError && <p className="error">{String(save.error)}</p>}
      </div>

      <div className="candidate-detail__entries">
        <h4>
          凭证明细
          {entries.data && (
            <span className="muted">
              {" "}
              · {entries.data.voucher_count} 凭证 · 显示 {entries.data.row_count} 行
            </span>
          )}
        </h4>
        {entries.isLoading && <p className="muted">加载明细…</p>}
        {entries.isError && <p className="error">明细加载失败</p>}
        {entries.data && (
          <DetailTable rows={entries.data.rows} totalRows={entries.data.row_count} />
        )}
      </div>
    </div>
  );
}

export function SuspectsPage({ project }: Props) {
  const queryClient = useQueryClient();
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [moduleFilter, setModuleFilter] = useState<string>("all");

  const candidates = useQuery({
    queryKey: ["candidates", project?.project_id],
    queryFn: () => api.listCandidates(project!.project_id),
    enabled: !!project?.project_id,
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["candidates", project?.project_id] });
  };

  const markFinal = useMutation({
    mutationFn: (groupId: string) =>
      api.updateCandidateStatus(project!.project_id, groupId, FINAL_STATUS),
    onSuccess: invalidate,
  });

  const exclude = useMutation({
    mutationFn: (groupId: string) =>
      api.updateCandidateStatus(project!.project_id, groupId, EXCLUDED_STATUS),
    onSuccess: invalidate,
  });

  const remove = useMutation({
    mutationFn: (groupId: string) => api.deleteCandidate(project!.project_id, groupId),
    onSuccess: () => {
      setExpandedId(null);
      invalidate();
    },
  });

  const groups = candidates.data?.groups ?? [];
  const stats = candidates.data?.stats;

  const modules = useMemo(
    () => sortedUnique(groups.map((g) => g.source_module).filter(Boolean)),
    [groups],
  );

  const filtered = useMemo(() => {
    return groups.filter((g) => {
      if (statusFilter !== "all" && (g.status || DEFAULT_STATUS) !== statusFilter) return false;
      if (moduleFilter !== "all" && g.source_module !== moduleFilter) return false;
      return true;
    });
  }, [groups, statusFilter, moduleFilter]);

  if (!project) {
    return (
      <section className="page">
        <h2>疑点工作台</h2>
        <p className="lead">请先在左侧选择项目；从财务画像图表点选后可加入疑点库。</p>
      </section>
    );
  }

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

      {groups.length > 0 && (
        <div className="suspect-filters">
          <label>
            状态
            <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
              <option value="all">全部</option>
              {STATUS_OPTIONS.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>
          <label>
            来源模块
            <select value={moduleFilter} onChange={(e) => setModuleFilter(e.target.value)}>
              <option value="all">全部</option>
              {modules.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </label>
          <span className="muted">
            显示 {filtered.length} / {groups.length}
          </span>
        </div>
      )}

      {candidates.isLoading && <p className="muted">加载疑点库…</p>}
      {candidates.isError && <p className="error">加载失败</p>}

      {!candidates.isLoading && groups.length === 0 && (
        <div className="placeholder-card">暂无疑点。在「财务画像」中点击图表数据点后可加入。</div>
      )}

      {!candidates.isLoading && groups.length > 0 && filtered.length === 0 && (
        <div className="placeholder-card">当前筛选条件下无匹配疑点。</div>
      )}

      {filtered.length > 0 && (
        <div className="candidate-list">
          {filtered.map((g) => {
            const open = expandedId === g.group_id;
            return (
              <article key={g.group_id} className={`candidate-card${open ? " is-open" : ""}`}>
                <header>
                  <button
                    type="button"
                    className="candidate-card__toggle"
                    onClick={() => setExpandedId(open ? null : g.group_id)}
                  >
                    <h3>{g.title}</h3>
                    <span className="muted">{open ? "收起" : "展开明细"}</span>
                  </button>
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
                    {g.status !== EXCLUDED_STATUS && (
                      <button
                        type="button"
                        className="btn-ghost"
                        onClick={() => exclude.mutate(g.group_id)}
                        disabled={exclude.isPending}
                      >
                        排除
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
                {open && (
                  <CandidateDetail
                    projectId={project.project_id}
                    group={g}
                    onSaved={invalidate}
                  />
                )}
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}

function sortedUnique(values: string[]): string[] {
  return [...new Set(values)].sort((a, b) => a.localeCompare(b, "zh-CN"));
}
