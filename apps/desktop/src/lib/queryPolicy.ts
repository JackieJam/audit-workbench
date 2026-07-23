import type { ProjectSummary } from "@/api/client";

const ANALYSIS_CACHE_RETENTION_MS = 30 * 60 * 1000;

/**
 * 财务聚合是项目数据版本与筛选条件的确定性结果。
 * 同一数据版本内保持新鲜；重新导入后 updated_at 变化，查询键自动切换。
 */
export const financialAnalysisQueryOptions = {
  staleTime: Number.POSITIVE_INFINITY,
  gcTime: ANALYSIS_CACHE_RETENTION_MS,
  refetchOnMount: false,
  refetchOnWindowFocus: false,
  refetchOnReconnect: false,
} as const;

export function projectDataKey(project: ProjectSummary) {
  return [project.project_id, project.updated_at] as const;
}
