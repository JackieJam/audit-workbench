import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type AgentChatResponse, type AuditSelection } from "@/api/client";

type AgentMessage = {
  role: string;
  content: string;
  at?: string;
  tool_calls?: AgentChatResponse["tool_calls"];
};

type AgentContextValue = {
  projectId: string | null;
  pinnedContext: AuditSelection | null;
  pinSelection: (ctx: AuditSelection | null) => void;
  suggestions: string[];
  messages: AgentMessage[];
  refreshState: () => void;
  draftPrompt: string | null;
  focusAgentNonce: number;
  askAgent: (prompt: string) => void;
  clearDraftPrompt: () => void;
};

const Ctx = createContext<AgentContextValue | null>(null);

export function AgentProvider({ projectId, children }: { projectId: string | null; children: ReactNode }) {
  const queryClient = useQueryClient();
  const [pinnedContext, setPinnedContext] = useState<AuditSelection | null>(null);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [draftPrompt, setDraftPrompt] = useState<string | null>(null);
  const [focusAgentNonce, setFocusAgentNonce] = useState(0);

  const stateQ = useQuery({
    queryKey: ["agent-state", projectId],
    queryFn: () => api.getAgentState(projectId!),
    enabled: !!projectId,
  });

  useEffect(() => {
    if (stateQ.data?.pinned_context) {
      setPinnedContext(stateQ.data.pinned_context as AuditSelection);
    }
    if (stateQ.data?.suggestions?.length) {
      setSuggestions(stateQ.data.suggestions);
    } else if (!stateQ.data?.pinned_context) {
      setSuggestions([
        "对所有模块进行AI风险分析",
        "概览项目年份与规模",
        "抽样规则有哪些？",
        "根据反馈建议规则调参",
      ]);
    }
  }, [stateQ.data?.pinned_context, stateQ.data?.suggestions]);

  const pinSelection = useCallback(
    (ctx: AuditSelection | null) => {
      setPinnedContext(ctx);
      if (!projectId) return;
      api.setAgentContext(projectId, ctx).then((res) => {
        if (res.suggestions?.length) setSuggestions(res.suggestions);
      });
    },
    [projectId],
  );

  const refreshState = useCallback(() => {
    if (projectId) queryClient.invalidateQueries({ queryKey: ["agent-state", projectId] });
  }, [projectId, queryClient]);

  const askAgent = useCallback((prompt: string) => {
    const text = prompt.trim();
    if (!text) return;
    setDraftPrompt(text);
    setFocusAgentNonce((n) => n + 1);
  }, []);

  const clearDraftPrompt = useCallback(() => setDraftPrompt(null), []);

  const value = useMemo(
    () => ({
      projectId,
      pinnedContext,
      pinSelection,
      suggestions,
      messages: (stateQ.data?.messages ?? []) as AgentMessage[],
      refreshState,
      draftPrompt,
      focusAgentNonce,
      askAgent,
      clearDraftPrompt,
    }),
    [
      projectId,
      pinnedContext,
      pinSelection,
      suggestions,
      stateQ.data?.messages,
      refreshState,
      draftPrompt,
      focusAgentNonce,
      askAgent,
      clearDraftPrompt,
    ],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAgent() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAgent must be used within AgentProvider");
  return v;
}
