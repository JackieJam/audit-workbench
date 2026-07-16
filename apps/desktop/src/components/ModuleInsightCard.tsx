import { useAgent } from "@/context/AgentContext";

type Props = {
  projectId: string;
  moduleKey: string;
};

/** 紧凑入口：把模块风险分析交给右侧 Agent，替代原大卡片。 */
export function ModuleInsightCard({ moduleKey }: Props) {
  const { askAgent } = useAgent();

  return (
    <div className="insight-launch">
      <button
        type="button"
        className="insight-launch__btn"
        title={`在右侧助手中分析「${moduleKey}」风险并给出抽样建议`}
        onClick={() =>
          askAgent(
            `请对「${moduleKey}」模块做 AI 风险分析：基于财务摘要与模块聚合数据，总结主要风险点，并给出可入库的抽样建议（必要时调用 run_module_insight / apply_module_insight_recommendations）。`,
          )
        }
      >
        <span className="insight-launch__icon" aria-hidden>
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
            <path
              d="M8 1.5c.4 0 .75.25.9.62l.55 1.4a4.5 4.5 0 0 0 2.53 2.53l1.4.55c.37.15.62.5.62.9s-.25.75-.62.9l-1.4.55a4.5 4.5 0 0 0-2.53 2.53l-.55 1.4a1 1 0 0 1-1.8 0l-.55-1.4A4.5 4.5 0 0 0 4.02 8.9l-1.4-.55A1 1 0 0 1 2 7.5c0-.4.25-.75.62-.9l1.4-.55a4.5 4.5 0 0 0 2.53-2.53l.55-1.4c.15-.37.5-.62.9-.62Z"
              stroke="currentColor"
              strokeWidth="1.2"
              strokeLinejoin="round"
            />
            <circle cx="8" cy="7.5" r="1.4" fill="currentColor" />
          </svg>
        </span>
        <span>AI 风险分析</span>
      </button>
    </div>
  );
}
