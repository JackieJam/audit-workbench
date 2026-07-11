type Props = {
  label: string;
  stats?: { rows: number; vouchers?: number };
  selectedRows?: number;
  selectedVouchers?: number;
  onClear: () => void;
  onAdd: () => void;
  adding?: boolean;
  added?: boolean;
  addDisabled?: boolean;
};

export function SelectionBar({
  label,
  stats,
  selectedRows = 0,
  selectedVouchers = 0,
  onClear,
  onAdd,
  adding,
  added,
  addDisabled,
}: Props) {
  return (
    <div className="selection-bar">
      <div className="selection-bar__info">
        <span className="selection-bar__label">{label}</span>
        {stats && (
          <span className="muted">
            {stats.rows.toLocaleString()} 行
            {stats.vouchers != null ? ` · ${stats.vouchers} 凭证` : ""}
          </span>
        )}
        {selectedRows > 0 && (
          <span className="selection-bar__picked">
            已选 {selectedRows} 行 · {selectedVouchers} 凭证
          </span>
        )}
      </div>
      <div className="selection-bar__actions">
        <button type="button" className="btn-ghost" onClick={onClear}>
          清除选择
        </button>
        <button
          type="button"
          className="btn-primary"
          onClick={onAdd}
          disabled={adding || added || addDisabled}
          title={addDisabled ? "请先勾选要入库的分录" : undefined}
        >
          {added ? "已加入疑点库" : adding ? "加入中…" : "加入疑点库"}
        </button>
      </div>
    </div>
  );
}
