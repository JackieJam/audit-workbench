type Props = {
  rows: Record<string, unknown>[];
  totalRows?: number;
  loading?: boolean;
  selectable?: boolean;
  selected?: Set<number>;
  onSelectedChange?: (next: Set<number>) => void;
};

const DISPLAY_COLS = [
  "过账日期",
  "凭证编号",
  "科目名称",
  "借/贷标识",
  "收入影响",
  "成本发生额",
  "毛利影响",
  "凭证货币价值",
  "客户",
  "摘要",
];

function formatCell(key: string, value: unknown): string {
  if (value == null || value === "") return "—";
  if (typeof value === "number") {
    return key.includes("影响") || key.includes("价值")
      ? value.toLocaleString("zh-CN", { maximumFractionDigits: 2 })
      : String(value);
  }
  return String(value);
}

export function DetailTable({
  rows,
  totalRows,
  loading,
  selectable,
  selected,
  onSelectedChange,
}: Props) {
  if (loading) {
    return <p className="muted">加载分录明细…</p>;
  }
  if (!rows.length) {
    return <p className="muted">无匹配分录。</p>;
  }

  const cols = DISPLAY_COLS.filter((c) => rows.some((r) => c in r && r[c] != null));
  const sel = selected ?? new Set<number>();
  const allChecked = rows.length > 0 && rows.every((_, i) => sel.has(i));
  const someChecked = rows.some((_, i) => sel.has(i));

  const toggleRow = (index: number) => {
    if (!onSelectedChange) return;
    const next = new Set(sel);
    if (next.has(index)) next.delete(index);
    else next.add(index);
    onSelectedChange(next);
  };

  const toggleAll = () => {
    if (!onSelectedChange) return;
    if (allChecked) onSelectedChange(new Set());
    else onSelectedChange(new Set(rows.map((_, i) => i)));
  };

  const shownTotal = totalRows ?? rows.length;

  return (
    <div className="detail-table-wrap">
      <table className="detail-table">
        <thead>
          <tr>
            {selectable && (
              <th className="detail-table__check">
                <input
                  type="checkbox"
                  aria-label="全选当前页"
                  checked={allChecked}
                  ref={(el) => {
                    if (el) el.indeterminate = someChecked && !allChecked;
                  }}
                  onChange={toggleAll}
                />
              </th>
            )}
            {cols.map((c) => (
              <th key={c}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className={sel.has(i) ? "detail-table__row--selected" : undefined}>
              {selectable && (
                <td className="detail-table__check">
                  <input
                    type="checkbox"
                    aria-label={`选择第 ${i + 1} 行`}
                    checked={sel.has(i)}
                    onChange={() => toggleRow(i)}
                  />
                </td>
              )}
              {cols.map((c) => (
                <td key={c}>{formatCell(c, row[c])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {shownTotal > rows.length && (
        <p className="muted detail-table-more">
          仅展示前 {rows.length} 行，共 {shownTotal} 行；勾选仅作用于当前展示行
        </p>
      )}
    </div>
  );
}
