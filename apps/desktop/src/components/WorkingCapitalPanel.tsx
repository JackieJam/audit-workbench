import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as echarts from "echarts";
import { api, type ProjectSummary } from "@/api/client";
import { DrilldownPanel } from "@/components/DrilldownPanel";
import { ModuleInsightCard } from "@/components/ModuleInsightCard";
import { useAgent } from "@/context/AgentContext";
import { usePreferredYear } from "@/hooks/usePreferredYear";

type Props = { project: ProjectSummary; preferredYear?: number | null };
type WcTab = "ap" | "or" | "op";

type Selection = {
  kind: string;
  year: number;
  month: number;
  direction: string;
  supplier?: string;
  label: string;
  sourceView: string;
  tags: string[];
};

const AP_DIRS = ["credit", "debit", "net"] as const;
const AP_LABELS: Record<string, string> = { credit: "暂估贷方增加", debit: "暂估借方减少", net: "暂估净额" };
const OR_DIRS = ["debit", "credit", "net"] as const;
const OR_LABELS: Record<string, string> = { debit: "其他应收S", credit: "其他应收H", net: "其他应收净额" };
const OP_DIRS = ["accrual", "writeoff", "net"] as const;
const OP_LABELS: Record<string, string> = { accrual: "其他应付预提H", writeoff: "其他应付核销S", net: "其他应付净值" };

function formatWan(v: number) {
  return `${(v / 10000).toFixed(1)}万`;
}

function periodLabel(period: number) {
  return period === 13 ? "13期" : `${period}月`;
}

export function WorkingCapitalPanel({ project, preferredYear }: Props) {
  const [year, setYear] = useState(project.years[0] ?? 0);
  const [tab, setTab] = useState<WcTab>("ap");
  const [selection, setSelection] = useState<Selection | null>(null);
  const [addedId, setAddedId] = useState<string | null>(null);
  const monthlyRef = useRef<HTMLDivElement>(null);
  const supplierRef = useRef<HTMLDivElement>(null);
  const queryClient = useQueryClient();
  const { pinSelection } = useAgent();
  usePreferredYear(preferredYear, project.years, setYear);

  useEffect(() => {
    if (project.years.length && !project.years.includes(year)) setYear(project.years[0]);
  }, [project.years, year]);


  const apMonthly = useQuery({
    queryKey: ["wc-ap", project.project_id, year],
    queryFn: () => api.wcApMonthly(project.project_id, year),
    enabled: year > 0 && tab === "ap",
  });

  const orMonthly = useQuery({
    queryKey: ["wc-or", project.project_id, year],
    queryFn: () => api.wcOrMonthly(project.project_id, year),
    enabled: year > 0 && tab === "or",
  });

  const opMonthly = useQuery({
    queryKey: ["wc-op", project.project_id, year],
    queryFn: () => api.wcOpMonthly(project.project_id, year),
    enabled: year > 0 && tab === "op",
  });

  const apMonth = selection?.kind.startsWith("ap") ? selection.month : 0;
  const apSuppliers = useQuery({
    queryKey: ["wc-ap-sup", project.project_id, year, apMonth],
    queryFn: () => api.wcApSuppliers(project.project_id, year, apMonth),
    enabled: year > 0 && tab === "ap" && apMonth > 0,
  });

  const drilldown = useQuery({
    queryKey: ["wc-drill", project.project_id, selection],
    queryFn: () => api.drilldown(project.project_id, {
      kind: selection!.kind,
      year: selection!.year,
      month: selection!.month,
      direction: selection!.direction,
      ...(selection!.supplier ? { supplier: selection!.supplier } : {}),
    }),
    enabled: !!selection,
  });

  const addCandidate = useMutation({
    mutationFn: (voucherIds: string[]) =>
      api.addCandidate(project.project_id, {
        title: selection!.label,
        source_module: "暂估往来",
        source_view: selection!.sourceView,
        selector: {
          kind: selection!.kind,
          year: selection!.year,
          month: selection!.month,
          direction: selection!.direction,
          ...(selection!.supplier ? { supplier: selection!.supplier } : {}),
        },
        reason: `${selection!.label}，纳入疑点库复核。`,
        tags: selection!.tags,
        voucher_ids: voucherIds,
      }),
    onSuccess: (res) => {
      setAddedId(res.group.group_id);
      queryClient.invalidateQueries({ queryKey: ["candidates", project.project_id] });
    },
  });

  useEffect(() => {
    if (!selection) {
      pinSelection(null);
      return;
    }
    pinSelection({
      label: selection.label,
      source_module: "暂估往来",
      source_view: selection.sourceView,
      selector: {
        kind: selection.kind,
        year: selection.year,
        month: selection.month,
        direction: selection.direction,
        ...(selection.supplier ? { supplier: selection.supplier } : {}),
      },
      summary: drilldown.data ? { rows: drilldown.data.row_count } : undefined,
    });
  }, [selection, drilldown.data?.row_count, pinSelection]);

  useEffect(() => {
    if (!monthlyRef.current) return;
    const rows = tab === "ap" ? apMonthly.data?.rows : tab === "or" ? orMonthly.data?.rows : opMonthly.data?.rows;
    if (!rows?.length) return;
    const chart = echarts.init(monthlyRef.current);
    const months = rows.map((r) => periodLabel(r.月份));
    if (tab === "ap") {
      const apRows = rows as import("@/api/client").ApMonthlyRow[];
      chart.setOption({
        tooltip: { trigger: "axis" },
        legend: { textStyle: { color: "#8b97a8" } },
        grid: { left: 16, right: 24, top: 40, bottom: 32, containLabel: true },
        xAxis: { type: "category", data: months },
        yAxis: { type: "value", axisLabel: { formatter: formatWan } },
        series: [
          { name: "暂估贷方增加", type: "bar", data: apRows.map((r) => r.暂估贷方增加) },
          { name: "暂估借方减少", type: "bar", data: apRows.map((r) => r.暂估借方减少) },
          { name: "暂估净额", type: "line", smooth: true, data: apRows.map((r) => r.暂估净额) },
        ],
      });
      const onClick = (params: { componentType?: string; dataIndex?: number; seriesIndex?: number }) => {
        if (params.componentType !== "series" || params.dataIndex == null || params.seriesIndex == null) return;
        const month = apRows[params.dataIndex].月份;
        const direction = AP_DIRS[params.seriesIndex] ?? "net";
        setSelection({
          kind: "ap_accrual_month", year, month, direction,
          label: `${year}年${periodLabel(month)} 应付暂估 ${AP_LABELS[direction]}`,
          sourceView: "应付暂估月度", tags: ["暂估异常", "月度"],
        });
        setAddedId(null);
      };
      chart.on("click", onClick);
      return () => { chart.off("click", onClick); chart.dispose(); };
    }
    if (tab === "or") {
      const orRows = rows as import("@/api/client").OrMonthlyRow[];
      chart.setOption({
        tooltip: { trigger: "axis" },
        legend: { textStyle: { color: "#8b97a8" } },
        grid: { left: 16, right: 24, top: 40, bottom: 32, containLabel: true },
        xAxis: { type: "category", data: months },
        yAxis: { type: "value", axisLabel: { formatter: formatWan } },
        series: [
          { name: "S发生额", type: "bar", data: orRows.map((r) => r.其他应收S发生额) },
          { name: "H发生额", type: "bar", data: orRows.map((r) => r.其他应收H发生额) },
          { name: "净额", type: "line", smooth: true, data: orRows.map((r) => r.其他应收净额) },
        ],
      });
      const onClick = (params: { componentType?: string; dataIndex?: number; seriesIndex?: number }) => {
        if (params.componentType !== "series" || params.dataIndex == null || params.seriesIndex == null) return;
        const month = orRows[params.dataIndex].月份;
        const direction = OR_DIRS[params.seriesIndex] ?? "net";
        setSelection({
          kind: "other_receivable_month", year, month, direction,
          label: `${year}年${periodLabel(month)} 其他应收 ${OR_LABELS[direction]}`,
          sourceView: "其他应收月度", tags: ["往来异常", "月度"],
        });
        setAddedId(null);
      };
      chart.on("click", onClick);
      return () => { chart.off("click", onClick); chart.dispose(); };
    }
    const opRows = rows as import("@/api/client").OpMonthlyRow[];
    chart.setOption({
      tooltip: { trigger: "axis" },
      legend: { textStyle: { color: "#8b97a8" } },
      grid: { left: 16, right: 24, top: 40, bottom: 32, containLabel: true },
      xAxis: { type: "category", data: months },
      yAxis: { type: "value", axisLabel: { formatter: formatWan } },
      series: [
        { name: "预提H", type: "bar", data: opRows.map((r) => r.其他应付预提H) },
        { name: "核销S", type: "bar", data: opRows.map((r) => r.其他应付核销S) },
        { name: "净值", type: "line", smooth: true, data: opRows.map((r) => r.其他应付净值) },
      ],
    });
    const onClick = (params: { componentType?: string; dataIndex?: number; seriesIndex?: number }) => {
      if (params.componentType !== "series" || params.dataIndex == null || params.seriesIndex == null) return;
      const month = opRows[params.dataIndex].月份;
      const direction = OP_DIRS[params.seriesIndex] ?? "net";
      setSelection({
        kind: "other_payable_month", year, month, direction,
        label: `${year}年${periodLabel(month)} 其他应付 ${OP_LABELS[direction]}`,
        sourceView: "其他应付月度", tags: ["往来异常", "月度"],
      });
      setAddedId(null);
    };
    chart.on("click", onClick);
    return () => { chart.off("click", onClick); chart.dispose(); };
  }, [apMonthly.data, orMonthly.data, opMonthly.data, tab, year]);

  useEffect(() => {
    if (!supplierRef.current || !apSuppliers.data?.rows.length || tab !== "ap") return;
    const chart = echarts.init(supplierRef.current);
    const names = apSuppliers.data.rows.map((r) => r.供应商).reverse();
    chart.setOption({
      tooltip: { trigger: "axis" },
      grid: { left: 120, right: 24 },
      xAxis: { type: "value", axisLabel: { formatter: formatWan } },
      yAxis: { type: "category", data: names, axisLabel: { width: 110, overflow: "truncate" } },
      series: [{ type: "bar", data: apSuppliers.data.rows.map((r) => r.暂估净额).reverse() }],
    });
    const onClick = (params: { componentType?: string; dataIndex?: number }) => {
      if (params.componentType !== "series" || params.dataIndex == null || !selection) return;
      const supplier = names[params.dataIndex];
      setSelection({
        kind: "ap_accrual_supplier",
        year, month: selection.month, direction: "net", supplier,
        label: `${year}年${periodLabel(selection.month)} 供应商「${supplier}」暂估`,
        sourceView: "应付暂估供应商", tags: ["供应商", "暂估异常"],
      });
      setAddedId(null);
    };
    chart.on("click", onClick);
    return () => { chart.off("click", onClick); chart.dispose(); };
  }, [apSuppliers.data, tab, selection?.month, year]);

  if (!project.years.length) return <p className="muted">请先在左侧上传序时账。</p>;

  return (
    <div className="module-panel">
      <ModuleInsightCard projectId={project.project_id} moduleKey="暂估往来" />
      <div className="filters">
        <label>年度
          <select value={year} onChange={(e) => { setYear(Number(e.target.value)); setSelection(null); }}>
            {project.years.map((y) => <option key={y} value={y}>{y}</option>)}
          </select>
        </label>
      </div>
      <nav className="module-tabs">
        {([["ap", "应付暂估"], ["or", "其他应收"], ["op", "其他应付"]] as const).map(([id, label]) => (
          <button key={id} type="button" className={tab === id ? "module-tab active" : "module-tab"} onClick={() => { setTab(id); setSelection(null); }}>{label}</button>
        ))}
      </nav>
      <div className="chart-card">
        <h3>月度视图</h3>
        <div ref={monthlyRef} className="chart-box" />
      </div>
      {tab === "ap" && selection?.kind === "ap_accrual_month" && apSuppliers.data?.rows.length ? (
        <div className="chart-card">
          <h3>{selection.month}月 供应商暂估 Top10</h3>
          <div ref={supplierRef} className="chart-box tall" />
        </div>
      ) : null}
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
