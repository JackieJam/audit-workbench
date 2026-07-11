import { useQuery } from "@tanstack/react-query";
import { api, type ProjectSummary } from "@/api/client";

function profilesNeedBuild(
  years: number[],
  profiles: Record<string, unknown> | undefined,
): boolean {
  if (!years.length) return false;
  const map = profiles ?? {};
  if (!Object.keys(map).length) return true;
  return years.some((y) => !(map[String(y)] ?? map[y]));
}

/** 若 state 中尚无画像，则自动 POST 生成；否则读缓存。 */
export function useEnsureProfiles(project: ProjectSummary | null) {
  const yearsKey = project?.years?.join(",") ?? "";
  return useQuery({
    queryKey: ["profiles", project?.project_id, yearsKey],
    queryFn: async () => {
      if (!project?.project_id) throw new Error("无项目");
      const cached = await api.getProfiles(project.project_id);
      if (profilesNeedBuild(project.years, cached.profiles as Record<string, unknown>)) {
        return api.buildProfiles(project.project_id);
      }
      return cached;
    },
    enabled: !!(project?.years?.length),
    staleTime: 10 * 60 * 1000,
  });
}
