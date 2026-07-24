import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { AgentUiAction, AuditSelection } from "@/api/client";

export type MainTab = "finance" | "suspects" | "sampling";
export type FinanceModule =
  | "income"
  | "expense"
  | "other_pnl"
  | "working_capital"
  | "balance_sheet"
  | "adjustment"
  | "profile"
  | "cross";

type WorkspaceValue = {
  mainTab: MainTab;
  setMainTab: (tab: MainTab) => void;
  financeModule: FinanceModule;
  setFinanceModule: (m: FinanceModule) => void;
  financeYear: number | null;
  setFinanceYear: (y: number | null) => void;
  applyUiActions: (actions: AgentUiAction[]) => void;
};

const Ctx = createContext<WorkspaceValue | null>(null);

export function WorkspaceProvider({
  children,
  onNavigateMain,
  pinSelection,
}: {
  children: ReactNode;
  onNavigateMain?: (tab: MainTab) => void;
  pinSelection?: (ctx: AuditSelection | null) => void;
}) {
  const queryClient = useQueryClient();
  const [mainTab, setMainTabState] = useState<MainTab>("finance");
  const [financeModule, setFinanceModule] = useState<FinanceModule>("income");
  const [financeYear, setFinanceYear] = useState<number | null>(null);

  const setMainTab = useCallback(
    (tab: MainTab) => {
      setMainTabState(tab);
      onNavigateMain?.(tab);
    },
    [onNavigateMain],
  );

  const applyUiActions = useCallback(
    (actions: AgentUiAction[]) => {
      for (const act of actions) {
        if (act.type === "navigate_main") {
          setMainTab(act.tab);
        } else if (act.type === "finance_module") {
          setFinanceModule(act.module);
          setMainTab("finance");
        } else if (act.type === "set_finance_year") {
          setFinanceYear(act.year);
        } else if (act.type === "pin_selection" && pinSelection) {
          pinSelection(act.context);
        } else if (act.type === "invalidate_queries") {
          queryClient.invalidateQueries({ queryKey: act.queryKey });
        } else if (act.type === "invalidate_project_analysis") {
          queryClient.invalidateQueries({
            predicate: (query) => query.queryKey.includes(act.project_id),
          });
        }
      }
    },
    [pinSelection, queryClient, setMainTab],
  );

  const value = useMemo(
    () => ({
      mainTab,
      setMainTab,
      financeModule,
      setFinanceModule,
      financeYear,
      setFinanceYear,
      applyUiActions,
    }),
    [mainTab, setMainTab, financeModule, financeYear, applyUiActions],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useWorkspace() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useWorkspace must be used within WorkspaceProvider");
  return v;
}
