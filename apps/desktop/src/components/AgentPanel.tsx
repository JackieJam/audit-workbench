import { useEffect, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { CaretRight } from "@phosphor-icons/react";
import { api, type AgentChatResponse, type InsightJob } from "@/api/client";
import { useAgent } from "@/context/AgentContext";
import { useWorkspace } from "@/context/WorkspaceContext";
import { useLlm } from "@/context/LlmContext";
import { AgentMarkdown } from "@/components/AgentMarkdown";
import { useInsightJobs } from "@/hooks/useInsightJobs";

type ChatMessage = {
  role: string;
  content: string;
  toolCalls?: AgentChatResponse["tool_calls"];
};

const TOOL_LABELS: Record<string, string> = {
  get_project_overview: "项目概览",
  query_journal: "序时账查询",
  set_analysis_currency_scope: "切换财务画像币种",
  get_data_quality_review: "数据质量复核",
  apply_classification_decisions: "应用分类决策",
  get_evidence_inventory: "证据来源清单",
  get_audit_case_summary: "审计事项摘要",
  query_drilldown: "钻取分录",
  list_candidates: "疑点库列表",
  add_to_candidate_pool: "加入疑点库",
  list_analysis_modules: "分析模块目录",
  get_rules_catalog: "规则目录",
  describe_agent_capabilities: "助手能力说明",
  update_rule: "更新规则",
  toggle_rule: "启停规则",
  get_rule_hit_summary: "规则命中摘要",
  suggest_rule_tuning: "调参建议",
  apply_rule_tuning: "应用调参",
  run_sampling_rules: "执行规则",
  extract_samples: "生成样本",
  get_sampling_status: "抽样状态",
  record_rule_feedback: "规则打分",
  list_rule_feedback: "规则反馈记录",
  get_column_mapping_status: "列名映射状态",
  run_module_insight: "模块 AI 分析",
  apply_module_insight_recommendations: "应用 AI 建议",
  get_module_insight_cache: "AI 分析缓存",
  get_module_insight_jobs: "分析进度",
  focus_analysis_view: "打开分析",
  run_cross_year_audit: "跨年稽核",
  get_cross_year_findings: "跨年稽核结果",
};

function jsonPreview(value: unknown) {
  const rendered = JSON.stringify(value, null, 2);
  if (!rendered) return "—";
  return rendered.length > 6000 ? `${rendered.slice(0, 6000)}\n…内容已截断` : rendered;
}

function toolStatus(
  toolCall: AgentChatResponse["tool_calls"][number],
  jobs: InsightJob[],
  resolvedActions: Set<string>,
) {
  const result = toolCall.result ?? {};
  const jobId = typeof result.job_id === "string" ? result.job_id : "";
  const liveJob = jobs.find((job) => job.job_id === jobId);
  const actionId = typeof result.action_id === "string" ? result.action_id : "";
  if (resolvedActions.has(actionId) || result.action_status === "approved") return ["已批准", "success"] as const;
  if (result.action_status === "rejected") return ["已拒绝", "muted"] as const;
  if (result.action_status === "failed") return ["执行失败", "error"] as const;
  if (liveJob?.status === "queued") return ["排队等待", "running"] as const;
  if (liveJob?.status === "running") return [`${liveJob.stage_label} ${liveJob.percent}%`, "running"] as const;
  if (liveJob?.status === "done" || result.status === "done") return ["已完成", "success"] as const;
  if (liveJob?.status === "error" || result.status === "error" || result.error) return ["执行失败", "error"] as const;
  if (result.approval_required === true) return ["待确认", "running"] as const;
  if (result.status === "queued") return ["已排队", "running"] as const;
  if (result.ok === true || result.cached === true) return ["已完成", "success"] as const;
  return ["已查询", "success"] as const;
}

function ToolCallsCard({
  toolCalls,
  jobs,
  resolvedActions,
  onResolve,
}: {
  toolCalls: AgentChatResponse["tool_calls"];
  jobs: InsightJob[];
  resolvedActions: Set<string>;
  onResolve: (actionId: string, decision: "approve" | "reject") => void;
}) {
  if (!toolCalls.length) return null;
  return (
    <div className="agent-tool-calls">
      {toolCalls.map((tc, i) => {
        const actionId = typeof tc.result?.action_id === "string" ? tc.result.action_id : "";
        const needsApproval = tc.result?.approval_required === true && actionId && !resolvedActions.has(actionId);
        const jobId = typeof tc.result?.job_id === "string" ? tc.result.job_id : "";
        const liveJob = jobs.find((job) => job.job_id === jobId);
        const [statusLabel, statusTone] = toolStatus(tc, jobs, resolvedActions);
        return (
          <div key={`${tc.tool}-${jobId}-${i}`} className="agent-tool-call">
            <div className="agent-tool-call__head">
              <span className="agent-tool-call__name">{TOOL_LABELS[tc.tool] ?? tc.tool}</span>
              <span className={`agent-tool-call__meta agent-tool-call__meta--${statusTone}`}>
                {statusLabel}
              </span>
            </div>
            {jobId ? (
              <div className="agent-tool-call__job">
                <code>{jobId}</code>
                {liveJob ? <span>{liveJob.stage_label}</span> : null}
              </div>
            ) : null}
            {needsApproval ? (
              <div className="agent-tool-call__approval">
                <button type="button" className="btn-ghost" onClick={() => onResolve(actionId, "approve")}>
                  批准
                </button>
                <button type="button" className="btn-ghost danger" onClick={() => onResolve(actionId, "reject")}>
                  拒绝
                </button>
              </div>
            ) : null}
            <details className="agent-tool-call__details">
              <summary>查看调用详情</summary>
              <span>参数</span>
              <pre>{jsonPreview(tc.args)}</pre>
              <span>结果</span>
              <pre>{jsonPreview(liveJob ? { ...tc.result, live_job: liveJob } : tc.result)}</pre>
            </details>
          </div>
        );
      })}
    </div>
  );
}

export function AgentPanel({ onCollapse }: { onCollapse?: () => void }) {
  const {
    projectId,
    pinnedContext,
    suggestions,
    messages,
    refreshState,
    draftPrompt,
    focusAgentNonce,
    clearDraftPrompt,
  } = useAgent();
  const { applyUiActions } = useWorkspace();
  const { selectedProfileId, selectedProfile } = useLlm();
  const { jobs: insightJobs } = useInsightJobs(projectId);
  const [input, setInput] = useState("");
  const [localMessages, setLocalMessages] = useState<ChatMessage[]>([]);
  const [resolvedActions, setResolvedActions] = useState<Set<string>>(() => new Set());
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const panelRef = useRef<HTMLElement>(null);

  useEffect(() => {
    setLocalMessages(messages.map((m) => ({ role: m.role, content: m.content, toolCalls: m.tool_calls })));
  }, [messages]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [localMessages]);

  useEffect(() => {
    if (!focusAgentNonce || !draftPrompt) return;
    const text = draftPrompt;
    setInput(text);
    clearDraftPrompt();
    panelRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    window.requestAnimationFrame(() => {
      inputRef.current?.focus();
      inputRef.current?.setSelectionRange(text.length, text.length);
    });
  }, [focusAgentNonce, draftPrompt, clearDraftPrompt]);

  const chat = useMutation({
    mutationFn: (text: string) =>
      api.agentChat(projectId!, text, pinnedContext, { profileId: selectedProfileId }),
    onSuccess: (res) => {
      setLocalMessages((prev) => [
        ...prev,
        { role: "assistant", content: res.reply, toolCalls: res.tool_calls },
      ]);
      if (res.ui_actions?.length) applyUiActions(res.ui_actions);
      refreshState();
    },
  });
  const resolveAction = useMutation({
    mutationFn: ({ actionId, decision }: { actionId: string; decision: "approve" | "reject" }) =>
      api.resolveAgentAction(projectId!, actionId, decision),
    onSuccess: (res) => {
      setResolvedActions((current) => new Set(current).add(res.action_id));
      if (res.ui_actions?.length) applyUiActions(res.ui_actions);
      refreshState();
    },
  });

  if (!projectId) {
    return (
      <aside className="agent-panel" ref={panelRef}>
        <div className="agent-panel__title-row">
          <h3>审计助手</h3>
          {onCollapse ? (
            <button type="button" className="agent-panel__collapse" onClick={onCollapse} aria-label="收起审计助手">
              <CaretRight size={15} weight="bold" />
            </button>
          ) : null}
        </div>
        <p className="muted">选择项目并上传序时账后，可在此提问。</p>
      </aside>
    );
  }

  const send = (text: string) => {
    const msg = text.trim();
    if (!msg || chat.isPending) return;
    setLocalMessages((prev) => [...prev, { role: "user", content: msg }]);
    setInput("");
    chat.mutate(msg);
  };

  return (
    <aside className="agent-panel" ref={panelRef}>
      <header className="agent-panel__head">
        <div className="agent-panel__title-row">
          <h3>审计助手</h3>
          {onCollapse ? (
            <button
              type="button"
              className="agent-panel__collapse"
              onClick={onCollapse}
              title="收起助手，释放图表空间"
              aria-label="收起审计助手"
            >
              <CaretRight size={15} weight="bold" />
            </button>
          ) : null}
        </div>
        <p className="muted">中枢 Agent · 可查询序时账、解释画像、复核口径并编排疑点与抽样</p>
        <details className="agent-boundary">
          <summary>能力与证据边界</summary>
          <p>
            可查询序时账、派生画像、规则、疑点与抽样；当前未接入 ERP 主数据、科目余额表、财务报表、合同、发票或银行流水。
            工具卡中的真实状态和任务编号优先于回答正文。
          </p>
        </details>
        {selectedProfile && (
          <p className="muted llm-active-chip">
            {selectedProfile.profile_name} · {selectedProfile.model}
            {selectedProfile.key_configured ? "" : " · 未配置密钥"}
          </p>
        )}
      </header>

      {pinnedContext && (
        <div className="agent-context-chip">
          <span className="agent-context-chip__label">当前分析范围</span>
          <span className="agent-context-chip__path">
            {pinnedContext.source_module} / {pinnedContext.source_view}
          </span>
          <strong>{pinnedContext.label}</strong>
        </div>
      )}

      <div className="agent-messages">
        {localMessages.length === 0 && (
          <p className="muted">
            可直接问具体科目、月份、客户、供应商或凭证，也可要求检查数据质量、证据缺口和疑点事项。
          </p>
        )}
        {localMessages.map((m, i) => (
          <div key={i} className={m.role === "user" ? "agent-msg agent-msg--user" : "agent-msg agent-msg--bot"}>
            {m.role === "assistant" ? (
              <>
                {m.toolCalls?.length ? (
                  <ToolCallsCard
                    toolCalls={m.toolCalls}
                    jobs={insightJobs}
                    resolvedActions={resolvedActions}
                    onResolve={(actionId, decision) => resolveAction.mutate({ actionId, decision })}
                  />
                ) : null}
                <AgentMarkdown content={m.content} />
              </>
            ) : (
              m.content
            )}
          </div>
        ))}
        {chat.isPending && (
          <p className="muted">正在请求模型；尚未返回真实工具调用或任务编号。</p>
        )}
        {chat.isError && <p className="error">{String(chat.error)}</p>}
        <div ref={bottomRef} />
      </div>

      {suggestions.length > 0 && (
        <div className="agent-suggestions">
          {suggestions.map((s) => (
            <button key={s} type="button" className="agent-suggestion" onClick={() => send(s)}>
              {s}
            </button>
          ))}
        </div>
      )}

      <form
        className="agent-input-row"
        onSubmit={(e) => {
          e.preventDefault();
          send(input);
        }}
      >
        <textarea
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="输入审计问题…"
          rows={2}
        />
        <button type="submit" className="btn-primary" disabled={chat.isPending || !input.trim()}>
          发送
        </button>
      </form>
    </aside>
  );
}
