import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type * as echarts from "echarts";
import { api, type ProjectSummary, type YearProfile } from "@/api/client";
import { ChartLoadingBar } from "@/components/ChartLoadingBar";
import { DrilldownPanel } from "@/components/DrilldownPanel";
import { useAgent } from "@/context/AgentContext";
import { useEcharts } from "@/hooks/useEcharts";
import { useEnsureProfiles } from "@/hooks/useEnsureProfiles";
import { usePreferredYear } from "@/hooks/usePreferredYear";
import { moduleOverviewSelection } from "@/lib/agentContext";

type Props = { project: ProjectSummary; preferredYear?: number | null };

type ProfileSelection =
  | { kind: "benford"; digit: number }
  | { kind: "month_end"; month: number };

function periodLabel(period: number) {
  return period === 13 ? "13期" : `${period}月`;
}

function formatWan(value: number) {
  return `${(value / 10000).toFixed(1)}万`;
}

function formatAmount(value: number | undefined) {
  if (value == null || !Number.isFinite(value)) return "—";
  if (Math.abs(value) >= 100_000_000) return `${(value / 100_000_000).toFixed(2)}亿`;
  if (Math.abs(value) >= 10_000) return `${(value / 10_000).toFixed(1)}万`;
  return value.toLocaleString(undefined, { maximumFractionDigits: 0 });
}

function formatPercent(value: number | undefined) {
  return value == null ? "—" : `${(value * 100).toFixed(1)}%`;
}

function mapValue(map: Record<string, number> | undefined, key: number) {
  return map?.[String(key)] ?? 0;
}

function selectionPayload(selection: ProfileSelection, year: number) {
  if (selection.kind === "benford") {
    return {
      label: `${year}年 · 本福特首位数字 ${selection.digit}`,
      sourceView: "本福特首位数字",
      selector: {
        kind: "profile_benford_digit",
        year,
        digit: selection.digit,
      },
      reason: `本福特首位数字 ${selection.digit} 的偏离筛查样本；该指标仅用于确定复核范围，不单独构成异常结论。`,
      tags: ["统计画像", "本福特"],
    };
  }
  return {
    label: `${year}年${selection.month}月 · 月末最后5天`,
    sourceView: "月末集中度",
    selector: {
      kind: "profile_month_end",
      year,
      month: selection.month,
    },
    reason: `${year}年${selection.month}月最后5天入账凭证，纳入截止性和期末调节风险复核。`,
    tags: ["统计画像", "月末集中"],
  };
}

export function ProfilePanel({ project, preferredYear }: Props) {
  const [year, setYear] = useState(project.years[0] ?? 0);
  const [selection, setSelection] = useState<ProfileSelection | null>(null);
  const [addedId, setAddedId] = useState<string | null>(null);
  const monthlyRef = useRef<HTMLDivElement>(null);
  const benfordRef = useRef<HTMLDivElement>(null);
  const monthEndRef = useRef<HTMLDivElement>(null);
  const queryClient = useQueryClient();
  const { pinSelection } = useAgent();

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

  useEffect(() => {
    setSelection(null);
    setAddedId(null);
  }, [year]);

  const profileMap = profiles.data?.profiles ?? {};
  const yearKeys = Object.keys(profileMap).map(Number).sort();
  const p: YearProfile | undefined = profileMap[String(year)];
  const temporal = p?.temporal_patterns;
  const benford = p?.benford_first_digit;
  const amountDistribution = p?.amount_distribution;

  const monthlyRows = useMemo(() => {
    if (!temporal?.monthly_count) return [];
    return Object.keys(temporal.monthly_count)
      .map(Number)
      .sort((a, b) => a - b)
      .map((month) => ({
        month,
        vouchers: mapValue(temporal.monthly_count, month),
        amount: mapValue(temporal.monthly_amount, month),
      }));
  }, [temporal?.monthly_amount, temporal?.monthly_count]);

  const benfordRows = useMemo(
    () =>
      Array.from({ length: 9 }, (_, index) => {
        const digit = index + 1;
        return {
          digit,
          observed: mapValue(benford?.observed, digit),
          expected: mapValue(benford?.expected, digit),
        };
      }),
    [benford?.expected, benford?.observed],
  );

  const monthEndRows = useMemo(
    () =>
      Array.from({ length: 12 }, (_, index) => {
        const month = index + 1;
        return { month, ratio: mapValue(temporal?.month_end_concentration, month) };
      }),
    [temporal?.month_end_concentration],
  );

  const maxMonthEnd = monthEndRows.reduce(
    (current, item) => (item.ratio > current.ratio ? item : current),
    { month: 0, ratio: 0 },
  );

  const buildMonthlyOption = useCallback((): echarts.EChartsOption | null => {
    if (!monthlyRows.length) return null;
    return {
      backgroundColor: "transparent",
      tooltip: { trigger: "axis" },
      legend: { textStyle: { color: "#8b97a8" } },
      grid: { left: 16, right: 24, top: 40, bottom: 32, containLabel: true },
      xAxis: {
        type: "category",
        data: monthlyRows.map((row) => periodLabel(row.month)),
        axisLabel: { color: "#8b97a8" },
      },
      yAxis: [
        { type: "value", name: "凭证数", axisLabel: { color: "#8b97a8" } },
        {
          type: "value",
          name: "借方金额",
          axisLabel: { color: "#8b97a8", formatter: formatWan },
        },
      ],
      series: [
        { name: "凭证数", type: "bar", data: monthlyRows.map((row) => row.vouchers) },
        {
          name: "借方金额",
          type: "line",
          yAxisIndex: 1,
          smooth: true,
          data: monthlyRows.map((row) => row.amount),
        },
      ],
    };
  }, [monthlyRows]);

  const buildBenfordOption = useCallback((): echarts.EChartsOption => ({
    backgroundColor: "transparent",
    tooltip: { trigger: "axis", valueFormatter: (value) => formatPercent(Number(value)) },
    legend: { textStyle: { color: "#8b97a8" } },
    grid: { left: 12, right: 18, top: 40, bottom: 28, containLabel: true },
    xAxis: {
      type: "category",
      data: benfordRows.map((row) => String(row.digit)),
      axisLabel: { color: "#8b97a8" },
    },
    yAxis: {
      type: "value",
      max: (value: { max: number }) => Math.max(0.35, Math.ceil(value.max * 10) / 10),
      axisLabel: { color: "#8b97a8", formatter: (value: number) => `${Math.round(value * 100)}%` },
    },
    series: [
      { name: "实际", type: "bar", data: benfordRows.map((row) => row.observed) },
      {
        name: "理论",
        type: "line",
        smooth: true,
        symbolSize: 7,
        data: benfordRows.map((row) => row.expected),
      },
    ],
  }), [benfordRows]);

  const buildMonthEndOption = useCallback((): echarts.EChartsOption => ({
    backgroundColor: "transparent",
    tooltip: { trigger: "axis", valueFormatter: (value) => formatPercent(Number(value)) },
    grid: { left: 12, right: 18, top: 24, bottom: 28, containLabel: true },
    xAxis: {
      type: "category",
      data: monthEndRows.map((row) => `${row.month}月`),
      axisLabel: { color: "#8b97a8" },
    },
    yAxis: {
      type: "value",
      min: 0,
      max: 1,
      axisLabel: { color: "#8b97a8", formatter: (value: number) => `${Math.round(value * 100)}%` },
    },
    series: [
      {
        name: "月末最后5天凭证占比",
        type: "bar",
        data: monthEndRows.map((row) => row.ratio),
        markLine: {
          symbol: "none",
          lineStyle: { color: "#fbbf24", type: "dashed" },
          label: { color: "#fbbf24", formatter: "内部复核线 60%" },
          data: [{ yAxis: 0.6 }],
        },
      },
    ],
  }), [monthEndRows]);

  useEcharts(monthlyRef, buildMonthlyOption, [monthlyRows], {
    enabled: monthlyRows.length > 0,
  });
  useEcharts(benfordRef, buildBenfordOption, [benfordRows], {
    enabled: !!benford?.sample_size,
    onClick: (_chart, params) => {
      if (params.dataIndex == null) return;
      const row = benfordRows[params.dataIndex];
      if (row) setSelection({ kind: "benford", digit: row.digit });
    },
  });
  useEcharts(monthEndRef, buildMonthEndOption, [monthEndRows], {
    enabled: !!temporal?.month_end_concentration,
    onClick: (_chart, params) => {
      if (params.dataIndex == null) return;
      const row = monthEndRows[params.dataIndex];
      if (row) setSelection({ kind: "month_end", month: row.month });
    },
  });

  const drilldown = useQuery({
    queryKey: ["profile-drilldown", project.project_id, year, selection],
    queryFn: () => api.drilldown(project.project_id, selectionPayload(selection!, year).selector),
    enabled: !!selection,
  });

  const addCandidate = useMutation({
    mutationFn: (voucherIds: string[]) => {
      const payload = selectionPayload(selection!, year);
      return api.addCandidate(project.project_id, {
        title: payload.label,
        source_module: "统计画像",
        source_view: payload.sourceView,
        selector: payload.selector,
        reason: payload.reason,
        tags: payload.tags,
        voucher_ids: voucherIds,
      });
    },
    onSuccess: (result) => {
      setAddedId(result.group.group_id);
      queryClient.invalidateQueries({ queryKey: ["candidates", project.project_id] });
    },
  });

  useEffect(() => {
    if (!selection) {
      pinSelection(moduleOverviewSelection("profile", "统计画像", year));
      return;
    }
    const payload = selectionPayload(selection, year);
    pinSelection({
      label: payload.label,
      source_module: "统计画像",
      source_view: payload.sourceView,
      selector: payload.selector,
      summary: drilldown.data ? { rows: drilldown.data.row_count } : undefined,
    });
  }, [drilldown.data?.row_count, pinSelection, selection, year]);

  if (!project.years.length) return <p className="muted">请先在左侧上传序时账。</p>;

  const ov = p?.overview;
  const loading = profiles.isLoading || profiles.isFetching || rebuild.isPending;
  const voucherPercentiles = amountDistribution?.voucher_level;
  const benfordTone =
    benford?.conformity === "明显偏离" || benford?.conformity === "临界偏离"
      ? " profile-signal--warning"
      : "";

  return (
    <div className="profile-panel">
      <ChartLoadingBar
        loading={loading}
        label="统计画像生成中"
        hint="首次约需数十秒，请稍候"
      />
      {profiles.isError ? <p className="error">{String(profiles.error)}</p> : null}

      {yearKeys.length > 0 ? (
        <div className="pipeline-actions profile-toolbar">
          <div className="profile-year-switcher">
            <span className="profile-year-switcher__label">年度</span>
            <div className="profile-year-switcher__options" role="group" aria-label="统计画像年度">
              {yearKeys.map((item) => (
                <button
                  key={item}
                  type="button"
                  className={`profile-year-switcher__button${item === year ? " active" : ""}`}
                  aria-pressed={item === year}
                  onClick={() => setYear(item)}
                >
                  {item}
                </button>
              ))}
            </div>
          </div>
          <button
            type="button"
            className="btn-ghost"
            onClick={() => rebuild.mutate()}
            disabled={loading}
          >
            重新生成
          </button>
        </div>
      ) : null}

      {ov ? (
        <>
          <p className="profile-overview-line">
            <strong>{ov.total_vouchers.toLocaleString()}</strong> 张凭证 ·
            {" "}{ov.total_rows.toLocaleString()} 行 ·
            {" "}平均 {ov.avg_rows_per_voucher.toFixed(1)} 行/凭证
            {ov.period13_rows ? ` · P13 ${ov.period13_rows.toLocaleString()} 行` : ""}
            {ov.date_range ? ` · ${ov.date_range.start} 至 ${ov.date_range.end}` : ""}
          </p>

          <div className="profile-signal-grid">
            <article className={`profile-signal${benfordTone}`}>
              <span className="profile-signal__label">本福特符合度</span>
              <strong>{benford?.conformity ?? "—"}</strong>
              <span>
                MAD {benford?.mean_absolute_deviation?.toFixed(4) ?? "—"} ·
                {" "}{benford?.sample_size.toLocaleString() ?? 0} 个样本
              </span>
            </article>
            <article className="profile-signal">
              <span className="profile-signal__label">最高月末集中度</span>
              <strong>{formatPercent(maxMonthEnd.ratio)}</strong>
              <span>{maxMonthEnd.month ? `${maxMonthEnd.month}月 · 最后5天凭证占比` : "暂无数据"}</span>
            </article>
            <article className="profile-signal">
              <span className="profile-signal__label">手工凭证占比</span>
              <strong>{formatPercent(p.manual_entry_ratio?.manual_voucher_ratio)}</strong>
              <span>按凭证数计算 · 行占比 {formatPercent(p.manual_entry_ratio?.manual_ratio)}</span>
            </article>
            <article className="profile-signal">
              <span className="profile-signal__label">Top 1% 金额集中度</span>
              <strong>{formatPercent(amountDistribution?.top_1pct_amount_ratio)}</strong>
              <span>{amountDistribution?.top_1pct_voucher_count ?? 0} 张大额凭证</span>
            </article>
          </div>

          <div className="profile-diagnostic-grid">
            <section className="chart-card">
              <h3>本福特首位数字</h3>
              <p className="chart-hint muted">
                {benford?.basis ?? "借方分录绝对额 ≥ 10"}；跨
                {" "}{benford?.order_magnitude_count ?? 0} 个数量级。点击数字回查样本。
                仅作筛查，不单独构成异常结论。
              </p>
              {benford?.sample_size ? (
                <div ref={benfordRef} className="chart-box chart-box--profile" />
              ) : (
                <div className="chart-state">没有满足本福特筛查口径的金额样本。</div>
              )}
            </section>

            <section className="chart-card">
              <h3>月末最后 5 天集中度</h3>
              <p className="chart-hint muted">
                按凭证数计算；60% 为内部复核参考线，不是会计准则阈值。点击月份回查。
              </p>
              <div ref={monthEndRef} className="chart-box chart-box--profile" />
            </section>
          </div>

          <section className="profile-amount-summary">
            <div>
              <h3>凭证级金额分布</h3>
              <p className="muted">每张凭证借方合计，避免借贷双计；用于确定大额抽样层级。</p>
            </div>
            <dl>
              <div><dt>P50</dt><dd>{formatAmount(voucherPercentiles?.p50)}</dd></div>
              <div><dt>P90</dt><dd>{formatAmount(voucherPercentiles?.p90)}</dd></div>
              <div><dt>P99</dt><dd>{formatAmount(voucherPercentiles?.p99)}</dd></div>
              <div><dt>最大值</dt><dd>{formatAmount(voucherPercentiles?.max)}</dd></div>
            </dl>
          </section>

          <section className="chart-card">
            <h3>月度凭证与借方金额趋势</h3>
            <p className="chart-hint muted">凭证数按唯一凭证编号计算；Period 13 单独展示。</p>
            <div ref={monthlyRef} className="chart-box" />
          </section>
        </>
      ) : !loading ? (
        <p className="muted">暂无画像数据。</p>
      ) : null}

      {selection ? (
        <DrilldownPanel
          label={selectionPayload(selection, year).label}
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
      ) : null}
    </div>
  );
}
