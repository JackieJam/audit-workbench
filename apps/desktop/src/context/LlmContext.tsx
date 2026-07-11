import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type LlmProfile } from "@/api/client";

const STORAGE_KEY = "audit-workbench-llm-profile-id";

type LlmContextValue = {
  profiles: LlmProfile[];
  selectedProfileId: string | null;
  selectedProfile: LlmProfile | null;
  setSelectedProfileId: (id: string | null) => void;
  refreshProfiles: () => void;
  secretBackend: string;
};

const Ctx = createContext<LlmContextValue | null>(null);

export function LlmProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [selectedProfileId, setSelectedProfileIdState] = useState<string | null>(() => {
    try {
      return localStorage.getItem(STORAGE_KEY);
    } catch {
      return null;
    }
  });

  const profilesQ = useQuery({
    queryKey: ["llm-profiles"],
    queryFn: api.listLlmProfiles,
  });

  const profiles = profilesQ.data?.profiles ?? [];
  const secretBackend = profilesQ.data?.secret_backend ?? "";

  useEffect(() => {
    if (!profiles.length) return;
    const defaultProfile = profiles.find((p) => p.is_default) ?? profiles[0];
    if (!selectedProfileId || !profiles.some((p) => p.profile_id === selectedProfileId)) {
      const next = defaultProfile.profile_id;
      setSelectedProfileIdState(next);
      try {
        localStorage.setItem(STORAGE_KEY, next);
      } catch {
        /* ignore */
      }
    }
  }, [profiles, selectedProfileId]);

  const setSelectedProfileId = useCallback((id: string | null) => {
    setSelectedProfileIdState(id);
    try {
      if (id) localStorage.setItem(STORAGE_KEY, id);
      else localStorage.removeItem(STORAGE_KEY);
    } catch {
      /* ignore */
    }
  }, []);

  const refreshProfiles = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ["llm-profiles"] });
  }, [queryClient]);

  const selectedProfile = profiles.find((p) => p.profile_id === selectedProfileId) ?? null;

  const value = useMemo(
    () => ({
      profiles,
      selectedProfileId,
      selectedProfile,
      setSelectedProfileId,
      refreshProfiles,
      secretBackend,
    }),
    [profiles, selectedProfileId, selectedProfile, setSelectedProfileId, refreshProfiles, secretBackend],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useLlm() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useLlm must be used within LlmProvider");
  return v;
}

/** @deprecated Use request body fields profile_id / api_key instead (headers reject non-ASCII). */
export function llmHeaders() {
  return undefined;
}
