import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChartLineUp,
  ClipboardText,
  Detective,
  FolderOpen,
  Moon,
  Plus,
  ShieldCheck,
  SidebarSimple,
  Sparkle,
  Sun,
  type Icon,
} from "@phosphor-icons/react";
import { api } from "@/api/client";
import { UploadPanel } from "@/components/UploadPanel";
import { FinancePage } from "@/pages/FinancePage";
import { SuspectsPage } from "@/pages/SuspectsPage";
import { AuditCasesPage } from "@/pages/AuditCasesPage";
import { SamplingPage } from "@/pages/SamplingPage";
import { AgentPanel } from "@/components/AgentPanel";
import { EmptyState } from "@/components/EmptyState";
import { AgentProvider, useAgent } from "@/context/AgentContext";
import { WorkspaceProvider } from "@/context/WorkspaceContext";
import { LlmProvider } from "@/context/LlmContext";
import { useTheme } from "@/context/ThemeContext";
import { LlmSettingsPanel } from "@/components/LlmSettingsPanel";
import { useAgentPanelWidth } from "@/hooks/useAgentPanelWidth";
import type { ProjectSummary } from "@/api/client";

type Tab = "finance" | "suspects" | "cases" | "sampling" | "llm";

const TABS: { id: Tab; label: string; icon: Icon }[] = [
  { id: "finance", label: "财务画像", icon: ChartLineUp },
  { id: "suspects", label: "疑点工作台", icon: Detective },
  { id: "cases", label: "审计事项", icon: FolderOpen },
  { id: "sampling", label: "抽样底稿", icon: ClipboardText },
  { id: "llm", label: "大模型", icon: Sparkle },
];

function ThemeToggle() {
  const { theme, toggleTheme } = useTheme();
  const dark = theme === "dark";
  return (
    <button
      type="button"
      className="icon-btn"
      onClick={toggleTheme}
      title={dark ? "切换为浅色模式" : "切换为深色模式"}
      aria-label={dark ? "切换为浅色模式" : "切换为深色模式"}
    >
      {dark ? <Sun size={17} /> : <Moon size={17} />}
    </button>
  );
}

function AppWorkspace({
  tab,
  setTab,
  selected,
}: {
  tab: Tab;
  setTab: (t: Tab) => void;
  selected: ProjectSummary | null;
}) {
  const { pinSelection, pinnedContext, focusAgentNonce } = useAgent();
  const lastFinanceContextRef = useRef<{
    projectId: string;
    context: NonNullable<typeof pinnedContext>;
  } | null>(null);
  const previousTabRef = useRef(tab);
  const {
    width,
    collapsed,
    workspaceRef,
    onSplitterPointerDown,
    resetWidth,
    nudgeWidth,
    collapse,
    expand,
  } = useAgentPanelWidth();

  useEffect(() => {
    if (focusAgentNonce > 0) expand();
  }, [expand, focusAgentNonce]);

  useEffect(() => {
    if (!selected) return;
    const previousTab = previousTabRef.current;
    previousTabRef.current = tab;
    if (
      lastFinanceContextRef.current &&
      lastFinanceContextRef.current.projectId !== selected.project_id
    ) {
      lastFinanceContextRef.current = null;
    }
    if (previousTab === tab) return;

    if (previousTab === "finance" && pinnedContext) {
      lastFinanceContextRef.current = {
        projectId: selected.project_id,
        context: pinnedContext,
      };
    }

    if (tab === "finance") {
      if (lastFinanceContextRef.current?.projectId === selected.project_id) {
        pinSelection(lastFinanceContextRef.current.context);
      }
    } else if (tab === "suspects") {
      pinSelection({
        label: "疑点工作台",
        source_module: "疑点工作台",
        source_view: "候选疑点与复核状态",
        selector: { kind: "workspace_overview", workspace: "suspects" },
      });
    } else if (tab === "sampling") {
      pinSelection({
        label: "抽样底稿",
        source_module: "抽样底稿",
        source_view: "总体、风险信号、抽样计划与底稿",
        selector: { kind: "workspace_overview", workspace: "sampling" },
      });
    } else if (tab === "cases") {
      pinSelection({
        label: "审计事项",
        source_module: "审计事项",
        source_view: "认定、证据、程序、结论与状态历史",
        selector: { kind: "workspace_overview", workspace: "cases" },
      });
    }
  }, [pinSelection, pinnedContext, selected?.project_id, tab]);

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
          <div hidden={tab !== "finance"} aria-hidden={tab !== "finance"}>
            <FinancePage project={selected} />
          </div>
          {tab === "suspects" && <SuspectsPage project={selected} />}
          {tab === "cases" && <AuditCasesPage project={selected} />}
          {tab === "sampling" && <SamplingPage project={selected} />}
        </main>
        {!collapsed ? (
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
        ) : null}
        <div
          className={`agent-panel-shell${collapsed ? " agent-panel-shell--collapsed" : ""}`}
          style={{ width: collapsed ? 48 : width }}
        >
          {collapsed ? (
            <button
              type="button"
              className="agent-panel-expand"
              onClick={expand}
              title="展开审计助手"
              aria-label="展开审计助手"
            >
              <span aria-hidden>
                <Sparkle size={16} />
              </span>
              <span>助手</span>
            </button>
          ) : (
            <AgentPanel onCollapse={collapse} />
          )}
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

  const apiStatus = health.isLoading
    ? { dot: "status-dot status-dot--pending", text: "正在连接 API…" }
    : health.isError
      ? { dot: "status-dot status-dot--err", text: "API 未连接，请先运行 scripts/dev-api.sh" }
      : {
          dot: "status-dot status-dot--ok",
          text: `API ${health.data?.version ?? ""} 已连接${
            selected
              ? ` · ${selected.project_name}${
                  selectedHasData ? `（${selected.total_rows.toLocaleString()} 行）` : "（待上传）"
                }`
              : ""
          }`,
        };
  const apiStatusFull = health.isSuccess
    ? `数据目录 ${health.data.storage_root}`
    : undefined;

  return (
    <LlmProvider>
      <div className={`app${sidebarCollapsed ? " app--sidebar-collapsed" : ""}`}>
        <header className="app-header">
          <div className="app-header-brand">
            <button
              type="button"
              className="icon-btn"
              onClick={() => setSidebarCollapsed((v) => !v)}
              title={sidebarCollapsed ? "展开项目与上传" : "收起项目与上传"}
              aria-expanded={!sidebarCollapsed}
              aria-label={sidebarCollapsed ? "展开项目与上传" : "收起项目与上传"}
            >
              <SidebarSimple size={18} />
            </button>
            <span className="brand-mark" aria-hidden>
              <ShieldCheck size={19} weight="duotone" />
            </span>
            <div className="brand-text">
              <h1>审计分析工作台</h1>
              <p className="brand-status" title={apiStatusFull}>
                <span className={apiStatus.dot} aria-hidden />
                {apiStatus.text}
              </p>
            </div>
          </div>
          <nav className="tabs">
            {TABS.map((t) => {
              const Icon = t.icon;
              return (
                <button
                  key={t.id}
                  type="button"
                  className={tab === t.id ? "tab active" : "tab"}
                  onClick={() => setTab(t.id)}
                >
                  <Icon size={16} />
                  {t.label}
                </button>
              );
            })}
          </nav>
          <div className="app-header-actions">
            <ThemeToggle />
          </div>
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
                  <Plus size={14} weight="bold" />
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
              </ul>
              {projects.isSuccess && projects.data.length === 0 && (
                <EmptyState
                  kind="project"
                  size="sm"
                  title="还没有项目"
                  description="创建一个项目并上传序时账，开始财务分析。"
                />
              )}

              <UploadPanel
                projectId={selectedId}
                onImported={() => {
                  if (selectedId) {
                    for (const key of [
                      "agent-state",
                      "module-insight",
                      "candidates",
                      "rule-results",
                      "samples",
                    ]) {
                      queryClient.invalidateQueries({ queryKey: [key, selectedId] });
                    }
                  }
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
