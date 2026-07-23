import { useEffect, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api, type AgentChatResponse } from "@/api/client";
import { useAgent } from "@/context/AgentContext";
import { useWorkspace } from "@/context/WorkspaceContext";
import { useLlm } from "@/context/LlmContext";
import { AgentMarkdown } from "@/components/AgentMarkdown";

type ChatMessage = {
  role: string;
  content: string;
  toolCalls?: AgentChatResponse["tool_calls"];
};

const TOOL_LABELS: Record<string, string> = {
  get_project_overview: "项目概览",
  get_rules_catalog: "规则目录",
  update_rule: "更新规则",
  toggle_rule: "启停规则",
  suggest_rule_tuning: "调参建议",
  apply_rule_tuning: "应用调参",
  run_sampling_rules: "执行规则",
  extract_samples: "生成样本",
  get_sampling_status: "抽样状态",
  record_rule_feedback: "规则打分",
  run_module_insight: "模块 AI 分析",
  apply_module_insight_recommendations: "应用 AI 建议",
  get_module_insight_cache: "AI 分析缓存",
  get_module_insight_jobs: "分析进度",
  focus_analysis_view: "打开分析",
  run_cross_year_audit: "跨年稽核",
};

function ToolCallsCard({
  toolCalls,
  resolvedActions,
  onResolve,
}: {
  toolCalls: AgentChatResponse["tool_calls"];
  resolvedActions: Set<string>;
  onResolve: (actionId: string, decision: "approve" | "reject") => void;
}) {
  if (!toolCalls.length) return null;
  return (
    <div className="agent-tool-calls">
      {toolCalls.map((tc, i) => {
        const actionId = typeof tc.result?.action_id === "string" ? tc.result.action_id : "";
        const needsApproval = tc.result?.approval_required === true && actionId && !resolvedActions.has(actionId);
        return (
        <div key={`${tc.tool}-${i}`} className="agent-tool-call">
          <span className="agent-tool-call__name">{TOOL_LABELS[tc.tool] ?? tc.tool}</span>
          {resolvedActions.has(actionId) || tc.result?.action_status === "approved" ? (
            <span className="agent-tool-call__meta">已批准</span>
          ) : tc.result?.action_status === "rejected" ? (
            <span className="agent-tool-call__meta">已拒绝</span>
          ) : tc.result?.action_status === "failed" ? (
            <span className="agent-tool-call__meta error">执行失败</span>
          ) : needsApproval ? (
            <span className="agent-tool-call__meta">
              待确认
              <button type="button" className="btn-ghost" onClick={() => onResolve(actionId, "approve")}>批准</button>
              <button type="button" className="btn-ghost danger" onClick={() => onResolve(actionId, "reject")}>拒绝</button>
            </span>
          ) : tc.result?.error ? (
            <span className="agent-tool-call__meta error">{String(tc.result.error)}</span>
          ) : tc.result?.ok === true || tc.result?.cached === true ? (
            <span className="agent-tool-call__meta">已完成</span>
          ) : tc.result?.count !== undefined ? (
            <span className="agent-tool-call__meta">已查询</span>
          ) : (
            <span className="agent-tool-call__meta">已查询</span>
          )}
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
              ›
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
              ›
            </button>
          ) : null}
        </div>
        <p className="muted">基于当前序时账与左侧选中范围 · 可对话打开左侧分析模块</p>
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
            可从各分析模块点「AI 风险分析」，或直接提问：项目概览、规则改参、生成样本、跨年稽核…
          </p>
        )}
        {localMessages.map((m, i) => (
          <div key={i} className={m.role === "user" ? "agent-msg agent-msg--user" : "agent-msg agent-msg--bot"}>
            {m.role === "assistant" ? (
              <>
                {m.toolCalls?.length ? (
                  <ToolCallsCard
                    toolCalls={m.toolCalls}
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
        {chat.isPending && <p className="muted">思考中…</p>}
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
