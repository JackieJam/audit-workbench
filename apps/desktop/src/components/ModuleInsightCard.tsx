import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowClockwise, ChatCircleText, Sparkle } from "@phosphor-icons/react";
import { api } from "@/api/client";
import { AnalysisProgressBar } from "@/components/AnalysisProgressBar";
import { useAgent } from "@/context/AgentContext";
import { useLlm } from "@/context/LlmContext";
import { useModuleInsight } from "@/hooks/useModuleInsight";

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

function riskClass(level?: string) {
  return level === "高" ? "high" : level === "中" ? "mid" : "low";
}

export function ModuleInsightCard({ projectId, moduleKey }: Props) {
  const queryClient = useQueryClient();
  const { askAgent } = useAgent();
  const { selectedProfile } = useLlm();
  const { generate, isGenerating, moduleJob, insight } = useModuleInsight(projectId, moduleKey);
  const [selected, setSelected] = useState<Set<number>>(() => new Set());

  const active = moduleJob?.status === "queued" || moduleJob?.status === "running";
  const phase = active
    ? "running"
    : moduleJob?.status === "error" || generate.isError
      ? "error"
      : moduleJob?.status === "done"
        ? "success"
        : "idle";
  const findings = (insight?.findings ?? []) as Finding[];
  const recommendations = (insight?.recommendations ?? []) as Recommendation[];

  const apply = useMutation({
    mutationFn: (indices: number[] | undefined) =>
      api.applyModuleInsight(projectId, moduleKey, indices),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["candidates", projectId] });
    },
  });

  const toggleRecommendation = (index: number) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  };

  const prompt = `请基于已经生成的「${moduleKey}」模块 AI 风险分析结果，解释主要风险、证据边界和下一步审计程序。`;

  return (
    <section className="insight-card">
      <header className="insight-card__head">
        <div>
          <div className="insight-card__eyebrow">
            <Sparkle size={14} weight="fill" />
            AI 风险分析
          </div>
          <h3>{moduleKey}</h3>
          <p className="muted">
            基于序时账派生指标；不包含合同、发票、银行流水或 ERP 主数据证据。
          </p>
        </div>
        <div className="insight-card__actions">
          {selectedProfile ? (
            <span className="muted insight-card__model">
              {selectedProfile.profile_name} · {selectedProfile.model}
            </span>
          ) : null}
          <button
            type="button"
            className="btn-primary"
            onClick={() => generate.mutate()}
            disabled={isGenerating || active}
          >
            {active ? (
              "分析中…"
            ) : insight ? (
              <>
                <ArrowClockwise size={14} />
                重新分析
              </>
            ) : (
              <>
                <Sparkle size={14} weight="fill" />
                开始分析
              </>
            )}
          </button>
        </div>
      </header>

      {moduleJob ? (
        <div className="insight-card__job-meta">
          <span>任务 {moduleJob.job_id}</span>
          <span>{moduleJob.stage_label}</span>
          <span>{moduleJob.status === "done" ? "已完成" : moduleJob.status === "error" ? "失败" : "执行中"}</span>
        </div>
      ) : null}

      <AnalysisProgressBar projectId={projectId} moduleKey={moduleKey} phase={phase} compact />

      {moduleJob?.error ? <p className="error">{moduleJob.error}</p> : null}
      {generate.isError ? <p className="error">{String(generate.error)}</p> : null}

      {!insight && !active && !generate.isError ? (
        <p className="muted insight-card__empty">
          点击“开始分析”会立即创建真实后台任务；任务编号、阶段和结果均来自服务端状态。
        </p>
      ) : null}

      {insight ? (
        <div className="insight-card__body">
          {insight.executive_summary ? (
            <div className="insight-card__summary">
              <span className="insight-card__summary-label">执行摘要</span>
              <p>{String(insight.executive_summary)}</p>
            </div>
          ) : null}

          {findings.length ? (
            <section className="insight-findings">
              <h4>主要发现</h4>
              <div className="insight-finding-list">
                {findings.map((finding, index) => (
                  <article key={`${finding.observation}-${index}`} className="insight-finding">
                    <div>
                      <span className={`risk-tag risk-tag--${riskClass(finding.severity)}`}>
                        {finding.severity ?? "中"}
                      </span>
                      <strong>{finding.observation || `发现 ${index + 1}`}</strong>
                    </div>
                    {finding.audit_meaning ? <p>{finding.audit_meaning}</p> : null}
                    {finding.suggested_procedure ? (
                      <p className="muted">建议程序：{finding.suggested_procedure}</p>
                    ) : null}
                  </article>
                ))}
              </div>
            </section>
          ) : null}

          {recommendations.length ? (
            <section className="insight-recs">
              <div className="insight-recs__head">
                <div>
                  <h4>可入库抽样建议</h4>
                  <p className="muted">勾选后由你明确确认写入疑点库。</p>
                </div>
                <div className="insight-recs__buttons">
                  <button
                    type="button"
                    className="btn-ghost"
                    onClick={() => setSelected(new Set(recommendations.map((_, index) => index)))}
                  >
                    全选
                  </button>
                  <button
                    type="button"
                    className="btn-primary"
                    disabled={apply.isPending}
                    onClick={() => apply.mutate(selected.size ? [...selected] : undefined)}
                  >
                    {apply.isPending
                      ? "写入中…"
                      : selected.size
                        ? `写入疑点库（${selected.size}）`
                        : "写入全部建议"}
                  </button>
                </div>
              </div>
              {apply.isSuccess ? (
                <p className="muted">
                  已入库 {apply.data.added} 组，跳过 {apply.data.skipped}
                  {apply.data.errors?.length ? ` · ${apply.data.errors.join("；")}` : ""}
                </p>
              ) : null}
              {apply.isError ? <p className="error">{String(apply.error)}</p> : null}
              <div className="insight-rec-list">
                {recommendations.map((recommendation, index) => (
                  <label key={`${recommendation.title}-${index}`} className="insight-rec-item">
                    <input
                      type="checkbox"
                      checked={selected.has(index)}
                      onChange={() => toggleRecommendation(index)}
                    />
                    <span>
                      <span className="insight-rec-item__title">
                        <span className={`risk-tag risk-tag--${riskClass(recommendation.risk_level)}`}>
                          {recommendation.risk_level ?? "中"}
                        </span>
                        <strong>{recommendation.title || `建议 ${index + 1}`}</strong>
                      </span>
                      {recommendation.reason ? <span>{recommendation.reason}</span> : null}
                      {recommendation.audit_procedure ? (
                        <span className="muted">建议程序：{recommendation.audit_procedure}</span>
                      ) : null}
                    </span>
                  </label>
                ))}
              </div>
            </section>
          ) : null}

          <footer className="insight-card__footer">
            <span className="muted">
              输入签名：{insight.input_signature || "未提供"} · 结果是风险筛查，不是审计结论
            </span>
            <button type="button" className="btn-ghost" onClick={() => askAgent(prompt)}>
              <ChatCircleText size={14} />
              在助手中追问
            </button>
          </footer>
        </div>
      ) : null}
    </section>
  );
}
