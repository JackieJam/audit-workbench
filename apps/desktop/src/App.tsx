import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { UploadPanel } from "@/components/UploadPanel";
import { FinancePage } from "@/pages/FinancePage";
import { SuspectsPage } from "@/pages/SuspectsPage";
import { SamplingPage } from "@/pages/SamplingPage";
import { AgentPanel } from "@/components/AgentPanel";
import { AgentProvider, useAgent } from "@/context/AgentContext";
import { WorkspaceProvider } from "@/context/WorkspaceContext";
import { LlmProvider } from "@/context/LlmContext";
import { LlmSettingsPanel } from "@/components/LlmSettingsPanel";
import { useAgentPanelWidth } from "@/hooks/useAgentPanelWidth";
import type { ProjectSummary } from "@/api/client";

type Tab = "finance" | "suspects" | "sampling" | "llm";

const TABS: { id: Tab; label: string }[] = [
  { id: "finance", label: "财务画像" },
  { id: "suspects", label: "疑点工作台" },
  { id: "sampling", label: "抽样底稿" },
  { id: "llm", label: "大模型" },
];

function AppWorkspace({
  tab,
  setTab,
  selected,
}: {
  tab: Tab;
  setTab: (t: Tab) => void;
  selected: ProjectSummary | null;
}) {
  const { pinSelection } = useAgent();
  const { width, workspaceRef, onSplitterPointerDown, resetWidth, nudgeWidth } = useAgentPanelWidth();

  if (tab === "llm") {
    return (
      <main className="main main--full">
        <LlmSettingsPanel />
      </main>
    );
  }

  return (
    <WorkspaceProvider pinSelection={pinSelection} onNavigateMain={(t) => setTab(t)}>
      <div className="workspace" ref={workspaceRef}>
        <main className="main">
          {tab === "finance" && <FinancePage project={selected} />}
          {tab === "suspects" && <SuspectsPage project={selected} />}
          {tab === "sampling" && <SamplingPage project={selected} />}
        </main>
        <div
          className="workspace-splitter"
          role="separator"
          aria-orientation="vertical"
          aria-label="调整审计助手宽度"
          aria-valuenow={width}
          tabIndex={0}
          title="拖动调整宽度 · 双击恢复默认"
          onPointerDown={onSplitterPointerDown}
          onDoubleClick={resetWidth}
          onKeyDown={(e) => {
            const step = e.shiftKey ? 40 : 16;
            if (e.key === "ArrowLeft") {
              e.preventDefault();
              nudgeWidth(step);
            } else if (e.key === "ArrowRight") {
              e.preventDefault();
              nudgeWidth(-step);
            } else if (e.key === "Home") {
              e.preventDefault();
              resetWidth();
            }
          }}
        />
        <div className="agent-panel-shell" style={{ width }}>
          <AgentPanel />
        </div>
      </div>
    </WorkspaceProvider>
  );
}

export default function App() {
  const [tab, setTab] = useState<Tab>("finance");
  const [newName, setNewName] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const queryClient = useQueryClient();

  const health = useQuery({ queryKey: ["health"], queryFn: api.health, retry: 2 });
  const projects = useQuery({ queryKey: ["projects"], queryFn: api.listProjects });

  const createProject = useMutation({
    mutationFn: (name: string) => api.createProject(name),
    onSuccess: (p) => {
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      setSelectedId(p.project_id);
      setNewName("");
      setSidebarCollapsed(false);
    },
  });

  const selected = projects.data?.find((p) => p.project_id === selectedId) ?? null;
  const selectedHasData = !!selected && selected.years.length > 0;

  const selectProject = (projectId: string) => {
    setSelectedId(projectId);
    const project = projects.data?.find((p) => p.project_id === projectId);
    // 已有数据 → 进入分析态，自动收起；无数据 → 保持展开便于上传
    setSidebarCollapsed(!!project && project.years.length > 0);
  };

  return (
    <LlmProvider>
      <div className={`app${sidebarCollapsed ? " app--sidebar-collapsed" : ""}`}>
        <header className="app-header">
          <div className="app-header-brand">
            <button
              type="button"
              className="sidebar-toggle"
              onClick={() => setSidebarCollapsed((v) => !v)}
              title={sidebarCollapsed ? "展开项目与上传" : "收起项目与上传"}
              aria-expanded={!sidebarCollapsed}
            >
              {sidebarCollapsed ? "☰ 项目" : "⟨ 收起"}
            </button>
            <div>
              <h1>审计分析工作台</h1>
              <p className="muted">
                {health.isLoading && "连接 API…"}
                {health.isError && "API 未连接 — 请先运行 scripts/dev-api.sh"}
                {health.isSuccess && (
                  <>
                    API {health.data.version} · 数据目录 {health.data.storage_root}
                    {selected && (
                      <>
                        {" · "}
                        {selected.project_name}
                        {selectedHasData
                          ? `（${selected.total_rows.toLocaleString()} 行）`
                          : "（待上传）"}
                      </>
                    )}
                  </>
                )}
              </p>
            </div>
          </div>
          <nav className="tabs">
            {TABS.map((t) => (
              <button
                key={t.id}
                type="button"
                className={tab === t.id ? "tab active" : "tab"}
                onClick={() => setTab(t.id)}
              >
                {t.label}
              </button>
            ))}
          </nav>
        </header>

        <aside className={`sidebar${sidebarCollapsed ? " sidebar--collapsed" : ""}`} aria-hidden={sidebarCollapsed}>
          {!sidebarCollapsed && (
            <>
              <div className="sidebar-head">
                <h2>项目</h2>
                {selectedHasData && (
                  <button
                    type="button"
                    className="sidebar-collapse-btn"
                    onClick={() => setSidebarCollapsed(true)}
                  >
                    收起
                  </button>
                )}
              </div>
              <form
                className="new-project"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (newName.trim()) createProject.mutate(newName.trim());
                }}
              >
                <input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="新项目名称" />
                <button type="submit" disabled={createProject.isPending}>
                  创建
                </button>
              </form>
              <ul className="project-list">
                {projects.data?.map((p) => (
                  <li
                    key={p.project_id}
                    className={selectedId === p.project_id ? "selected" : ""}
                    onClick={() => selectProject(p.project_id)}
                    onKeyDown={(e) => e.key === "Enter" && selectProject(p.project_id)}
                    role="button"
                    tabIndex={0}
                  >
                    <strong>{p.project_name}</strong>
                    <span className="muted">
                      {p.total_rows.toLocaleString()} 行 · {p.years.join("、") || "无数据"}
                    </span>
                  </li>
                ))}
                {projects.isSuccess && projects.data.length === 0 && (
                  <li className="muted">暂无项目，请先创建</li>
                )}
              </ul>

              <UploadPanel
                projectId={selectedId}
                onImported={() => {
                  queryClient.invalidateQueries({ queryKey: ["projects"] });
                  setSidebarCollapsed(true);
                }}
              />
            </>
          )}
        </aside>

        <AgentProvider projectId={selectedId}>
          <AppWorkspace tab={tab} setTab={setTab} selected={selected} />
        </AgentProvider>
      </div>
    </LlmProvider>
  );
}
