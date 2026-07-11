import { useIsMutating } from "@tanstack/react-query";
import { useQuery } from "@tanstack/react-query";
import { api, type InsightJob } from "@/api/client";
export function useInsightJobs(projectId: string | null | undefined, moduleKey?: string) {
  const mutating = useIsMutating({
    predicate: (m) =>
      !!projectId &&
      Array.isArray(m.options.mutationKey) &&
      m.options.mutationKey[0] === "module-insight-generate" &&
      m.options.mutationKey[1] === projectId &&
      (!moduleKey || m.options.mutationKey[2] === moduleKey),
  });

  const q = useQuery({
    queryKey: ["insight-jobs", projectId],
    queryFn: () => api.getInsightJobs(projectId!),
    enabled: !!projectId,
    refetchInterval: (query) => {
      const data = query.state.data;
      if (mutating > 0) return 700;
      if (data && data.running_count > 0) return 700;
      if (data?.jobs.some((j) => j.status === "done" || j.status === "error")) return 700;
      return false;
    },
  });

  const jobs = q.data?.jobs ?? [];
  const stages = q.data?.stages ?? [];
  const running = jobs.filter((j) => j.status === "running");
  const moduleJob: InsightJob | null = moduleKey
    ? jobs.find((j) => j.module_key === moduleKey) ?? null
    : null;

  const percent = moduleKey
    ? (moduleJob?.percent ?? 0)
    : (q.data?.overall_percent ?? 0);

  const currentStage = moduleJob?.stage ?? running[0]?.stage ?? "";

  return {
    query: q,
    stages,
    jobs,
    running,
    moduleJob,
    percent,
    currentStage,
    overallPercent: q.data?.overall_percent ?? 0,
    runningCount: q.data?.running_count ?? mutating,
    isPolling: mutating > 0 || running.length > 0,
  };
}
