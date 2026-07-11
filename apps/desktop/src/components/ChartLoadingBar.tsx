import { useEffect, useState } from "react";

/** 长时间请求无真实进度时，缓慢爬升到 92%，结束后由调用方清零。 */
export function useSimulatedProgress(active: boolean): number {
  const [percent, setPercent] = useState(0);

  useEffect(() => {
    if (!active) {
      setPercent(0);
      return;
    }
    setPercent(6);
    const timer = window.setInterval(() => {
      setPercent((prev) => {
        if (prev >= 92) return prev;
        const step = Math.max(0.6, (92 - prev) * 0.07);
        return Math.min(92, prev + step);
      });
    }, 450);
    return () => window.clearInterval(timer);
  }, [active]);

  return Math.round(percent);
}

type Props = {
  loading: boolean;
  label?: string;
  hint?: string;
  /** 传入 0–100 时显示确定进度；不传则用不确定滑动条 */
  percent?: number | null;
};

/** 图表 / 导入等长请求的进度条，避免空白等待被当成卡住。 */
export function ChartLoadingBar({
  loading,
  label = "加载中",
  hint = "大数据量可能需要数十秒，请稍候",
  percent = null,
}: Props) {
  if (!loading) return null;

  const determinate = typeof percent === "number" && Number.isFinite(percent);
  const width = determinate ? Math.max(0, Math.min(100, percent)) : null;

  return (
    <div className="chart-loading" role="status" aria-live="polite">
      <div className="chart-loading__meta">
        <span className="chart-loading__label">{label}</span>
        <span className="chart-loading__hint">
          {determinate ? `${width}%` : null}
          {hint ? (determinate ? ` · ${hint}` : hint) : null}
        </span>
      </div>
      {determinate ? (
        <div
          className="chart-loading__track"
          role="progressbar"
          aria-valuenow={width ?? 0}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <div className="analysis-progress__fill" style={{ width: `${width}%` }} />
        </div>
      ) : (
        <div
          className="chart-loading__track analysis-progress__track--legacy-indeterminate"
          role="progressbar"
          aria-valuetext="加载中"
        >
          <div className="analysis-progress__indeterminate" />
        </div>
      )}
    </div>
  );
}
