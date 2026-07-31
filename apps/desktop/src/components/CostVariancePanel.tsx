import { useCallback, useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type * as echarts from "echarts";

import { api, type ProjectSummary } from "@/api/client";
import { ChartLoadingBar } from "@/components/ChartLoadingBar";
import { DrilldownPanel } from "@/components/DrilldownPanel";
import { EmptyState } from "@/components/EmptyState";
import { ModuleInsightCard } from "@/components/ModuleInsightCard";
import { useAgent } from "@/context/AgentContext";
import { useEcharts } from "@/hooks/useEcharts";
import { usePreferredYear } from "@/hooks/usePreferredYear";
import { chartPalette } from "@/lib/chartTheme";
import { moduleOverviewSelection } from "@/lib/agentContext";
import { financialAnalysisQueryOptions, projectDataKey } from "@/lib/queryPolicy";

type Props = { project: ProjectSummary; preferredYear?: number | null };
type Metric = "variance" | "cogs" | "inventory" | "manufacturing";
type Selection = { year: number; month: number; metric: Metric; label: string };

const METRICS: Metric[] = ["variance", "cogs", "inventory", "manufacturing"];
const LABELS: Record<Metric, string> = {
  variance: "差异科目净额",
  cogs: "结转营业成本",
  inventory: "结转存货",
  manufacturing: "制造归集",
};

function periodLabel(period: number) {
  return period === 13 ? "13期" : `${period}月`;
}

function formatWan(value: number) {
  return `${(value / 10_000).toFixed(1)}万`;
}

function formatAmount(value: number) {
  return value.toLocaleString("zh-CN", { maximumFractionDigits: 0 });
}

export function CostVariancePanel({ project, preferredYear }: Props) {
  const [year, setYear] = useState(project.years.at(-1) ?? 0);
  const [selection, setSelection] = useState<Selection | null>(null);
  const chartRef = useRef<HTMLDivElement>(null);
  const queryClient = useQueryClient();
  const { pinSelection } = useAgent();
  usePreferredYear(preferredYear, project.years, setYear);

  const monthly = useQuery({
    queryKey: ["cost-variance-monthly", ...projectDataKey(project), year],
    queryFn: () => api.costVarianceMonthly(project.project_id, year),
    enabled: year > 0,
    ...financialAnalysisQueryOptions,
  });
  const drilldown = useQuery({
    queryKey: ["cost-variance-drilldown", project.project_id, selection],
    queryFn: () => api.drilldown(project.project_id, {
      kind: "cost_variance_month",
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
        source_module: "成本差异",
        source_view: "标准成本差异结转",
        selector: {
          kind: "cost_variance_month",
          year: selection!.year,
          month: selection!.month,
          metric: selection!.metric,
        },
        reason: "成本差异本身属正常调整；本样本因金额、结转去向或周期波动需要进一步核实。",
        tags: ["成本差异", selection!.label, periodLabel(selection!.month)],
        voucher_ids: voucherIds,
      }),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["candidates", project.project_id] }),
  });

  const rows = monthly.data?.rows ?? [];
  const buildOption = useCallback((): echarts.EChartsOption | null => {
    if (!rows.length) return null;
    const pal = chartPalette();
    return {
      backgroundColor: "transparent",
      tooltip: { trigger: "axis" },
      legend: { textStyle: { color: pal.muted } },
      grid: { left: 16, right: 28, top: 54, bottom: 34, containLabel: true },
      xAxis: {
        type: "category",
        data: rows.map((row) => periodLabel(row.月份)),
        axisLabel: { color: pal.muted },
      },
      yAxis: [
        {
          type: "value",
          axisLabel: { color: pal.muted, formatter: formatWan },
          splitLine: { lineStyle: { color: pal.grid } },
        },
        {
          type: "value",
          axisLabel: { color: pal.muted, formatter: formatWan },
          splitLine: { show: false },
        },
      ],
      series: [
        { name: "差异科目净额", type: "bar", data: rows.map((row) => row.差异科目净额) },
        { name: "结转营业成本", type: "bar", data: rows.map((row) => row.结转营业成本) },
        { name: "结转存货", type: "bar", data: rows.map((row) => row.结转存货) },
        { name: "制造归集", type: "bar", data: rows.map((row) => row.制造归集) },
        {
          name: "差异绝对发生额",
          type: "line",
          yAxisIndex: 1,
          smooth: true,
          symbolSize: 6,
          data: rows.map((row) => row.差异绝对发生额),
        },
      ],
    };
  }, [rows]);

  useEcharts(chartRef, buildOption, [rows, year], {
    enabled: monthly.isSuccess && rows.length > 0,
    onClick: (_chart, params) => {
      if (
        params.componentType !== "series"
        || params.dataIndex == null
        || params.seriesIndex == null
        || params.seriesIndex >= METRICS.length
      ) return;
      const metric = METRICS[params.seriesIndex];
      setSelection({
        year,
        month: rows[params.dataIndex].月份,
        metric,
        label: LABELS[metric],
      });
    },
  });

  useEffect(() => {
    if (!selection) {
      pinSelection(moduleOverviewSelection("cost_variance", "成本差异", year));
      return;
    }
    pinSelection({
      label: `${selection.year}年${periodLabel(selection.month)} · ${selection.label}`,
      source_module: "成本差异",
      source_view: "标准成本差异结转",
      selector: {
        kind: "cost_variance_month",
        year: selection.year,
        month: selection.month,
        metric: selection.metric,
      },
      summary: drilldown.data ? { rows: drilldown.data.row_count } : undefined,
    });
  }, [selection, drilldown.data?.row_count, pinSelection, year]);

  if (!project.years.length) {
    return <EmptyState kind="upload" size="sm" title="尚未上传序时账" />;
  }

  const summary = monthly.data?.summary;
  return (
    <div className="module-panel">
      <ModuleInsightCard projectId={project.project_id} moduleKey="成本差异" />
      <div className="year-segmented" aria-label="成本差异年度">
        {project.years.map((item) => (
          <button
            key={item}
            type="button"
            className={year === item ? "active" : ""}
            onClick={() => { setYear(item); setSelection(null); }}
          >
            {item}
          </button>
        ))}
      </div>
      {summary ? (
        <>
          <p className="muted chart-hint">{summary.interpretation}</p>
          <div className="metric-grid">
            <div className="metric-card">
              <span>差异绝对发生额</span>
              <strong>{formatAmount(summary.variance_absolute_amount)}</strong>
            </div>
            <div className="metric-card">
              <span>对营业成本净影响 · 占成本发生额 {(summary.cogs_impact_ratio * 100).toFixed(1)}%</span>
              <strong>{formatAmount(summary.cogs_impact)}</strong>
            </div>
            <div className="metric-card">
              <span>年末（12月/13期）占全年差异</span>
              <strong>{(summary.year_end_amount_ratio * 100).toFixed(1)}%</strong>
            </div>
            <div className="metric-card">
              <span>异常波动月份</span>
              <strong>
                {summary.outlier_months.length
                  ? summary.outlier_months.map(periodLabel).join("、")
                  : "未识别"}
              </strong>
            </div>
          </div>
        </>
      ) : null}
      {summary && summary.variance_absolute_amount === 0 && !monthly.isLoading ? (
        <EmptyState
          kind="search"
          size="sm"
          title="本年度未识别到成本差异结转"
          description="系统仅识别明确的差异结转科目，不会把普通主营业务成本混入本模块。"
        />
      ) : (
        <div className="chart-card">
          <h3>成本差异月度结转与去向</h3>
          <p className="chart-hint muted">
            正常调整不自动列为疑点；重点观察对营业成本的直接影响、期末集中及异常月份。点击柱形回查分录。
          </p>
          <ChartLoadingBar loading={monthly.isLoading} label="成本差异分析加载中" />
          <div ref={chartRef} className="chart-box" />
        </div>
      )}
      {selection ? (
        <DrilldownPanel
          label={`${selection.year}年${periodLabel(selection.month)} · ${selection.label}`}
          rows={drilldown.data?.rows ?? []}
          loading={drilldown.isLoading}
          onClear={() => setSelection(null)}
          onAdd={(voucherIds) => addCandidate.mutate(voucherIds)}
          adding={addCandidate.isPending}
          added={addCandidate.isSuccess}
        />
      ) : null}
    </div>
  );
}
