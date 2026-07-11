import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useLlm } from "@/context/LlmContext";
import { useModuleInsight } from "@/hooks/useModuleInsight";
import { AnalysisProgressBar } from "@/components/AnalysisProgressBar";

type Props = {
  projectId: string;
  moduleKey: string;
};

type Finding = {
  severity?: string;
  observation?: string;
  audit_meaning?: string;
  suggested_procedure?: string;
};

type Recommendation = {
  title?: string;
  risk_level?: string;
  reason?: string;
  audit_procedure?: string;
  condition?: Record<string, unknown>;
};

export function ModuleInsightCard({ projectId, moduleKey }: Props) {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<Set<number>>(() => new Set());
  const { selectedProfile } = useLlm();
  const { generate, isGenerating, insight } = useModuleInsight(projectId, moduleKey);
  const cardPhase = generate.isError ? "error" : isGenerating ? "running" : "idle";

  const apply = useMutation({
    mutationFn: (indices: number[] | undefined) =>
      api.applyModuleInsight(projectId, moduleKey, indices),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["candidates", projectId] });
    },
  });

  const findings = (insight?.findings ?? []) as Finding[];
  const recommendations = (insight?.recommendations ?? []) as Recommendation[];

  const toggleRec = (idx: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });
  };

  return (
    <section className="insight-card">
      <div className="insight-card__head">
        <div>
          <h3>AI 风险分析</h3>
          <p className="muted">基于财务摘要与模块聚合数据，按常见风险点生成报告与抽样建议</p>
        </div>
        <div className="insight-card__actions">
          {selectedProfile && (
            <span className="muted insight-card__model">
              {selectedProfile.profile_name} · {selectedProfile.model}
            </span>
          )}
          <button
            type="button"
            className="btn-primary"
            onClick={() => generate.mutate()}
            disabled={isGenerating}
          >
            {isGenerating ? "分析中…" : insight ? "重新分析" : "生成分析"}
          </button>
        </div>
      </div>

      <AnalysisProgressBar projectId={projectId} moduleKey={moduleKey} phase={cardPhase} compact />

      {generate.isError && <p className="error">{String(generate.error)}</p>}

      {!insight && !isGenerating && (
        <p className="muted">点击「生成分析」获取风险报告；请先在「大模型」页签配置方案与 API Key。</p>
      )}

      {insight && (
        <div className="insight-card__body">
          {insight.executive_summary && (
            <p className="insight-card__summary">{String(insight.executive_summary)}</p>
          )}

          {findings.length > 0 && (
            <div className="insight-findings">
              <h4>主要发现</h4>
              <ul>
                {findings.map((f, i) => (
                  <li key={i}>
                    <span className={`risk-tag risk-tag--${f.severity === "高" ? "high" : f.severity === "中" ? "mid" : "low"}`}>
                      {f.severity ?? "中"}
                    </span>
                    <strong>{f.observation}</strong>
                    {f.audit_meaning && <span className="muted"> — {f.audit_meaning}</span>}
                    {f.suggested_procedure && <div className="muted">{f.suggested_procedure}</div>}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {recommendations.length > 0 && (
            <div className="insight-recs">
              <div className="insight-recs__head">
                <h4>抽样建议</h4>
                <div className="insight-recs__btns">
                  <button
                    type="button"
                    className="btn-ghost"
                    onClick={() => setSelected(new Set(recommendations.map((_, i) => i)))}
                  >
                    全选
                  </button>
                  <button
                    type="button"
                    className="btn-primary"
                    onClick={() =>
                      apply.mutate(selected.size ? [...selected] : undefined)
                    }
                    disabled={apply.isPending}
                  >
                    {apply.isPending
                      ? "应用中…"
                      : selected.size
                        ? `一键应用（${selected.size}）`
                        : "一键应用全部"}
                  </button>
                </div>
              </div>
              {apply.isSuccess && (
                <p className="muted">
                  已入库 {apply.data.added} 组，跳过 {apply.data.skipped}
                  {apply.data.errors?.length ? ` · ${apply.data.errors.join("；")}` : ""}
                </p>
              )}
              <div className="insight-rec-list">
                {recommendations.map((rec, idx) => (
                  <label key={idx} className="insight-rec-item">
                    <input
                      type="checkbox"
                      checked={selected.has(idx)}
                      onChange={() => toggleRec(idx)}
                    />
                    <div>
                      <div className="insight-rec-item__title">
                        <span className={`risk-tag risk-tag--${rec.risk_level === "高" ? "high" : rec.risk_level === "中" ? "mid" : "low"}`}>
                          {rec.risk_level ?? "中"}
                        </span>
                        {rec.title}
                      </div>
                      {rec.reason && <p className="muted">{rec.reason}</p>}
                      {rec.audit_procedure && <p className="muted">{rec.audit_procedure}</p>}
                    </div>
                  </label>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
