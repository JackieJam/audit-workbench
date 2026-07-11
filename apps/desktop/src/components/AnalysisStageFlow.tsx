import type { InsightStage } from "@/api/client";

type Props = {
  stages: InsightStage[];
  currentStageId: string;
  compact?: boolean;
};

export function AnalysisStageFlow({ stages, currentStageId, compact = false }: Props) {
  if (!stages.length) return null;

  const currentIdx = stages.findIndex((s) => s.id === currentStageId);

  return (
    <ol className={compact ? "analysis-flow analysis-flow--compact" : "analysis-flow"}>
      {stages.map((stage, idx) => {
        let state: "done" | "active" | "pending" = "pending";
        if (currentIdx >= 0) {
          if (idx < currentIdx) state = "done";
          else if (idx === currentIdx) state = "active";
        }
        return (
          <li key={stage.id} className={`analysis-flow__node analysis-flow__node--${state}`}>
            <span className="analysis-flow__dot" aria-hidden />
            <span className="analysis-flow__label">{stage.label}</span>
            {idx < stages.length - 1 && <span className="analysis-flow__edge" aria-hidden />}
          </li>
        );
      })}
    </ol>
  );
}
