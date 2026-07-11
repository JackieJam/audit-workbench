import { AnalysisStatusIcon } from "@/components/AnalysisStatusIcon";
import { AnalysisStageFlow } from "@/components/AnalysisStageFlow";
import { useInsightJobs } from "@/hooks/useInsightJobs";
import type { ModuleInsightPhase } from "@/hooks/useModuleInsight";

type Props = {
  projectId: string;
  moduleKey?: string;
  phase?: ModuleInsightPhase;
  label?: string;
  compact?: boolean;
};

export function AnalysisProgressBar({
  projectId,
  moduleKey,
  phase = "idle",
  label,
  compact = false,
}: Props) {
  const { stages, percent, currentStage, running, overallPercent, runningCount } = useInsightJobs(
    projectId,
    moduleKey,
  );

  const status = phase === "idle" ? "idle" : phase;
  const runningPhase = status === "running";
  const displayPercent = moduleKey ? percent : overallPercent;

  const displayLabel =
    label ??
    (runningPhase
      ? moduleKey
        ? `${moduleKey} · ${running.find((j) => j.module_key === moduleKey)?.stage_label ?? "分析中"}`
        : runningCount > 1
          ? `${runningCount} 个模块分析中`
          : running[0]
            ? `${running[0].module_key} · ${running[0].stage_label}`
            : "模块分析进行中"
      : status === "success"
        ? "模块分析已完成"
        : status === "error"
          ? "模块分析失败"
          : "");

  if (status === "idle") return null;

  return (
    <div
      className={
        compact
          ? `analysis-progress analysis-progress--compact analysis-progress--${status}`
          : `analysis-progress analysis-progress--${status}`
      }
    >
      <div className="analysis-progress__meta">
        <span className="analysis-progress__label">
          <AnalysisStatusIcon status={status} />
          {displayLabel}
        </span>
        {runningPhase && <span className="analysis-progress__pct">{displayPercent}%</span>}
      </div>

      {runningPhase && !compact && running.length > 1 && (
        <div className="analysis-progress__modules">
          {running.map((j) => (
            <span key={j.module_key} className="analysis-progress__chip">
              {j.module_key} {j.percent}%
            </span>
          ))}
        </div>
      )}

      {runningPhase && stages.length > 0 && (
        <AnalysisStageFlow
          stages={stages}
          currentStageId={currentStage || stages[0]?.id || ""}
          compact={compact}
        />
      )}

      {runningPhase && (
        <div className="analysis-progress__track" role="progressbar" aria-valuenow={displayPercent} aria-valuemin={0} aria-valuemax={100}>
          <div className="analysis-progress__fill" style={{ width: `${displayPercent}%` }} />
        </div>
      )}

      {status === "success" && (
        <div className="analysis-progress__track" role="progressbar" aria-valuenow={100} aria-valuemin={0} aria-valuemax={100}>
          <div className="analysis-progress__fill analysis-progress__fill--done" style={{ width: "100%" }} />
        </div>
      )}
    </div>
  );
}
