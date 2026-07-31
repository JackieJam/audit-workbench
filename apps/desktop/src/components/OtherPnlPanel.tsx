import { useCallback, useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type * as echarts from "echarts";
import { api, type OtherPnlMonthlyRow, type ProjectSummary } from "@/api/client";
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
type Metric =
  | "investment_income"
  | "fair_value_change"
  | "other_income"
  | "asset_disposal"
  | "non_operating_income"
  | "non_operating_expense"
  | "credit_impairment"
  | "asset_impairment"
  | "income_tax";
type View = "returns" | "non_operating" | "impairment_tax";
type Selection = { year: number; month: number; metric: Metric; label: string };

const LABELS: Record<Metric, string> = {
  investment_income: "投资收益",
  fair_value_change: "公允价值变动损益",
  other_income: "其他收益",
  asset_disposal: "资产处置收益",
  non_operating_income: "营业外收入",
  non_operating_expense: "营业外支出",
  credit_impairment: "信用减值损失",
  asset_impairment: "资产减值损失",
  income_tax: "所得税费用",
};
const VIEW_METRICS: Record<View, Metric[]> = {
  returns: ["investment_income", "fair_value_change", "other_income", "asset_disposal"],
  non_operating: ["non_operating_income", "non_operating_expense"],
  impairment_tax: ["credit_impairment", "asset_impairment", "income_tax"],
};

function metricValue(row: OtherPnlMonthlyRow, metric: Metric) {
  if (metric === "investment_income") return row.投资收益;
  if (metric === "fair_value_change") return row.公允价值变动损益;
  if (metric === "other_income") return row.其他收益;
  if (metric === "asset_disposal") return row.资产处置收益;
  if (metric === "non_operating_income") return row.营业外收入;
  if (metric === "non_operating_expense") return row.营业外支出;
  if (metric === "credit_impairment") return row.信用减值损失;
  if (metric === "asset_impairment") return row.资产减值损失;
  return row.所得税费用;
}

function viewNetImpact(row: OtherPnlMonthlyRow, view: View) {
  const metrics = VIEW_METRICS[view];
  const expenseMetrics = new Set<Metric>([
    "non_operating_expense",
    "credit_impairment",
    "asset_impairment",
    "income_tax",
  ]);
  return metrics.reduce(
    (sum, metric) =>
      sum + metricValue(row, metric) * (expenseMetrics.has(metric) ? -1 : 1),
    0,
  );
}

function formatWan(value: number) {
  return `${(value / 10000).toFixed(1)}万`;
}

function periodLabel(period: number) {
  return period === 13 ? "13期" : `${period}月`;
}

export function OtherPnlPanel({ project, preferredYear }: Props) {
  const [year, setYear] = useState(project.years[project.years.length - 1] ?? 0);
  const [view, setView] = useState<View>("returns");
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
        source_module: "其他损益",
        source_view: "月度其他损益",
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
    const metrics = VIEW_METRICS[view];
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
        ...metrics.map((metric) => ({
          name: LABELS[metric],
          type: "bar" as const,
          data: rows.map((row) => metricValue(row, metric)),
        })),
        {
          name: "当前组净影响",
          type: "line",
          smooth: true,
          data: rows.map((row) => viewNetImpact(row, view)),
        },
      ],
    };
  }, [rows, view]);

  useEcharts(chartRef, buildOption, [rows, year, view], {
    enabled: monthly.isSuccess && rows.length > 0,
    onClick: (_chart, params) => {
      if (params.componentType !== "series" || params.dataIndex == null || params.seriesIndex == null) return;
      const metrics = VIEW_METRICS[view];
      if (params.seriesIndex >= metrics.length) return;
      const metric = metrics[params.seriesIndex];
      setSelection({ year, month: rows[params.dataIndex].月份, metric, label: LABELS[metric] });
    },
  });

  useEffect(() => {
    if (!selection) {
      pinSelection(moduleOverviewSelection("other_pnl", "其他损益", year));
      return;
    }
    pinSelection({
      label: `${selection.year}年${periodLabel(selection.month)} · ${selection.label}`,
      source_module: "其他损益",
      source_view: "月度其他损益",
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
      <ModuleInsightCard
        projectId={project.project_id}
        moduleKey="营业外与投资收益"
        displayLabel="其他损益"
      />
      <div className="filters">
        <label>
          年度
          <select value={year} onChange={(event) => { setYear(Number(event.target.value)); setSelection(null); }}>
            {project.years.map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
        </label>
      </div>
      <div className="year-segmented" aria-label="其他损益分类">
        <button type="button" className={view === "returns" ? "active" : ""} onClick={() => { setView("returns"); setSelection(null); }}>
          投资及其他收益
        </button>
        <button type="button" className={view === "non_operating" ? "active" : ""} onClick={() => { setView("non_operating"); setSelection(null); }}>
          营业外收支
        </button>
        <button type="button" className={view === "impairment_tax" ? "active" : ""} onClick={() => { setView("impairment_tax"); setSelection(null); }}>
          减值与所得税
        </button>
      </div>
      <div className="chart-card">
        <h3>其他损益月度分析</h3>
        <p className="chart-hint muted">收益类按贷增借减、损失及所得税按借增贷减；Period 13 单独展示。点击图形可回查分录。</p>
        <ChartLoadingBar loading={monthly.isLoading} label="其他损益加载中" />
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
