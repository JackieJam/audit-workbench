import type { AuditSelection, FinanceModuleId } from "@/api/client";

export function moduleOverviewSelection(
  module: FinanceModuleId,
  label: string,
  year?: number,
): AuditSelection {
  return {
    label: year ? `${label} · ${year}年` : label,
    source_module: label,
    source_view: "模块概览",
    selector: {
      kind: "module_overview",
      module,
      ...(year ? { year } : {}),
    },
    summary: year ? { year } : undefined,
  };
}
