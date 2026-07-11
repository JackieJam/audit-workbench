import { useEffect, useMemo, useState } from "react";
import { DetailTable } from "@/components/DetailTable";
import { SelectionBar } from "@/components/SelectionBar";

const DISPLAY_LIMIT = 100;

type Props = {
  label: string;
  rows: Record<string, unknown>[];
  loading?: boolean;
  onClear: () => void;
  onAdd: (voucherIds: string[]) => void;
  adding?: boolean;
  added?: boolean;
};

function voucherIdsFromSelection(rows: Record<string, unknown>[], indices: Set<number>): string[] {
  const ids = new Set<string>();
  for (const i of indices) {
    const vid = String(rows[i]?.["凭证编号"] ?? "").trim();
    if (vid) ids.add(vid);
  }
  return [...ids];
}

export function DrilldownPanel({ label, rows, loading, onClear, onAdd, adding, added }: Props) {
  const [selected, setSelected] = useState<Set<number>>(() => new Set());

  const visibleRows = useMemo(() => rows.slice(0, DISPLAY_LIMIT), [rows]);

  useEffect(() => {
    setSelected(new Set());
  }, [rows]);

  const selectedVouchers = voucherIdsFromSelection(visibleRows, selected);

  const handleClear = () => {
    setSelected(new Set());
    onClear();
  };

  return (
    <section className="drilldown-panel">
      <SelectionBar
        label={label}
        stats={
          rows.length
            ? {
                rows: rows.length,
                vouchers: new Set(rows.map((r) => String(r["凭证编号"] ?? "")).filter(Boolean)).size,
              }
            : undefined
        }
        selectedRows={selected.size}
        selectedVouchers={selectedVouchers.length}
        onClear={handleClear}
        onAdd={() => onAdd(selectedVouchers)}
        adding={adding}
        added={added}
        addDisabled={selected.size === 0}
      />
      <DetailTable
        rows={visibleRows}
        totalRows={rows.length}
        loading={loading}
        selectable
        selected={selected}
        onSelectedChange={setSelected}
      />
    </section>
  );
}
