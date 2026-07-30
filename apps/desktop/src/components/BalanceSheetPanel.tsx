import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as echarts from "echarts";
import { api, type ProjectSummary } from "@/api/client";
import { DrilldownPanel } from "@/components/DrilldownPanel";
import { ModuleInsightCard } from "@/components/ModuleInsightCard";
import { EmptyState } from "@/components/EmptyState";
import { chartPalette, useThemeVersion, withChartTheme } from "@/lib/chartTheme";
import { useAgent } from "@/context/AgentContext";
import { moduleOverviewSelection } from "@/lib/agentContext";
import { usePreferredYear } from "@/hooks/usePreferredYear";
import { financialAnalysisQueryOptions, projectDataKey } from "@/lib/queryPolicy";

type Props = { project: ProjectSummary; preferredYear?: number | null };

type Selection = {
  kind: string;
  year: number;
  category: string;
  month?: number;
  direction?: string;
  account?: string;
  label: string;
  sourceView: string;
};

function formatWan(v: number) {
  return `${(v / 10000).toFixed(1)}万`;
}

function periodLabel(period: number) {
  return period === 13 ? "13期" : `${period}月`;
}

export function BalanceSheetPanel({ project, preferredYear }: Props) {
  const [year, setYear] = useState(project.years[0] ?? 0);
  const [category, setCategory] = useState("");
  const [selection, setSelection] = useState<Selection | null>(null);
  const [addedId, setAddedId] = useState<string | null>(null);
  const monthlyRef = useRef<HTMLDivElement>(null);
  const accountRef = useRef<HTMLDivElement>(null);
  const queryClient = useQueryClient();
  const { pinSelection } = useAgent();
  usePreferredYear(preferredYear, project.years, setYear);

  const cats = useQuery({
    queryKey: ["bs-cats", ...projectDataKey(project), year],
    queryFn: () => api.bsCategories(project.project_id, year),
    enabled: year > 0,
    ...financialAnalysisQueryOptions,
  });

  useEffect(() => {
    const first = cats.data?.categories[0];
    if (first && !category) setCategory(first);
    if (first && category && !cats.data?.categories.includes(category)) setCategory(first);
  }, [cats.data, category]);

  const monthly = useQuery({
    queryKey: ["bs-monthly", ...projectDataKey(project), year, category],
    queryFn: () => api.bsMonthly(project.project_id, year, category),
    enabled: year > 0 && !!category,
    ...financialAnalysisQueryOptions,
  });

  const accounts = useQuery({
    queryKey: ["bs-accounts", ...projectDataKey(project), year, category],
    queryFn: () => api.bsAccounts(project.project_id, year, category),
    enabled: year > 0 && !!category,
    ...financialAnalysisQueryOptions,
  });

  const drilldown = useQuery({
    queryKey: ["bs-drill", project.project_id, selection],
    queryFn: () => api.drilldown(project.project_id, {
      kind: selection!.kind,
      year: selection!.year,
      category: selection!.category,
      ...(selection!.month != null ? { month: selection!.month } : {}),
      ...(selection!.direction ? { direction: selection!.direction } : {}),
      ...(selection!.account ? { account: selection!.account } : {}),
    }),
    enabled: !!selection,
  });

  const addCandidate = useMutation({
    mutationFn: (voucherIds: string[]) =>
      api.addCandidate(project.project_id, {
        title: selection!.label,
        source_module: "资产负债",
        source_view: selection!.sourceView,
        selector: {
          kind: selection!.kind,
          year: selection!.year,
          category: selection!.category,
          ...(selection!.month != null ? { month: selection!.month } : {}),
          ...(selection!.direction ? { direction: selection!.direction } : {}),
          ...(selection!.account ? { account: selection!.account } : {}),
        },
        reason: `${selection!.label}，纳入疑点库复核。`,
        tags: ["科目变动", selection!.category],
        voucher_ids: voucherIds,
      }),
    onSuccess: (res) => {
      setAddedId(res.group.group_id);
      queryClient.invalidateQueries({ queryKey: ["candidates", project.project_id] });
    },
  });

  useEffect(() => {
    if (!selection) {
      pinSelection(moduleOverviewSelection("balance_sheet", "资产负债", year));
      return;
    }
    pinSelection({
      label: selection.label,
      source_module: "资产负债",
      source_view: selection.sourceView,
      selector: {
        kind: selection.kind,
        year: selection.year,
        category: selection.category,
        ...(selection.month != null ? { month: selection.month } : {}),
        ...(selection.direction ? { direction: selection.direction } : {}),
        ...(selection.account ? { account: selection.account } : {}),
      },
      summary: drilldown.data ? { rows: drilldown.data.row_count } : undefined,
    });
  }, [selection, drilldown.data?.row_count, pinSelection, year]);

  const themeVersion = useThemeVersion();

  useEffect(() => {
    if (!monthlyRef.current || !monthly.data?.rows.length) return;
    const pal = chartPalette();
    const chart = echarts.init(monthlyRef.current);
    const rows = monthly.data.rows;
    const months = rows.map((r) => periodLabel(r.月份));
    chart.setOption(
      withChartTheme({
        tooltip: { trigger: "axis" },
        legend: { textStyle: { color: pal.muted } },
        grid: { left: 16, right: 24, top: 40, bottom: 32, containLabel: true },
        xAxis: { type: "category", data: months, axisLabel: { color: pal.muted } },
        yAxis: {
          type: "value",
          axisLabel: { color: pal.muted, formatter: formatWan },
          splitLine: { lineStyle: { color: pal.grid } },
        },
        series: [
          { name: "借方发生额", type: "bar", data: rows.map((r) => r.借方发生额) },
          { name: "贷方发生额", type: "bar", data: rows.map((r) => r.贷方发生额) },
          { name: "净变动", type: "line", smooth: true, data: rows.map((r) => r.净变动) },
        ],
      }),
    );
    const dirs = ["debit", "credit", "net"] as const;
    const onClick = (params: { componentType?: string; dataIndex?: number; seriesIndex?: number }) => {
      if (params.componentType !== "series" || params.dataIndex == null || params.seriesIndex == null) return;
      const month = rows[params.dataIndex].月份;
      const direction = dirs[params.seriesIndex] ?? "net";
      setSelection({
        kind: "bs_category_month", year, category, month, direction,
        label: `${year}年${periodLabel(month)} ${category} ${direction === "debit" ? "借方" : direction === "credit" ? "贷方" : "净变动"}`,
        sourceView: "科目类别月度变动",
      });
      setAddedId(null);
    };
    chart.on("click", onClick);
    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => { chart.off("click", onClick); window.removeEventListener("resize", onResize); chart.dispose(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [monthly.data, year, category, themeVersion]);

  useEffect(() => {
    if (!accountRef.current || !accounts.data?.rows.length) return;
    const pal = chartPalette();
    const chart = echarts.init(accountRef.current);
    const rows = accounts.data.rows;
    const labels = rows.map((r) => `${r.科目编号} ${r.科目名称}`).reverse();
    chart.setOption(
      withChartTheme({
        tooltip: { trigger: "axis" },
        grid: { left: 160, right: 24 },
        xAxis: {
          type: "value",
          axisLabel: { color: pal.muted, formatter: formatWan },
          splitLine: { lineStyle: { color: pal.grid } },
        },
        yAxis: {
          type: "category",
          data: labels,
          axisLabel: { color: pal.muted, width: 150, overflow: "truncate" },
        },
        series: [
          {
            type: "bar",
            data: rows.map((r) => r.净变动).reverse(),
            itemStyle: { color: pal.accent, borderRadius: [0, 4, 4, 0] },
          },
        ],
      }),
    );
    const onClick = (params: { componentType?: string; dataIndex?: number }) => {
      if (params.componentType !== "series" || params.dataIndex == null) return;
      const row = rows[rows.length - 1 - params.dataIndex];
      setSelection({
        kind: "bs_category_account", year, category, account: row.科目编号,
        label: `${year}年 ${category} · ${row.科目编号} ${row.科目名称}`,
        sourceView: "科目构成",
      });
      setAddedId(null);
    };
    chart.on("click", onClick);
    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => { chart.off("click", onClick); window.removeEventListener("resize", onResize); chart.dispose(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accounts.data, year, category, themeVersion]);

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
  if (!cats.data?.categories.length) {
    return (
      <EmptyState
        kind="search"
        size="sm"
        title="未识别到资产负债类科目"
        description="当前数据中没有可归入资产负债分析的科目，请检查科目映射。"
      />
    );
  }

  return (
    <div className="module-panel">
      <ModuleInsightCard projectId={project.project_id} moduleKey="资产负债" />
      <p className="muted chart-hint">
        序时账为流量数据：展示借贷发生额与净变动，非余额表。默认优先往来类；货币资金流水大属常态，仅作结构浏览。
      </p>
      <div className="filters">
        <label>年度
          <select value={year} onChange={(e) => { setYear(Number(e.target.value)); setSelection(null); }}>
            {project.years.map((y) => <option key={y} value={y}>{y}</option>)}
          </select>
        </label>
        <label>科目类别
          <select value={category} onChange={(e) => { setCategory(e.target.value); setSelection(null); }}>
            {(cats.data?.categories ?? []).map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </label>
      </div>
      <div className="chart-card">
        <h3>月度发生额与净变动</h3>
        <div ref={monthlyRef} className="chart-box" />
      </div>
      <div className="chart-card">
        <h3>科目构成 Top15</h3>
        <p className="muted chart-hint">
          {category === "货币资金"
            ? "货币资金下多为银行子户流水，属预期结构，不宜当作风险集中度。"
            : "按净变动绝对值看该类变动由哪些明细科目驱动，点击可下钻。"}
        </p>
        <div ref={accountRef} className="chart-box tall" />
      </div>
      {selection && (
        <DrilldownPanel
          label={selection.label}
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
