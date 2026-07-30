import { useEffect, useState } from "react";
import type { EChartsOption } from "echarts";

export type ChartPalette = {
  text: string;
  muted: string;
  grid: string;
  accent: string;
  accentSoft: string;
  onAccent: string;
  success: string;
  warning: string;
  danger: string;
  series: string[];
  tooltipBg: string;
  tooltipBorder: string;
  tooltipText: string;
};

function cssVar(name: string, fallback: string): string {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

/** 在 option 构建时读取当前主题令牌，主题切换后由 useThemeVersion 触发重建。 */
export function chartPalette(): ChartPalette {
  return {
    text: cssVar("--chart-text", "#46556a"),
    muted: cssVar("--chart-muted", "#728197"),
    grid: cssVar("--chart-grid", "#e6eaf0"),
    accent: cssVar("--accent", "#2456d6"),
    accentSoft: cssVar("--accent-soft", "rgba(36, 86, 214, 0.1)"),
    onAccent: cssVar("--on-accent", "#ffffff"),
    success: cssVar("--success", "#15803d"),
    warning: cssVar("--warning", "#b45309"),
    danger: cssVar("--danger", "#dc2626"),
    // 序列色：首色跟随主题主色，其余为中明度色，浅深底均可读
    series: [
      cssVar("--accent", "#2456d6"),
      "#e8a33d",
      "#3aa981",
      "#8a7ff0",
      "#d0638f",
      "#3aa6c9",
    ],
    tooltipBg: cssVar("--panel", "#ffffff"),
    tooltipBorder: cssVar("--border", "#e2e7ee"),
    tooltipText: cssVar("--text", "#16202e"),
  };
}

/** 注入 ECharts 基础主题层：全局文字色 + 主题化 tooltip。 */
export function withChartTheme(option: EChartsOption): EChartsOption {
  const pal = chartPalette();
  const tooltip = {
    backgroundColor: pal.tooltipBg,
    borderColor: pal.tooltipBorder,
    borderWidth: 1,
    textStyle: { color: pal.tooltipText, fontSize: 12 },
    extraCssText:
      "box-shadow: 0 4px 16px rgba(18, 28, 45, 0.12); border-radius: 8px; padding: 8px 12px;",
    ...(option.tooltip as object | undefined),
  };
  return {
    textStyle: { color: pal.muted, ...(option.textStyle as object | undefined) },
    color: pal.series,
    ...option,
    tooltip,
  } as EChartsOption;
}

/** data-theme 变化计数：图表 effect 把它放进 deps，即可在主题切换时重建。 */
export function useThemeVersion(): number {
  const [version, setVersion] = useState(0);
  useEffect(() => {
    const mo = new MutationObserver(() => setVersion((v) => v + 1));
    mo.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    return () => mo.disconnect();
  }, []);
  return version;
}
