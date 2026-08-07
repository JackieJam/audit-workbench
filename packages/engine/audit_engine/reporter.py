"""
Excel 输出模块：生成审计抽样报告。
修复原版问题：
- 按凭证合并（不再每行重复 LLM 判断）
- 严格按 max_sample_size 截断
- 规则类型去重（同凭证命中多规则时合并展示）
- LLM 判断为空的凭证不出现在样本清单
"""

from __future__ import annotations

from typing import Any

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from audit_engine.data_columns import VOUCHER_KEY_COLUMN, ensure_voucher_identity
from audit_engine.rule_engine import RuleHit, RuleResult

HEADER_FONT = Font(bold=True, size=10, color="FFFFFF")
HEADER_FILL = PatternFill(start_color="2F5496", end_color="2F5496", fill_type="solid")
HIGH_FILL = PatternFill(start_color="FCE4EC", end_color="FCE4EC", fill_type="solid")
MED_FILL = PatternFill(start_color="FFF8E1", end_color="FFF8E1", fill_type="solid")
THIN_BORDER = Border(
    left=Side(style="thin"), right=Side(style="thin"),
    top=Side(style="thin"), bottom=Side(style="thin"),
)
WRAP = Alignment(wrap_text=True, vertical="top")
CENTER = Alignment(horizontal="center", vertical="center")

SAMPLE_COLS = [
    ("序号", 6), ("凭证编号", 14), ("组合ID", 24), ("关联凭证", 28), ("年份", 8), ("过账日期", 12), ("凭证类型", 10),
    ("总账科目", 12), ("总账科目名称", 22), ("借/贷", 8), ("金额", 16),
    ("文本", 30), ("供应商", 22), ("客户", 22), ("用户名", 12),
    ("规则类型", 20), ("触发证据", 35), ("组合证据", 50),
    ("LLM风险级别", 12), ("LLM判断理由", 35), ("建议核查程序", 35),
    ("核查结论", 20),  # 留给审计师填写
]

_FORMULA_PREFIXES = ("=", "+", "-", "@")


def excel_safe_value(value: Any) -> Any:
    """防止 spreadsheet formula injection：对危险前缀文本加撇号转义。"""
    if not isinstance(value, str) or not value:
        return value
    check = value.lstrip(" \t\r\n")
    if check and check[0] in _FORMULA_PREFIXES:
        return "'" + check
    return value


def generate_report_bytes(
    df: pd.DataFrame,
    rule_results: list[RuleResult],
    llm_judgments: dict[str, list[Any]] | None = None,
    max_sample_size: int = 50,
    manual_final_samples: list[dict] | None = None,
    explicit_samples: list[dict] | None = None,
    rules_config: dict | None = None,
) -> tuple[bytes, dict]:
    """生成 Excel 报告字节流，返回 (bytes, stats)。"""
    import tempfile
    from pathlib import Path

    llm_judgments = llm_judgments or {}
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "report.xlsx")
        stats = generate_report(
            df, rule_results, path, llm_judgments,
            max_sample_size=max_sample_size,
            manual_final_samples=manual_final_samples,
            explicit_samples=explicit_samples,
            rules_config=rules_config,
        )
        data = Path(path).read_bytes()
    return data, stats


def generate_report(
    df: pd.DataFrame,
    rule_results: list[RuleResult],
    output_path: str,
    llm_judgments: dict[str, list[Any]] | None = None,
    max_sample_size: int = 50,
    manual_final_samples: list[dict] | None = None,
    explicit_samples: list[dict] | None = None,
    rules_config: dict | None = None,
) -> dict:
    """
    生成 Excel 报告，返回统计摘要 dict。
    """
    from audit_engine.routine_filter import prioritize_voucher_ids_for_export

    # ── 构建凭证维度的合并视图 ──
    work = ensure_voucher_identity(df)
    judgment_lookup = _build_judgment_lookup(llm_judgments)
    hit_lookup = _build_hit_lookup(rule_results, work)
    hit_vids = set(hit_lookup.keys())

    if explicit_samples is not None:
        confirmed_vids = prioritize_voucher_ids_for_export(
            _sample_identity_tokens(explicit_samples, work),
            hit_voucher_ids=hit_vids,
            max_size=max_sample_size,
        )
    elif judgment_lookup:
        # 有 LLM 核实：按风险级别排序，取 top N（lookup 键已是 voucher_key）
        confirmed_primary_keys = sorted(
            judgment_lookup.keys(),
            key=lambda v: judgment_lookup[v].risk_level == "高",
            reverse=True,
        )
        available_keys = set(work[VOUCHER_KEY_COLUMN].dropna().astype(str)) if VOUCHER_KEY_COLUMN in work.columns else set()
        confirmed_vids = []
        for primary_key in confirmed_primary_keys:
            sample_vids: list[str] = []
            if primary_key in available_keys:
                sample_vids.append(primary_key)
            else:
                # 兼容旧 judgment 仅有展示编号的情况
                judgment = judgment_lookup[primary_key]
                sample_vids.extend(
                    _resolve_display_keys(
                        work,
                        getattr(judgment, "voucher_id", primary_key),
                        year=getattr(judgment, "fiscal_year", None) or None,
                        company=getattr(judgment, "company_code", None) or None,
                    )
                )
            for key in list(sample_vids):
                for hit in hit_lookup.get(key, []):
                    sample_vids.extend(_hit_voucher_keys(hit, work))
            for sample_vid in dict.fromkeys(sample_vids):
                if sample_vid and sample_vid not in confirmed_vids:
                    confirmed_vids.append(sample_vid)
                if len(confirmed_vids) >= max_sample_size:
                    break
            if len(confirmed_vids) >= max_sample_size:
                break
    else:
        # 无 LLM：按规则命中优先级排序，取 top N 凭证
        voucher_priority: dict[str, int] = {}
        for rr in rule_results:
            for hit in rr.hits:
                for vid in _hit_voucher_keys(hit, work):
                    voucher_priority[vid] = max(voucher_priority.get(vid, 0), hit.priority)
        confirmed_vids = sorted(
            voucher_priority.keys(),
            key=lambda v: voucher_priority[v],
            reverse=True,
        )[:max_sample_size]

    wb = Workbook()
    _write_sample_sheet(
        wb, work, confirmed_vids, hit_lookup, judgment_lookup, rules_config=rules_config,
    )
    if manual_final_samples:
        _write_manual_final_sheet(wb, work, manual_final_samples)
    _write_stats_sheet(wb, rule_results, llm_judgments)
    _write_rules_sheet(wb, rule_results)

    wb.save(output_path)

    total_unique_vouchers = len({
        vid
        for rr in rule_results
        for h in rr.hits
        for vid in _hit_voucher_keys(h, work)
    })
    return {
        "output_path": output_path,
        "sample_vouchers": len(confirmed_vids),
        "total_rule_hits": sum(r.count for r in rule_results),
        "total_unique_vouchers": total_unique_vouchers,
        "llm_confirmed": len(judgment_lookup),
        "high_risk": sum(1 for j in judgment_lookup.values() if j.risk_level == "高") if judgment_lookup else 0,
        "medium_risk": sum(1 for j in judgment_lookup.values() if j.risk_level == "中") if judgment_lookup else 0,
        "manual_final_vouchers": len({
            str(vid)
            for group in manual_final_samples or []
            for vid in (group.get("voucher_keys") or group.get("voucher_ids", []))
        }),
    }


def _build_judgment_lookup(llm_judgments: dict) -> dict[str, Any]:
    """仅纳入 status=confirmed；pending/rejected/fallback 不进样本主表。

    查找键优先 voucher_key（稳定身份），避免同号凭证跨年/跨公司扩散。
    """
    lookup: dict = {}
    for judgments in llm_judgments.values():
        for j in judgments:
            status = getattr(j, "status", "")
            if status != "confirmed":
                continue
            key = str(getattr(j, "voucher_key", "") or getattr(j, "voucher_id", "") or "").strip()
            if not key:
                continue
            if key not in lookup or j.risk_level == "高":
                lookup[key] = j
    return lookup


def _resolve_display_keys(
    df: pd.DataFrame,
    voucher_id: object,
    *,
    year: object = None,
    company: object = None,
) -> list[str]:
    display = str(voucher_id or "").strip()
    if not display or df.empty or "凭证编号" not in df.columns:
        return []
    rows = df[df["凭证编号"].astype(str).eq(display)]
    if year not in {None, ""} and not rows.empty:
        year_values = pd.Series(pd.NA, index=rows.index, dtype="Int64")
        for column in ("会计年度", "_year"):
            if column in rows.columns:
                year_values = year_values.fillna(
                    pd.to_numeric(rows[column], errors="coerce").astype("Int64")
                )
        if "过账日期" in rows.columns:
            year_values = year_values.fillna(
                pd.to_datetime(rows["过账日期"], errors="coerce").dt.year.astype("Int64")
            )
        rows = rows[year_values.eq(int(year))]
    company_text = str(company or "").strip()
    if company_text and "公司代码" in rows.columns:
        rows = rows[rows["公司代码"].astype(str).eq(company_text)]
    return list(dict.fromkeys(rows[VOUCHER_KEY_COLUMN].dropna().astype(str)))


def _sample_identity_tokens(
    samples: list[dict],
    df: pd.DataFrame,
) -> list[str]:
    available_keys = set(df[VOUCHER_KEY_COLUMN].dropna().astype(str))
    tokens: list[str] = []
    for sample in samples:
        key = str(
            sample.get("_voucher_key")
            or sample.get("凭证唯一键")
            or sample.get("凭证键")
            or ""
        ).strip()
        if key and key in available_keys:
            tokens.append(key)
            continue
        tokens.extend(
            _resolve_display_keys(
                df,
                sample.get("凭证编号"),
                year=sample.get("会计年度", sample.get("年份")),
                company=sample.get("公司代码"),
            )
        )
    return list(dict.fromkeys(tokens))


def _build_hit_lookup(
    rule_results: list[RuleResult],
    df: pd.DataFrame,
) -> dict[str, list[RuleHit]]:
    lookup: dict = {}
    for rr in rule_results:
        for hit in rr.hits:
            for vid in _hit_voucher_keys(hit, df):
                lookup.setdefault(vid, []).append(hit)
    return lookup


def _hit_voucher_keys(hit, df: pd.DataFrame) -> list[str]:
    keys: list[str] = []
    primary_key = str(getattr(hit, "voucher_key", "") or "").strip()
    if primary_key:
        keys.append(primary_key)
    else:
        keys.extend(
            _resolve_display_keys(
                df,
                getattr(hit, "voucher_id", ""),
                year=getattr(hit, "year", None),
            )
        )

    related_ids = list(getattr(hit, "related_voucher_ids", ()) or ())
    related_keys = list(getattr(hit, "related_voucher_keys", ()) or ())
    for index, related_id in enumerate(related_ids):
        related_key = (
            str(related_keys[index]).strip()
            if index < len(related_keys)
            else ""
        )
        if related_key:
            keys.append(related_key)
        else:
            keys.extend(_resolve_display_keys(df, related_id))
    return [key for key in dict.fromkeys(keys) if key]


def _write_sample_sheet(wb, df, confirmed_vids, hit_lookup, judgment_lookup, rules_config=None):
    from audit_engine.routine_filter import filter_export_voucher_rows

    ws = wb.active
    ws.title = "样本清单"
    voucher_rows_map = {
        str(vid): grp
        for vid, grp in df.groupby(VOUCHER_KEY_COLUMN, sort=False)
    }

    headers = [c for c, _ in SAMPLE_COLS]
    widths = [w for _, w in SAMPLE_COLS]

    for col_idx, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = CENTER
        cell.border = THIN_BORDER

    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    row_num = 2
    for seq, voucher_key in enumerate(confirmed_vids, 1):
        voucher_rows = voucher_rows_map.get(voucher_key)
        if voucher_rows is None:
            voucher_rows = df.iloc[0:0]
        hits = hit_lookup.get(voucher_key, [])
        display_id = (
            str(voucher_rows["凭证编号"].iloc[0])
            if "凭证编号" in voucher_rows.columns and not voucher_rows.empty
            else str(voucher_key)
        )
        judgment = judgment_lookup.get(display_id)
        voucher_rows = filter_export_voucher_rows(
            voucher_rows,
            rules_config,
            has_rule_hit=bool(hits),
        )

        rule_types = " | ".join(dict.fromkeys(h.rule_type for h in hits))
        evidences = " | ".join(dict.fromkeys(h.evidence for h in hits))
        group_ids = " | ".join(dict.fromkeys(h.group_id for h in hits if h.group_id))
        related_vouchers = " | ".join(dict.fromkeys(
            related for h in hits for related in h.related_voucher_ids
        ))
        relation_evidences = " | ".join(dict.fromkeys(
            h.relation_evidence for h in hits if h.relation_evidence
        ))
        if hits and hits[0].year:
            year = hits[0].year
        else:
            year = ""
            for year_column in ("会计年度", "_year"):
                if year_column not in voucher_rows.columns or voucher_rows.empty:
                    continue
                year_value = pd.to_numeric(
                    voucher_rows[year_column].iloc[0], errors="coerce"
                )
                if pd.notna(year_value):
                    year = int(year_value)
                    break

        for _, row in voucher_rows.iterrows():
            amt = row.get("_amount_raw", row.get("凭证货币价值"))
            values = [
                seq,
                display_id,
                group_ids,
                related_vouchers,
                year,
                row.get("过账日期"),
                row.get("凭证类型"),
                row.get("总账科目"),
                row.get("总账科目：长文本"),
                row.get("借/贷标识"),
                amt,
                str(row.get("文本", ""))[:80],
                row.get("供应商科目：名称 1"),
                row.get("客户科目：姓名 1"),
                row.get("用户名"),
                rule_types,
                evidences[:100],
                relation_evidences[:200],
                judgment.risk_level if judgment else "",
                judgment.reason if judgment else "",
                judgment.audit_procedures if judgment else "",
                "",  # 核查结论留白
            ]

            fill = HIGH_FILL if (judgment and judgment.risk_level == "高") else (
                MED_FILL if judgment else None
            )

            for col_idx, val in enumerate(values, 1):
                cell = ws.cell(row=row_num, column=col_idx, value=excel_safe_value(val))
                cell.border = THIN_BORDER
                cell.alignment = WRAP
                if fill:
                    cell.fill = fill

            row_num += 1

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(SAMPLE_COLS))}1"


def _write_manual_final_sheet(wb, df, manual_final_samples):
    ws = wb.create_sheet("人工直入样本")
    headers = [
        "疑点群体", "来源模块", "来源视图", "标签", "入样理由",
        "凭证编号", "年份", "过账日期", "凭证类型", "总账科目", "总账科目名称",
        "借/贷", "金额", "文本", "供应商", "客户", "用户名",
    ]
    widths = [28, 14, 20, 24, 36, 14, 8, 12, 10, 12, 22, 8, 16, 34, 22, 22, 12]

    for col_idx, (header, width) in enumerate(zip(headers, widths, strict=True), 1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = CENTER
        cell.border = THIN_BORDER
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    row_num = 2
    for group in manual_final_samples:
        voucher_keys = {
            str(value)
            for value in group.get("voucher_keys", [])
            if str(value).strip()
        }
        if not voucher_keys:
            for voucher_id in group.get("voucher_ids", []):
                voucher_keys.update(
                    _resolve_display_keys(
                        df,
                        voucher_id,
                        year=(group.get("selector") or {}).get("year"),
                    )
                )
        if not voucher_keys:
            continue
        rows = df[df[VOUCHER_KEY_COLUMN].astype(str).isin(voucher_keys)]
        for _, row in rows.iterrows():
            values = [
                group.get("title", ""),
                group.get("source_module", ""),
                group.get("source_view", ""),
                " | ".join(group.get("tags", [])),
                group.get("reason", ""),
                row.get("凭证编号"),
                int(row.get("_year")) if "_year" in row and row.get("_year") == row.get("_year") else "",
                row.get("过账日期"),
                row.get("凭证类型"),
                row.get("总账科目"),
                row.get("总账科目：长文本"),
                row.get("借/贷标识"),
                row.get("_amount_raw", row.get("凭证货币价值")),
                str(row.get("文本", ""))[:120],
                row.get("供应商科目：名称 1"),
                row.get("客户科目：姓名 1"),
                row.get("用户名"),
            ]
            for col_idx, value in enumerate(values, 1):
                cell = ws.cell(row=row_num, column=col_idx, value=excel_safe_value(value))
                cell.border = THIN_BORDER
                cell.alignment = WRAP
                cell.fill = MED_FILL
            row_num += 1

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}1"


def _write_stats_sheet(wb, rule_results, llm_judgments):
    from audit_engine.llm_verifier import JUDGMENT_CONFIRMED, confirmation_rate

    ws = wb.create_sheet("规则统计")
    headers = ["规则名称", "命中凭证数", "LLM确认数", "确认率", "待核验", "高风险", "中风险"]
    widths = [22, 14, 14, 10, 10, 10, 10]

    for col_idx, (h, w) in enumerate(zip(headers, widths, strict=True), 1):
        cell = ws.cell(row=1, column=col_idx, value=h)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = CENTER
        cell.border = THIN_BORDER
        ws.column_dimensions[get_column_letter(col_idx)].width = w

    total_hits = total_confirmed = total_pending = total_high = total_med = 0

    for row_idx, rr in enumerate(rule_results, 2):
        voucher_count = len({
            str(getattr(h, "voucher_key", "") or h.voucher_id)
            for h in rr.hits
        })
        judgments = llm_judgments.get(rr.rule_name, [])
        confirmed = sum(1 for j in judgments if getattr(j, "status", "") == JUDGMENT_CONFIRMED)
        pending = sum(1 for j in judgments if getattr(j, "status", "") == "pending_review")
        high = sum(
            1 for j in judgments
            if getattr(j, "status", "") == JUDGMENT_CONFIRMED and j.risk_level == "高"
        )
        med = sum(
            1 for j in judgments
            if getattr(j, "status", "") == JUDGMENT_CONFIRMED and j.risk_level == "中"
        )
        # 分母用模型已决数（确认+驳回），不用命中总体，避免把未核实混进确认率
        rate_value = confirmation_rate(judgments)
        rate = f"{rate_value:.0%}" if rate_value is not None else "N/A"

        for col_idx, val in enumerate(
            [rr.rule_name, voucher_count, confirmed, rate, pending, high, med], 1
        ):
            ws.cell(row=row_idx, column=col_idx, value=excel_safe_value(val)).border = THIN_BORDER

        total_hits += voucher_count
        total_confirmed += confirmed
        total_pending += pending
        total_high += high
        total_med += med

    # 合计确认率与单规则口径一致：confirmed / (confirmed + rejected)，pending 不进分母
    all_judgments = [j for items in llm_judgments.values() for j in items]
    total_rate_value = confirmation_rate(all_judgments)
    total_rate = f"{total_rate_value:.0%}" if total_rate_value is not None else "N/A"
    total_row = len(rule_results) + 2
    for col_idx, val in enumerate(
        ["合计", total_hits, total_confirmed, total_rate, total_pending, total_high, total_med], 1
    ):
        cell = ws.cell(row=total_row, column=col_idx, value=val)
        cell.font = Font(bold=True)
        cell.border = THIN_BORDER

    ws.freeze_panes = "A2"


def _write_rules_sheet(wb, rule_results):
    """规则命中明细：每条 RuleHit 一行，供追溯。"""
    ws = wb.create_sheet("命中明细")
    headers = [
        "规则名称", "规则类型", "凭证编号", "组合ID", "关联凭证", "优先级",
        "触发证据", "组合证据", "凭证唯一键", "关联凭证唯一键",
    ]
    widths = [18, 24, 14, 24, 28, 8, 60, 70, 40, 55]

    for col_idx, (h, w) in enumerate(zip(headers, widths, strict=True), 1):
        cell = ws.cell(row=1, column=col_idx, value=h)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = CENTER
        cell.border = THIN_BORDER
        ws.column_dimensions[get_column_letter(col_idx)].width = w

    row_num = 2
    for rr in rule_results:
        for hit in sorted(rr.hits, key=lambda h: h.priority, reverse=True):
            for col_idx, val in enumerate(
                [
                    rr.rule_name,
                    hit.rule_type,
                    hit.voucher_id,
                    hit.group_id or "",
                    " | ".join(hit.related_voucher_ids),
                    hit.priority,
                    hit.evidence,
                    hit.relation_evidence,
                    getattr(hit, "voucher_key", "") or "",
                    " | ".join(getattr(hit, "related_voucher_keys", ()) or ()),
                ],
                1,
            ):
                ws.cell(row=row_num, column=col_idx, value=excel_safe_value(val)).border = THIN_BORDER
            row_num += 1

    ws.freeze_panes = "A2"
