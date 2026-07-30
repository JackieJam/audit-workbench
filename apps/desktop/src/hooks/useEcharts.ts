import { useEffect, useRef, type RefObject } from "react";
import * as echarts from "echarts";
import { useThemeVersion, withChartTheme } from "@/lib/chartTheme";

type Options = {
  enabled?: boolean;
  onClick?: (chart: echarts.ECharts, params: echarts.ECElementEvent) => void;
};

/** 绑定 ECharts：复用实例、ResizeObserver、布局稳定后 resize；主题切换时自动重建。 */
export function useEcharts(
  containerRef: RefObject<HTMLDivElement | null>,
  buildOption: () => echarts.EChartsOption | null,
  deps: unknown[],
  options: Options = {},
) {
  const chartRef = useRef<echarts.ECharts | null>(null);
  const onClickRef = useRef(options.onClick);
  onClickRef.current = options.onClick;
  const themeVersion = useThemeVersion();

  useEffect(() => {
    const el = containerRef.current;
    const enabled = options.enabled ?? true;
    if (!el || !enabled) return;

    const option = buildOption();
    if (!option) return;

    let chart = echarts.getInstanceByDom(el) as echarts.ECharts | undefined;
    if (!chart) chart = echarts.init(el);
    chartRef.current = chart;
    chart.setOption(withChartTheme(option), { notMerge: true });

    const onClick = (params: echarts.ECElementEvent) => onClickRef.current?.(chart!, params);
    chart.off("click");
    chart.on("click", onClick);

    const resize = () => chart?.resize();
    const ro = new ResizeObserver(() => resize());
    ro.observe(el);
    requestAnimationFrame(resize);
    window.addEventListener("resize", resize);

    return () => {
      chart?.off("click", onClick);
      window.removeEventListener("resize", resize);
      ro.disconnect();
      chart?.dispose();
      chartRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [containerRef, options.enabled, themeVersion, ...deps]);

  return chartRef;
}
