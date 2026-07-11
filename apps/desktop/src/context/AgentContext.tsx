import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type AuditSelection } from "@/api/client";

type AgentContextValue = {
  projectId: string | null;
  pinnedContext: AuditSelection | null;
  pinSelection: (ctx: AuditSelection | null) => void;
  suggestions: string[];
  messages: { role: string; content: string; at?: string }[];
  refreshState: () => void;
};

const Ctx = createContext<AgentContextValue | null>(null);

export function AgentProvider({ projectId, children }: { projectId: string | null; children: ReactNode }) {
  const queryClient = useQueryClient();
  const [pinnedContext, setPinnedContext] = useState<AuditSelection | null>(null);
  const [suggestions, setSuggestions] = useState<string[]>([]);

  const stateQ = useQuery({
    queryKey: ["agent-state", projectId],
    queryFn: () => api.getAgentState(projectId!),
    enabled: !!projectId,
  });

  useEffect(() => {
    if (stateQ.data?.pinned_context) {
      setPinnedContext(stateQ.data.pinned_context as AuditSelection);
    }
  }, [stateQ.data?.pinned_context]);

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

  const value = useMemo(
    () => ({
      projectId,
      pinnedContext,
      pinSelection,
      suggestions,
      messages: (stateQ.data?.messages ?? []) as { role: string; content: string; at?: string }[],
      refreshState,
    }),
    [projectId, pinnedContext, pinSelection, suggestions, stateQ.data?.messages, refreshState],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAgent() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAgent must be used within AgentProvider");
  return v;
}
