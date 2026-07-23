import { useEffect, useRef, useState, useCallback } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as echarts from "echarts";
import { api, type ProjectSummary } from "@/api/client";
import { ChartLoadingBar } from "@/components/ChartLoadingBar";
import { DrilldownPanel } from "@/components/DrilldownPanel";
import { useAgent } from "@/context/AgentContext";
import { moduleOverviewSelection } from "@/lib/agentContext";
import { ModuleInsightCard } from "@/components/ModuleInsightCard";
import { useEcharts } from "@/hooks/useEcharts";
import { usePreferredYear } from "@/hooks/usePreferredYear";
import { financialAnalysisQueryOptions, projectDataKey } from "@/lib/queryPolicy";

type Props = {
  project: ProjectSummary;
  preferredYear?: number | null;
};

type MonthlySelection = {
  kind: "monthly";
  year: number;
  month: number;
  metric: "revenue" | "cost" | "gross";
  metricLabel: string;
  category: string;
};

type CustomerSelection = {
  kind: "customer";
  year: number;
  customer: string;
  category: string;
};

type ChartSelection = MonthlySelection | CustomerSelection;

const METRIC_BY_SERIES = ["revenue", "cost", "gross"] as const;
const METRIC_LABELS: Record<string, string> = {
  revenue: "净收入",
  cost: "净成本",
  gross: "毛利",
};

function formatWan(v: number) {
  return `${(v / 10000).toFixed(1)}万`;
}

function periodLabel(period: number) {
  return period === 13 ? "13期" : `${period}月`;
}

function selectionLabel(sel: ChartSelection): string {
  if (sel.kind === "monthly") {
    return `${sel.year}年${periodLabel(sel.month)} · ${sel.category} · ${sel.metricLabel}`;
  }
  return `${sel.year}年 · ${sel.category} · 客户「${sel.customer}」`;
}

function selectionToAddPayload(sel: ChartSelection) {
  if (sel.kind === "monthly") {
    return {
      title: `${sel.year}年${periodLabel(sel.month)} ${sel.category} ${sel.metricLabel}`,
      source_module: "收入成本",
      source_view: "月度收入成本",
      selector: {
        kind: "monthly_income_cost",
        year: sel.year,
        month: sel.month,
        metric: sel.metric,
        category: sel.category,
      },
      reason: `从月度收入成本图选择 ${sel.year}年${periodLabel(sel.month)} ${sel.metricLabel}，纳入疑点库复核。`,
      tags: ["月度", sel.metricLabel],
    };
  }
  return {
    title: `${sel.year}年 客户「${sel.customer}」收入`,
    source_module: "收入成本",
    source_view: "客户收入",
    selector: {
      kind: "customer_revenue",
      year: sel.year,
      customer: sel.customer,
      category: sel.category,
    },
    reason: `客户「${sel.customer}」收入集中，纳入疑点库复核。`,
    tags: ["客户"],
  };
}

export function IncomeCostPanel({ project, preferredYear }: Props) {
  const [year, setYear] = useState(project.years[project.years.length - 1] ?? 0);
  const [category, setCategory] = useState("总计");
  const [selection, setSelection] = useState<ChartSelection | null>(null);
  const [addedId, setAddedId] = useState<string | null>(null);
  const monthlyRef = useRef<HTMLDivElement>(null);
  const customerRef = useRef<HTMLDivElement>(null);

  const queryClient = useQueryClient();
  const { pinSelection } = useAgent();
  usePreferredYear(preferredYear, project.years, setYear);

  useEffect(() => {
    if (project.years.length && !project.years.includes(year)) {
      setYear(project.years[0]);
    }
  }, [project.years, year]);

  useEffect(() => {
    setSelection(null);
    setAddedId(null);
  }, [year, category]);


  const categories = useQuery({
    queryKey: ["ic-cats", ...projectDataKey(project), year],
    queryFn: () => api.incomeCostCategories(project.project_id, year),
    enabled: year > 0,
    ...financialAnalysisQueryOptions,
  });

  const monthly = useQuery({
    queryKey: ["ic-monthly", ...projectDataKey(project), year, category],
    queryFn: () => api.incomeCostMonthly(project.project_id, year, category),
    enabled: year > 0,
    ...financialAnalysisQueryOptions,
  });

  const customers = useQuery({
    queryKey: ["ic-customers", ...projectDataKey(project), year, category],
    queryFn: () => api.incomeCostCustomers(project.project_id, year, category),
    enabled: year > 0,
    ...financialAnalysisQueryOptions,
  });

  const drilldown = useQuery({
    queryKey: ["ic-drilldown", project.project_id, selection],
    queryFn: () => {
      if (!selection) throw new Error("no selection");
      if (selection.kind === "monthly") {
        return api.incomeCostDrilldownMonthly(
          project.project_id,
          selection.year,
          selection.month,
          selection.metric,
          selection.category,
        );
      }
      return api.incomeCostDrilldownCustomer(
        project.project_id,
        selection.year,
        selection.customer,
        selection.category,
      );
    },
    enabled: !!selection,
  });

  const addCandidate = useMutation({
    mutationFn: (voucherIds: string[]) => {
      if (!selection) throw new Error("no selection");
      return api.addCandidate(project.project_id, { ...selectionToAddPayload(selection), voucher_ids: voucherIds });
    },
    onSuccess: (data) => {
      setAddedId(data.group.group_id);
      queryClient.invalidateQueries({ queryKey: ["candidates", project.project_id] });
    },
  });

  useEffect(() => {
    if (!selection) {
      pinSelection(moduleOverviewSelection("income", "收入成本", year));
      return;
    }
    const payload = selectionToAddPayload(selection);
    pinSelection({
      label: selectionLabel(selection),
      source_module: payload.source_module,
      source_view: payload.source_view,
      selector: payload.selector,
      summary: drilldown.data ? { rows: drilldown.data.row_count } : undefined,
    });
  }, [selection, drilldown.data?.row_count, pinSelection, year]);

  const monthlyRows = monthly.data?.rows ?? [];
  const monthlyHasValues = monthlyRows.some(
    (r) => Math.abs(r.净收入) > 0 || Math.abs(r.净成本 ?? r.净成本影响) > 0 || Math.abs(r.毛利) > 0,
  );

  const buildMonthlyOption = useCallback((): echarts.EChartsOption | null => {
    if (!monthlyRows.length) return null;
    const months = monthlyRows.map((r) => periodLabel(r.月份));
    // 图上「净成本」用正数发生额：优先后端净成本，否则取 -净成本影响
    const costBars = monthlyRows.map((r) =>
      r.净成本 != null ? r.净成本 : -r.净成本影响,
    );
    return {
      backgroundColor: "transparent",
      tooltip: { trigger: "axis" },
      legend: { textStyle: { color: "#8b97a8" } },
      grid: { left: 16, right: 24, top: 40, bottom: 32, containLabel: true },
      xAxis: { type: "category", data: months, axisLabel: { color: "#8b97a8" } },
      yAxis: {
        type: "value",
        axisLabel: { color: "#8b97a8", formatter: (v: number) => formatWan(v) },
        splitLine: { lineStyle: { color: "#2a3344" } },
      },
      series: [
        { name: "净收入", type: "bar", data: monthlyRows.map((r) => r.净收入) },
        { name: "净成本", type: "bar", data: costBars },
        { name: "毛利", type: "line", smooth: true, data: monthlyRows.map((r) => r.毛利) },
      ],
    };
  }, [monthlyRows]);

  useEcharts(
    monthlyRef,
    buildMonthlyOption,
    [monthlyRows, year, category],
    {
      enabled: monthly.isSuccess && monthlyRows.length > 0,
      onClick: (_chart, params) => {
        if (params.componentType !== "series" || params.dataIndex == null || params.seriesIndex == null) return;
        const month = monthlyRows[params.dataIndex as number].月份;
        const metric = METRIC_BY_SERIES[params.seriesIndex as number] ?? "revenue";
        setSelection({
          kind: "monthly",
          year,
          month,
          metric,
          metricLabel: METRIC_LABELS[metric],
          category,
        });
        setAddedId(null);
      },
    },
  );

  const customerRows = customers.data?.rows ?? [];

  const buildCustomerOption = useCallback((): echarts.EChartsOption | null => {
    if (!customerRows.length) return null;
    const names = customerRows.map((r) => r.客户).reverse();
    const values = customerRows.map((r) => r.净收入).reverse();
    const maxAbs = Math.max(...values.map((v) => Math.abs(v)), 1);
    const shortThreshold = 0.32;

    return {
      backgroundColor: "transparent",
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        formatter: (items: unknown) => {
          const list = Array.isArray(items) ? items : [items];
          const first = list[0] as { dataIndex?: number; value?: number };
          const idx = first?.dataIndex ?? 0;
          const name = names[idx] ?? "";
          const value = Number(first?.value ?? 0);
          return `${name}<br/>净收入 ${formatWan(value)}`;
        },
      },
      grid: { left: 12, right: 96, top: 16, bottom: 32, containLabel: true },
      xAxis: {
        type: "value",
        axisLabel: { color: "#8b97a8", formatter: (v: number) => formatWan(v) },
        splitLine: { lineStyle: { color: "#2a3344" } },
      },
      yAxis: {
        type: "category",
        data: names,
        axisLabel: { show: false },
        axisTick: { show: false },
        axisLine: { show: false },
      },
      series: [
        {
          type: "bar",
          barMaxWidth: 28,
          data: values.map((value) => {
            const short = Math.abs(value) / maxAbs < shortThreshold;
            return {
              value,
              label: {
                show: true,
                position: short ? "right" : "insideLeft",
                color: short ? "#c9d4e4" : "#f5f8fc",
                fontSize: 11,
                distance: short ? 6 : 8,
                formatter: (params) => {
                  const name = names[params.dataIndex ?? 0] ?? "";
                  const shown = name.length > 32 ? `${name.slice(0, 31)}…` : name;
                  return `${shown}  ${formatWan(Number(params.value ?? 0))}`;
                },
              },
            };
          }),
          itemStyle: { color: "#4f8ef7", borderRadius: [0, 4, 4, 0] },
        },
      ],
    };
  }, [customerRows]);

  useEcharts(
    customerRef,
    buildCustomerOption,
    [customerRows, year, category],
    {
      enabled: customers.isSuccess && customerRows.length > 0,
      onClick: (_chart, params) => {
        if (params.componentType !== "series" || params.dataIndex == null) return;
        const names = customerRows.map((r) => r.客户).reverse();
        const customer = names[params.dataIndex as number];
        setSelection({ kind: "customer", year, customer, category });
        setAddedId(null);
      },
    },
  );

  if (!project.years.length) {
    return <p className="muted">请先在左侧上传序时账。</p>;
  }


  return (
    <div className="income-cost-panel">
      <ModuleInsightCard projectId={project.project_id} moduleKey="收入成本" />

      <div className="filters">
        <label>
          年度
          <select value={year} onChange={(e) => setYear(Number(e.target.value))}>
            {project.years.map((y) => (
              <option key={y} value={y}>
                {y}
              </option>
            ))}
          </select>
        </label>
        <label>
          分类
          <select value={category} onChange={(e) => setCategory(e.target.value)}>
            {(categories.data?.categories ?? ["总计"]).map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="chart-card">
        <h3>月度收入成本</h3>
        <p className="chart-hint muted">
          净收入=主营/其他业务收入；净成本=主营/其他业务成本（6401/6402，不含生产成本→存货）；毛利=净收入−净成本。点击柱/点可回查分录。
        </p>
        <ChartLoadingBar
          loading={monthly.isLoading}
          label="月度收入成本加载中"
        />
        {monthly.isError && <p className="error">{String(monthly.error)}</p>}
        {monthly.isSuccess && !monthlyHasValues && (
          <p className="muted">该年度暂无收入成本数据，请切换其他年度或检查科目分类。</p>
        )}
        <div ref={monthlyRef} className="chart-box" />
      </div>

      <div className="chart-card">
        <h3>客户收入 Top10</h3>
        <ChartLoadingBar
          loading={customers.isLoading}
          label="客户收入加载中"
        />
        {!customers.isLoading && !(customers.data?.rows.length) ? (
          <p className="muted">当前数据无客户维度或未识别客户列。</p>
        ) : (
          <>
            <p className="chart-hint muted">客户名标在柱上（短柱在右侧）；点击条形可回查分录。</p>
            <div ref={customerRef} className="chart-box tall" />
          </>
        )}
      </div>

      {selection && (
        <>
          <DrilldownPanel
            label={selectionLabel(selection)}
            rows={drilldown.data?.rows ?? []}
            loading={drilldown.isLoading}
            onClear={() => {
              setSelection(null);
              setAddedId(null);
            }}
            onAdd={(voucherIds) => addCandidate.mutate(voucherIds)}
            adding={addCandidate.isPending}
            added={!!addedId}
          />
          {addCandidate.isError && (
            <p className="error">{String(addCandidate.error)}</p>
          )}
        </>
      )}
    </div>
  );
}
