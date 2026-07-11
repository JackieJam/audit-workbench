import { useEffect, useRef, useState } from "react";
import { useMutation, useIsMutating, useMutationState, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type ModuleInsight } from "@/api/client";
import { useLlm } from "@/context/LlmContext";

export const moduleInsightQueryKey = (projectId: string, moduleKey: string) =>
  ["module-insight", projectId, moduleKey] as const;

export const moduleInsightMutationKey = (projectId: string, moduleKey: string) =>
  ["module-insight-generate", projectId, moduleKey] as const;

export function useModuleInsight(projectId: string, moduleKey: string) {
  const queryClient = useQueryClient();
  const { selectedProfileId } = useLlm();
  const queryKey = moduleInsightQueryKey(projectId, moduleKey);
  const mutationKey = moduleInsightMutationKey(projectId, moduleKey);

  const cached = useQuery({
    queryKey,
    queryFn: () => api.getModuleInsight(projectId, moduleKey),
  });

  const generate = useMutation({
    mutationKey,
    mutationFn: () => api.generateModuleInsight(projectId, moduleKey, { profileId: selectedProfileId }),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKey, {
        module: moduleKey,
        cached: true,
        insight: data.insight,
      });
    },
  });

  const isGenerating = useIsMutating({ mutationKey }) > 0;

  return {
    cached,
    generate,
    isGenerating,
    insight: cached.data?.insight as ModuleInsight | null | undefined,
  };
}

/** 当前项目下所有进行中的模块分析数量 */
export function useModuleInsightInFlight(projectId: string | null | undefined) {
  const count = useIsMutating({
    predicate: (m) =>
      !!projectId &&
      Array.isArray(m.options.mutationKey) &&
      m.options.mutationKey[0] === "module-insight-generate" &&
      m.options.mutationKey[1] === projectId,
  });
  return count;
}


export type ModuleInsightPhase = "idle" | "running" | "success" | "error";

function _projectInsightPredicate(projectId: string | null | undefined) {
  return (m: { options: { mutationKey?: unknown } }) =>
    !!projectId &&
    Array.isArray(m.options.mutationKey) &&
    m.options.mutationKey[0] === "module-insight-generate" &&
    m.options.mutationKey[1] === projectId;
}

/** 项目级模块分析状态：进行中 / 刚完成 / 失败 */
export function useModuleInsightStatus(projectId: string | null | undefined) {
  const inFlight = useIsMutating({ predicate: _projectInsightPredicate(projectId) });
  const [phase, setPhase] = useState<ModuleInsightPhase>("idle");
  const prevInFlight = useRef(0);

  const recentErrors = useMutationState({
    filters: {
      predicate: (m) => _projectInsightPredicate(projectId)(m) && m.state.status === "error",
    },
  });

  useEffect(() => {
    if (inFlight > 0) {
      setPhase("running");
      prevInFlight.current = inFlight;
      return;
    }

    if (prevInFlight.current > 0) {
      const next: ModuleInsightPhase = recentErrors.length > 0 ? "error" : "success";
      setPhase(next);
      prevInFlight.current = 0;
      const delay = next === "error" ? 6000 : 4000;
      const t = window.setTimeout(() => setPhase("idle"), delay);
      return () => window.clearTimeout(t);
    }
  }, [inFlight, recentErrors.length]);

  const label =
    phase === "running"
      ? inFlight > 1
        ? `${inFlight} 个模块分析进行中`
        : "模块分析进行中"
      : phase === "success"
        ? "模块分析已完成"
        : phase === "error"
          ? "模块分析失败，请检查大模型配置"
          : "";

  return { inFlight, phase, label, visible: phase !== "idle" };
}
