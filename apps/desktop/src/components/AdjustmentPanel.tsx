import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type AdjustmentSummaryRow, type ProjectSummary } from "@/api/client";
import { DrilldownPanel } from "@/components/DrilldownPanel";
import { ModuleInsightCard } from "@/components/ModuleInsightCard";
import { EmptyState } from "@/components/EmptyState";
import { useAgent } from "@/context/AgentContext";
import { moduleOverviewSelection } from "@/lib/agentContext";
import { usePreferredYear } from "@/hooks/usePreferredYear";
import { financialAnalysisQueryOptions, projectDataKey } from "@/lib/queryPolicy";

type Props = { project: ProjectSummary; preferredYear?: number | null };

export function AdjustmentPanel({ project, preferredYear }: Props) {
  const [year, setYear] = useState(project.years[0] ?? 0);
  const [selected, setSelected] = useState<AdjustmentSummaryRow | null>(null);
  const [addedId, setAddedId] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const { pinSelection } = useAgent();
  usePreferredYear(preferredYear, project.years, setYear);

  const summary = useQuery({
    queryKey: ["adj-summary", ...projectDataKey(project), year],
    queryFn: () => api.adjustmentSummary(project.project_id, year),
    enabled: year > 0,
    ...financialAnalysisQueryOptions,
  });

  const drilldown = useQuery({
    queryKey: ["adj-drill", project.project_id, year, selected?.凭证编号, selected?.过账日期],
    queryFn: () =>
      api.adjustmentDrilldown(project.project_id, year, selected!.凭证编号, selected!.过账日期),
    enabled: !!selected,
  });

  const addCandidate = useMutation({
    mutationFn: (voucherIds: string[]) =>
      api.addCandidate(project.project_id, {
        title: `${year}年 调账凭证 ${selected!.凭证编号}`,
        source_module: "调账冲销",
        source_view: "调账冲销摘要",
        selector: {
          kind: "adjustment_voucher",
          year,
          voucher_id: selected!.凭证编号,
          date: selected!.过账日期,
        },
        reason: `命中关键词「${selected!.命中关键词}」，纳入疑点库复核。`,
        tags: ["冲销调账", ...(selected!.命中关键词 ? selected!.命中关键词.split("、").slice(0, 3) : [])],
        voucher_ids: voucherIds,
      }),
    onSuccess: (res) => {
      setAddedId(res.group.group_id);
      queryClient.invalidateQueries({ queryKey: ["candidates", project.project_id] });
    },
  });

  useEffect(() => {
    if (!selected) {
      pinSelection(moduleOverviewSelection("adjustment", "调账冲销", year));
      return;
    }
    pinSelection({
      label: `${selected.过账日期} · 凭证 ${selected.凭证编号} · ${selected.命中关键词}`,
      source_module: "调账冲销",
      source_view: "调账冲销摘要",
      selector: {
        kind: "adjustment_voucher",
        year,
        voucher_id: selected.凭证编号,
        date: selected.过账日期,
      },
      summary: drilldown.data ? { rows: drilldown.data.row_count } : undefined,
    });
  }, [selected, year, drilldown.data?.row_count, pinSelection]);

  if (!project.years.length) {
    return (
      <EmptyState
        kind="upload"
        size="sm"
        title="尚未上传序时账"
        description="在左侧上传年度序时账后，即可查看该模块分析。"
      />
    );
  }

  const rows = summary.data?.rows ?? [];

  return (
    <div className="module-panel">
      <ModuleInsightCard projectId={project.project_id} moduleKey="调账冲销" />
      <div className="filters">
        <label>年度
          <select value={year} onChange={(e) => { setYear(Number(e.target.value)); setSelected(null); setAddedId(null); }}>
            {project.years.map((y) => <option key={y} value={y}>{y}</option>)}
          </select>
        </label>
      </div>
      <div className="chart-card">
        <h3>调账/冲销凭证摘要（Top {rows.length}）</h3>
        {summary.isLoading && <p className="muted">加载中…</p>}
        {!summary.isLoading && rows.length === 0 && <p className="muted">未命中调账关键词或反记账标识。</p>}
        {rows.length > 0 && (
          <div className="detail-table-wrap">
            <table className="detail-table selectable-table">
              <thead>
                <tr>
                  <th>过账日期</th>
                  <th>凭证编号</th>
                  <th>命中关键词</th>
                  <th>借方</th>
                  <th>贷方</th>
                  <th>最大行金额</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr
                    key={`${r.凭证编号}-${r.过账日期}`}
                    className={selected?.凭证编号 === r.凭证编号 && selected?.过账日期 === r.过账日期 ? "row-selected" : ""}
                    onClick={() => { setSelected(r); setAddedId(null); }}
                  >
                    <td>{r.过账日期}</td>
                    <td>{r.凭证编号}</td>
                    <td>{r.命中关键词}</td>
                    <td>{r.借方金额?.toLocaleString()}</td>
                    <td>{r.贷方金额?.toLocaleString()}</td>
                    <td>{r.最大行金额?.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {selected && (
        <DrilldownPanel
          label={`${selected.过账日期} · 凭证 ${selected.凭证编号} · ${selected.命中关键词}`}
          rows={drilldown.data?.rows ?? []}
          loading={drilldown.isLoading}
          onClear={() => { setSelected(null); setAddedId(null); }}
          onAdd={(voucherIds) => addCandidate.mutate(voucherIds)}
          adding={addCandidate.isPending}
          added={!!addedId}
        />
      )}
    </div>
  );
}
