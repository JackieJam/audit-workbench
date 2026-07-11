import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import * as echarts from "echarts";
import { api, type ProjectSummary, type YearProfile } from "@/api/client";
import { ChartLoadingBar } from "@/components/ChartLoadingBar";
import { useEnsureProfiles } from "@/hooks/useEnsureProfiles";
import { usePreferredYear } from "@/hooks/usePreferredYear";

type Props = { project: ProjectSummary; preferredYear?: number | null };

export function ProfilePanel({ project, preferredYear }: Props) {
  const [year, setYear] = useState(project.years[0] ?? 0);
  const chartRef = useRef<HTMLDivElement>(null);
  const queryClient = useQueryClient();

  usePreferredYear(preferredYear, project.years, setYear);

  const profiles = useEnsureProfiles(project);

  const rebuild = useMutation({
    mutationFn: () => api.buildProfiles(project.project_id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["profiles", project.project_id] });
    },
  });

  useEffect(() => {
    if (project.years.length && !project.years.includes(year)) setYear(project.years[0]);
  }, [project.years, year]);

  const profileMap = profiles.data?.profiles ?? {};
  const yearKeys = Object.keys(profileMap).map(Number).sort();
  const p: YearProfile | undefined = profileMap[String(year)] ?? profileMap[year];

  const monthlyRows = (() => {
    const tp = p?.temporal_patterns as
      | { monthly_count?: Record<string, number>; monthly_amount?: Record<string, number> }
      | undefined;
    if (!tp?.monthly_count) return [];
    return Object.keys(tp.monthly_count)
      .map((m) => Number(m))
      .sort((a, b) => a - b)
      .map((month) => ({
        month,
        vouchers: tp.monthly_count?.[month] ?? tp.monthly_count?.[String(month)] ?? 0,
        amount: tp.monthly_amount?.[month] ?? tp.monthly_amount?.[String(month)] ?? 0,
      }));
  })();

  useEffect(() => {
    if (!chartRef.current || !monthlyRows.length) return;
    const chart = echarts.init(chartRef.current);
    const rows = monthlyRows;
    chart.setOption({
      backgroundColor: "transparent",
      tooltip: { trigger: "axis" },
      legend: { textStyle: { color: "#8b97a8" } },
      grid: { left: 16, right: 24, top: 40, bottom: 32, containLabel: true },
      xAxis: {
        type: "category",
        data: rows.map((r) => `${r.month}月`),
        axisLabel: { color: "#8b97a8" },
      },
      yAxis: [
        { type: "value", name: "凭证数", axisLabel: { color: "#8b97a8" } },
        { type: "value", name: "金额", axisLabel: { color: "#8b97a8" } },
      ],
      series: [
        { name: "凭证数", type: "bar", data: rows.map((r) => r.vouchers) },
        { name: "金额", type: "line", yAxisIndex: 1, smooth: true, data: rows.map((r) => r.amount) },
      ],
    });
    const onResize = () => chart.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.dispose();
    };
  }, [monthlyRows]);

  if (!project.years.length) return <p className="muted">请先在左侧上传序时账。</p>;

  const ov = p?.overview;
  const loading = profiles.isLoading || profiles.isFetching || rebuild.isPending;

  return (
    <div className="profile-panel">
      <ChartLoadingBar
        loading={loading}
        label="统计画像生成中"
        hint="首次约需数十秒，请稍候"
      />
      {profiles.isError && (
        <p className="error">{String(profiles.error)}</p>
      )}

      {yearKeys.length > 0 && (
        <div className="pipeline-actions">
          <label className="filters">
            年度
            <select value={year} onChange={(e) => setYear(Number(e.target.value))}>
              {yearKeys.map((y) => (
                <option key={y} value={y}>{y}</option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className="btn-ghost"
            onClick={() => rebuild.mutate()}
            disabled={loading}
          >
            重新生成
          </button>
        </div>
      )}

      {ov ? (
        <>
          <div className="metric-grid">
            <div className="metric-card"><span className="muted">总行数</span><strong>{ov.total_rows.toLocaleString()}</strong></div>
            <div className="metric-card"><span className="muted">凭证数</span><strong>{ov.total_vouchers.toLocaleString()}</strong></div>
            <div className="metric-card"><span className="muted">P13 行</span><strong>{ov.period13_rows ?? 0}</strong></div>
            <div className="metric-card"><span className="muted">平均分录/凭证</span><strong>{ov.avg_rows_per_voucher.toFixed(1)}</strong></div>
          </div>
          {ov.date_range && (
            <p className="muted">日期范围 {ov.date_range.start} ~ {ov.date_range.end}</p>
          )}
          {p?.manual_entry_ratio?.manual_ratio != null && (
            <p className="muted">手工凭证占比 {(p.manual_entry_ratio.manual_ratio * 100).toFixed(1)}%</p>
          )}
          <div className="chart-card">
            <h3>月度凭证与金额趋势</h3>
            <div ref={chartRef} className="chart-box" />
          </div>
        </>
      ) : (
        !loading && <p className="muted">暂无画像数据。</p>
      )}
    </div>
  );
}
