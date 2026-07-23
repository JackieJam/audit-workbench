import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as echarts from "echarts";
import { api, type ProjectSummary } from "@/api/client";
import { ChartLoadingBar } from "@/components/ChartLoadingBar";
import { DrilldownPanel } from "@/components/DrilldownPanel";
import { ModuleInsightCard } from "@/components/ModuleInsightCard";
import { useAgent } from "@/context/AgentContext";
import { moduleOverviewSelection } from "@/lib/agentContext";
import { financialAnalysisQueryOptions, projectDataKey } from "@/lib/queryPolicy";

type Props = { project: ProjectSummary };

type Selection = { year: number; category: string };

function formatWan(v: number) {
  return `${(v / 10000).toFixed(1)}万`;
}

export function ExpensePanel({ project }: Props) {
  const chartRef = useRef<HTMLDivElement>(null);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [addedId, setAddedId] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const { pinSelection } = useAgent();

  const data = useQuery({
    queryKey: ["expense-cross", ...projectDataKey(project)],
    queryFn: () => api.expenseCrossYear(project.project_id),
    enabled: project.years.length > 0,
    ...financialAnalysisQueryOptions,
  });

  const drilldown = useQuery({
    queryKey: ["expense-drill", project.project_id, selection],
    queryFn: () => api.expenseDrilldown(project!.project_id, selection!.year, selection!.category),
    enabled: !!selection,
  });

  const addCandidate = useMutation({
    mutationFn: (voucherIds: string[]) =>
      api.addCandidate(project.project_id, {
        title: `${selection!.year}年 费用「${selection!.category}」`,
        source_module: "费用",
        source_view: "跨年费用结构",
        selector: { kind: "expense_category", year: selection!.year, expense_category: selection!.category },
        reason: `从跨年费用图选择 ${selection!.year}年 ${selection!.category}，纳入疑点库复核。`,
        tags: ["费用波动", selection!.category],
        voucher_ids: voucherIds,
      }),
    onSuccess: (res) => {
      setAddedId(res.group.group_id);
      queryClient.invalidateQueries({ queryKey: ["candidates", project.project_id] });
    },
  });

  useEffect(() => {
    if (!chartRef.current || !data.data?.rows.length) return;
    const rows = data.data.rows;
    const years = [...new Set(rows.map((r) => r.年份))].sort();
    const categories = [...new Set(rows.map((r) => r.费用类别))];
    const manyCategories = categories.length > 8;
    const chart = echarts.init(chartRef.current);
    chart.setOption({
      backgroundColor: "transparent",
      tooltip: { trigger: "axis" },
      legend: { textStyle: { color: "#8b97a8" }, type: "scroll" },
      grid: { left: 56, right: 24, top: 48, bottom: manyCategories ? 96 : 40, containLabel: true },
      xAxis: {
        type: "category",
        data: categories,
        axisLabel: {
          color: "#8b97a8",
          rotate: manyCategories ? 38 : 0,
          interval: 0,
          fontSize: 11,
          margin: 12,
          overflow: "break",
          width: 72,
        },
        axisTick: { alignWithLabel: true },
      },
      yAxis: { type: "value", axisLabel: { color: "#8b97a8", formatter: formatWan } },
      dataZoom: manyCategories
        ? [
            {
              type: "slider",
              xAxisIndex: 0,
              start: 0,
              end: Math.min(100, Math.round((10 / categories.length) * 100)),
              height: 20,
              bottom: 8,
              borderColor: "transparent",
              fillerColor: "rgba(59,130,246,0.12)",
              handleStyle: { color: "#3b82f6" },
              textStyle: { color: "#8b97a8", fontSize: 10 },
            },
          ]
        : undefined,
      series: years.map((y) => ({
        name: String(y),
        type: "bar",
        data: categories.map((c) => rows.find((r) => r.年份 === y && r.费用类别 === c)?.金额 ?? 0),
      })),
    });
    const onClick = (params: { componentType?: string; seriesName?: string; name?: string }) => {
      if (params.componentType !== "series" || !params.seriesName || !params.name) return;
      setSelection({ year: Number(params.seriesName), category: params.name });
      setAddedId(null);
    };
    chart.on("click", onClick);
    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => {
      chart.off("click", onClick);
      window.removeEventListener("resize", onResize);
      chart.dispose();
    };
  }, [data.data]);

  useEffect(() => {
    if (!selection) {
      pinSelection(moduleOverviewSelection("expense", "费用"));
      return;
    }
    pinSelection({
      label: `${selection.year}年 · ${selection.category}`,
      source_module: "费用",
      source_view: "跨年费用结构",
      selector: { kind: "expense_category", year: selection.year, expense_category: selection.category },
      summary: drilldown.data ? { rows: drilldown.data.row_count } : undefined,
    });
  }, [selection, drilldown.data?.row_count, pinSelection]);

  if (!project.years.length) return <p className="muted">请先在左侧上传序时账。</p>;

  return (
    <div className="module-panel">
      <ModuleInsightCard projectId={project.project_id} moduleKey="费用" />
      <div className="chart-card">
        <h3>跨年费用结构对比</h3>
        <p className="chart-hint muted">点击柱形回查分录，勾选行后「加入疑点库」。</p>
        <ChartLoadingBar
          loading={data.isLoading}
          label="费用结构加载中"
          hint="首次汇总大账套可能需要数十秒"
        />
        {data.isError ? (
          <div className="chart-state chart-state--error" role="alert">
            <span>费用结构读取失败：{String(data.error)}</span>
            <button type="button" className="btn-ghost" onClick={() => data.refetch()}>
              重试
            </button>
          </div>
        ) : data.isSuccess && data.data.rows.length === 0 ? (
          <div className="chart-state">
            未识别到费用类分录。请检查科目编码、科目名称及费用分类映射。
          </div>
        ) : (
          <div ref={chartRef} className="chart-box chart-box--category-labels" />
        )}
      </div>
      {selection && (
        <DrilldownPanel
          label={`${selection.year}年 · ${selection.category}`}
          rows={drilldown.data?.rows ?? []}
          loading={drilldown.isLoading}
          onClear={() => { setSelection(null); setAddedId(null); }}
          onAdd={(voucherIds) => addCandidate.mutate(voucherIds)}
          adding={addCandidate.isPending}
          added={!!addedId}
        />
      )}
    </div>
  );
}
