import { useEffect, useRef } from "react";
import { useMutation, useIsMutating, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type ModuleInsight } from "@/api/client";
import { useLlm } from "@/context/LlmContext";
import { useInsightJobs } from "@/hooks/useInsightJobs";

export const moduleInsightQueryKey = (projectId: string, moduleKey: string) =>
  ["module-insight", projectId, moduleKey] as const;

export const moduleInsightMutationKey = (projectId: string, moduleKey: string) =>
  ["module-insight-generate", projectId, moduleKey] as const;

export function useModuleInsight(projectId: string, moduleKey: string) {
  const queryClient = useQueryClient();
  const { selectedProfileId } = useLlm();
  const queryKey = moduleInsightQueryKey(projectId, moduleKey);
  const mutationKey = moduleInsightMutationKey(projectId, moduleKey);
  const jobs = useInsightJobs(projectId, moduleKey);
  const refreshedJobRef = useRef<string | null>(null);

  const cached = useQuery({
    queryKey,
    queryFn: () => api.getModuleInsight(projectId, moduleKey),
  });

  const generate = useMutation({
    mutationKey,
    mutationFn: () => api.startModuleInsightJob(projectId, moduleKey, { profileId: selectedProfileId }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["insight-jobs", projectId] });
    },
  });

  const isGenerating = useIsMutating({ mutationKey }) > 0;

  useEffect(() => {
    const job = jobs.moduleJob;
    if (job?.status !== "done" || refreshedJobRef.current === job.job_id) return;
    refreshedJobRef.current = job.job_id;
    queryClient.invalidateQueries({ queryKey });
  }, [jobs.moduleJob?.job_id, jobs.moduleJob?.status, queryClient, queryKey]);

  return {
    cached,
    generate,
    isGenerating,
    moduleJob: jobs.moduleJob,
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

/** 项目级模块分析状态：进行中 / 刚完成 / 失败 */
export function useModuleInsightStatus(projectId: string | null | undefined) {
  const insightJobs = useInsightJobs(projectId);
  const active = insightJobs.jobs.filter((job) => job.status === "queued" || job.status === "running");
  const failed = insightJobs.jobs.filter((job) => job.status === "error");
  const completed = insightJobs.jobs.filter((job) => job.status === "done");
  const phase: ModuleInsightPhase = active.length
    ? "running"
    : failed.length
      ? "error"
      : completed.length
        ? "success"
        : "idle";
  const inFlight = active.length;

  const label =
    phase === "running"
      ? inFlight > 1
        ? `${inFlight} 个模块分析进行中`
        : "模块分析进行中"
      : phase === "success"
        ? "模块分析已完成"
        : phase === "error"
          ? failed[0]?.error || "模块分析失败，请检查大模型配置"
          : "";

  return { inFlight, phase, label, visible: phase !== "idle" };
}
