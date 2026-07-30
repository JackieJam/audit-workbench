import { useCallback, useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type * as echarts from "echarts";
import { api, type ProjectSummary } from "@/api/client";
import { ChartLoadingBar } from "@/components/ChartLoadingBar";
import { DrilldownPanel } from "@/components/DrilldownPanel";
import { ModuleInsightCard } from "@/components/ModuleInsightCard";
import { EmptyState } from "@/components/EmptyState";
import { chartPalette } from "@/lib/chartTheme";
import { useAgent } from "@/context/AgentContext";
import { useEcharts } from "@/hooks/useEcharts";
import { usePreferredYear } from "@/hooks/usePreferredYear";
import { moduleOverviewSelection } from "@/lib/agentContext";
import { financialAnalysisQueryOptions, projectDataKey } from "@/lib/queryPolicy";

type Props = { project: ProjectSummary; preferredYear?: number | null };
type Metric = "investment_income" | "non_operating_income" | "non_operating_expense";
type Selection = { year: number; month: number; metric: Metric; label: string };

const METRICS: Metric[] = ["investment_income", "non_operating_income", "non_operating_expense"];
const LABELS: Record<Metric, string> = {
  investment_income: "投资收益",
  non_operating_income: "营业外收入",
  non_operating_expense: "营业外支出",
};

function formatWan(value: number) {
  return `${(value / 10000).toFixed(1)}万`;
}

function periodLabel(period: number) {
  return period === 13 ? "13期" : `${period}月`;
}

export function OtherPnlPanel({ project, preferredYear }: Props) {
  const [year, setYear] = useState(project.years[project.years.length - 1] ?? 0);
  const [selection, setSelection] = useState<Selection | null>(null);
  const chartRef = useRef<HTMLDivElement>(null);
  const queryClient = useQueryClient();
  const { pinSelection } = useAgent();
  usePreferredYear(preferredYear, project.years, setYear);

  const monthly = useQuery({
    queryKey: ["other-pnl-monthly", ...projectDataKey(project), year],
    queryFn: () => api.otherPnlMonthly(project.project_id, year),
    enabled: year > 0,
    ...financialAnalysisQueryOptions,
  });
  const drilldown = useQuery({
    queryKey: ["other-pnl-drilldown", project.project_id, selection],
    queryFn: () =>
      api.drilldown(project.project_id, {
        kind: "other_pnl_month",
        year: selection!.year,
        month: selection!.month,
        metric: selection!.metric,
      }),
    enabled: !!selection,
  });
  const addCandidate = useMutation({
    mutationFn: (voucherIds: string[]) =>
      api.addCandidate(project.project_id, {
        title: `${selection!.year}年${periodLabel(selection!.month)} ${selection!.label}`,
        source_module: "营业外与投资收益",
        source_view: "月度营业外与投资收益",
        selector: {
          kind: "other_pnl_month",
          year: selection!.year,
          month: selection!.month,
          metric: selection!.metric,
        },
        reason: `${selection!.label}月度发生额纳入疑点库复核。`,
        tags: [selection!.label, periodLabel(selection!.month)],
        voucher_ids: voucherIds,
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["candidates", project.project_id] }),
  });

  const rows = monthly.data?.rows ?? [];
  const buildOption = useCallback((): echarts.EChartsOption | null => {
    if (!rows.length) return null;
    const pal = chartPalette();
    return {
      backgroundColor: "transparent",
      tooltip: { trigger: "axis" },
      legend: { textStyle: { color: pal.muted } },
      grid: { left: 16, right: 24, top: 40, bottom: 32, containLabel: true },
      xAxis: { type: "category", data: rows.map((row) => periodLabel(row.月份)) },
      yAxis: {
        type: "value",
        axisLabel: { color: pal.muted, formatter: formatWan },
        splitLine: { lineStyle: { color: pal.grid } },
      },
      series: [
        { name: "投资收益", type: "bar", data: rows.map((row) => row.投资收益) },
        { name: "营业外收入", type: "bar", data: rows.map((row) => row.营业外收入) },
        { name: "营业外支出", type: "bar", data: rows.map((row) => row.营业外支出) },
        { name: "净影响", type: "line", smooth: true, data: rows.map((row) => row.净影响) },
      ],
    };
  }, [rows]);

  useEcharts(chartRef, buildOption, [rows, year], {
    enabled: monthly.isSuccess && rows.length > 0,
    onClick: (_chart, params) => {
      if (params.componentType !== "series" || params.dataIndex == null || params.seriesIndex == null) return;
      if (params.seriesIndex >= METRICS.length) return;
      const metric = METRICS[params.seriesIndex];
      setSelection({ year, month: rows[params.dataIndex].月份, metric, label: LABELS[metric] });
    },
  });

  useEffect(() => {
    if (!selection) {
      pinSelection(moduleOverviewSelection("other_pnl", "营业外与投资收益", year));
      return;
    }
    pinSelection({
      label: `${selection.year}年${periodLabel(selection.month)} · ${selection.label}`,
      source_module: "营业外与投资收益",
      source_view: "月度营业外与投资收益",
      selector: {
        kind: "other_pnl_month",
        year: selection.year,
        month: selection.month,
        metric: selection.metric,
      },
      summary: drilldown.data ? { rows: drilldown.data.row_count } : undefined,
    });
  }, [selection, drilldown.data?.row_count, pinSelection, year]);

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

  return (
    <div className="module-panel">
      <ModuleInsightCard projectId={project.project_id} moduleKey="营业外与投资收益" />
      <div className="filters">
        <label>
          年度
          <select value={year} onChange={(event) => { setYear(Number(event.target.value)); setSelection(null); }}>
            {project.years.map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
        </label>
      </div>
      <div className="chart-card">
        <h3>投资收益与营业外收支</h3>
        <p className="chart-hint muted">收入类按贷增借减、支出类按借增贷减；Period 13 单独展示。点击图形可回查分录。</p>
        <ChartLoadingBar loading={monthly.isLoading} label="营业外与投资收益加载中" />
        <div ref={chartRef} className="chart-box" />
      </div>
      {selection && (
        <DrilldownPanel
          label={`${selection.year}年${periodLabel(selection.month)} · ${selection.label}`}
          rows={drilldown.data?.rows ?? []}
          loading={drilldown.isLoading}
          onClear={() => setSelection(null)}
          onAdd={(voucherIds) => addCandidate.mutate(voucherIds)}
          adding={addCandidate.isPending}
          added={addCandidate.isSuccess}
        />
      )}
    </div>
  );
}
