"""Audit Invariant Tests — 锁住审计语义正确性，而非仅代码路径。

覆盖 Review 中的关键不变量：
- 多文件异构列名不得静默漏数
- 12 月发生额绝对值不得被描述成期末余额
- 无关冲回不得降低某笔预提的未匹配金额
- 样本量变化不得改变总体异常数量
- LLM API 失败不得推高 confirmation_rate
- 混合凭证白名单不得拆散分录
"""

from __future__ import annotations

import copy
from io import BytesIO

import pandas as pd
import pytest
from audit_engine.cross_year import (
    _accrual_reversal_pairs,
    _match_accrual_reversals,
    _yearend_balance_buildup,
)
from audit_engine.ingestion import load_files, resolve_file_mapping
from audit_engine.llm_verifier import (
    JUDGMENT_PENDING_REVIEW,
    LLMJudgment,
    _fallback_judgments_for_hits,
    confirmation_rate,
    summarize_judgments,
)
from audit_engine.reporter import excel_safe_value, generate_report_bytes
from audit_engine.rule_engine import RuleHit, apply_whitelist, run_all_rules
from audit_engine.rules_config import default_rules_config
from openpyxl import Workbook


def _xlsx(rows: list[dict], name: str = "f.xlsx") -> BytesIO:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    headers = list(rows[0].keys())
    ws.append(headers)
    for row in rows:
        ws.append([row[h] for h in headers])
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    buf.name = name  # type: ignore[attr-defined]
    return buf


# ── P0-1: 多文件列映射 ──────────────────────────────────────


def test_heterogeneous_file_columns_do_not_lose_amounts() -> None:
    """2024 叫「金额」、2025 叫「凭证金额」→ 合并后两年金额均不得丢失。"""
    f2024 = _xlsx(
        [{
            "凭证编号": "A1",
            "过账日期": "2024-06-15",
            "金额": 100_000,
            "借/贷标识": "S",
            "总账科目": "1122",
            "文本": "应收",
        }],
        "2024.xlsx",
    )
    f2025 = _xlsx(
        [{
            "凭证编号": "B1",
            "过账日期": "2025-06-15",
            "凭证金额": 200_000,
            "借/贷标识": "S",
            "总账科目": "1122",
            "文本": "应收",
        }],
        "2025.xlsx",
    )
    # 模拟 UI 只确认了「金额」映射（来自并集探测的偏好）
    preferred = {
        "凭证编号": "凭证编号",
        "过账日期": "过账日期",
        "凭证货币价值": "金额",
        "借/贷标识": "借/贷标识",
        "总账科目": "总账科目",
        "文本": "文本",
    }
    df, year_map, _missing = load_files([f2024, f2025], column_mapping=preferred)
    assert 2024 in year_map and 2025 in year_map
    assert float(pd.to_numeric(year_map[2024]["凭证货币价值"], errors="coerce").sum()) == 100_000
    assert float(pd.to_numeric(year_map[2025]["凭证货币价值"], errors="coerce").sum()) == 200_000
    assert "_source_file" in df.columns and "_source_sheet" in df.columns
    assert set(df["_source_file"].astype(str)) == {"2024.xlsx", "2025.xlsx"}


def test_resolve_file_mapping_falls_back_per_file() -> None:
    preferred = {"凭证货币价值": "金额"}
    resolved = resolve_file_mapping(
        ["凭证编号", "过账日期", "凭证金额", "借/贷标识"],
        preferred_mapping=preferred,
    )
    assert resolved["凭证货币价值"] == "凭证金额"


def test_debit_credit_only_file_not_blocked_as_amount_gap() -> None:
    """仅有借方/贷方金额的文件应能合成，不得被金额缺口误拦。"""
    f = _xlsx(
        [{
            "凭证编号": "D1",
            "过账日期": "2024-03-01",
            "借方金额": 5000,
            "贷方金额": 0,
            "总账科目": "6601",
            "文本": "费用",
        }],
        "dc-only.xlsx",
    )
    df, year_map, _ = load_files([f])
    assert not df.empty
    assert 2024 in year_map
    assert float(pd.to_numeric(df["凭证货币价值"], errors="coerce").fillna(0).sum()) == 5000


def test_currency_code_column_not_flagged_as_amount_leftover() -> None:
    from audit_engine.ingestion import _detect_amount_mapping_gap, _ensure_amount_ready

    df = pd.DataFrame({
        "凭证编号": ["C1"],
        "过账日期": pd.Timestamp("2024-01-01"),
        "凭证货币代码": [840],
        "借/贷标识": ["S"],
        "总账科目": ["6601"],
        "文本": ["x"],
    })
    assert _detect_amount_mapping_gap(_ensure_amount_ready(df), "c.xlsx") is None


# ── P0-2: 期末余额 ≠ 12 月绝对发生额 ────────────────────────


def _ar_year_df(year: int, debit: float, credit: float = 0.0, month: int = 6) -> pd.DataFrame:
    rows = []
    if debit:
        rows.append({
            "凭证编号": f"{year}-AR-S",
            "过账日期": pd.Timestamp(f"{year}-{month:02d}-15"),
            "总账科目": "1122010001",
            "总账科目：长文本": "应收账款",
            "借/贷标识": "S",
            "凭证货币价值": debit,
            "文本": "应收增加",
        })
    if credit:
        rows.append({
            "凭证编号": f"{year}-AR-H",
            "过账日期": pd.Timestamp(f"{year}-12-20"),
            "总账科目": "1122010001",
            "总账科目：长文本": "应收账款",
            "借/贷标识": "H",
            "凭证货币价值": credit,
            "文本": "回款",
        })
    return pd.DataFrame(rows)


def test_balance_buildup_uses_cumulative_net_not_december_abs() -> None:
    """12 月借贷对冲 1 亿不应被描述成年末余额 2 亿。"""
    # 全年净发生 = 0（借 1 亿 + 贷 1 亿），但 12 月 abs = 2 亿
    year_map = {
        2023: _ar_year_df(2023, debit=100_000_000.0, credit=100_000_000.0),
        2024: _ar_year_df(2024, debit=100_000_000.0, credit=100_000_000.0),
    }
    findings = _yearend_balance_buildup(year_map, growth_ratio=1.5)
    # 累计净发生额始终为 0，不得触发“持续累积”
    assert not any("累积" in f.category for f in findings)

    # 真正净增场景应触发，且证据是累计净额
    growing = {
        2022: _ar_year_df(2022, debit=1_000_000.0),
        2023: _ar_year_df(2023, debit=1_000_000.0),
    }
    findings2 = _yearend_balance_buildup(growing, growth_ratio=1.5)
    assert any(f.category == "应收累计净发生持续累积" for f in findings2)
    finding = next(f for f in findings2 if f.category == "应收累计净发生持续累积")
    assert "余额" not in finding.description or "近似" in finding.description
    assert finding.evidence[2022] == 1_000_000.0
    assert finding.evidence[2023] == 2_000_000.0
    assert finding.evidence.get("metric") == "cumulative_net_occurrence"


# ── P0-3: 预提逐笔配对 ──────────────────────────────────────


def test_unrelated_reversal_does_not_cover_accrual() -> None:
    """不同对手方冲回（即便金额相同）不得掩盖悬空预提。"""
    accruals = pd.DataFrame([
        {
            "凭证编号": "ACC-A",
            "过账日期": pd.Timestamp("2025-12-20"),
            "文本": "预提运费",
            "总账科目": "2202010001",
            "借/贷标识": "H",
            "供应商编号": "A",
            "凭证货币价值": 1_000_000.0,
        },
        {
            "凭证编号": "ACC-B",
            "过账日期": pd.Timestamp("2025-12-21"),
            "文本": "预提咨询费",
            "总账科目": "2202010001",
            "借/贷标识": "H",
            "供应商编号": "B",
            "凭证货币价值": 1_000_000.0,
        },
    ])
    # 同金额、不同对手方——旧总额覆盖算法会误判 100% 覆盖
    unrelated = pd.DataFrame([
        {
            "凭证编号": "REV-C",
            "过账日期": pd.Timestamp("2026-01-10"),
            "文本": "冲回其他项目",
            "总账科目": "2202010001",
            "借/贷标识": "S",
            "供应商编号": "C",
            "凭证货币价值": 1_000_000.0,
        },
        {
            "凭证编号": "REV-D",
            "过账日期": pd.Timestamp("2026-01-11"),
            "文本": "冲回其他项目",
            "总账科目": "2202010001",
            "借/贷标识": "S",
            "供应商编号": "D",
            "凭证货币价值": 1_000_000.0,
        },
    ])
    pairs, unmatched = _match_accrual_reversals(accruals, unrelated, amount_tolerance=0.05)
    assert pairs == []
    assert len(unmatched) == 2
    unmatched_before = float(unmatched["凭证货币价值"].abs().sum())

    extra = unrelated.copy()
    extra["凭证编号"] = ["REV-C2", "REV-D2"]
    more_unrelated = pd.concat([unrelated, extra], ignore_index=True)
    _pairs2, unmatched2 = _match_accrual_reversals(accruals, more_unrelated, amount_tolerance=0.05)
    unmatched_after = float(unmatched2["凭证货币价值"].abs().sum())
    assert unmatched_after >= unmatched_before


def test_same_party_wrong_amount_does_not_block_correct_party_match() -> None:
    """同对手方错误金额不应挡住另一笔正确金额的同对手方配对。"""
    accruals = pd.DataFrame([{
        "凭证编号": "ACC-1",
        "过账日期": pd.Timestamp("2025-12-20"),
        "文本": "预提运费",
        "总账科目": "2202010001",
        "借/贷标识": "H",
        "供应商编号": "A",
        "凭证货币价值": 1_000_000.0,
    }])
    reversals = pd.DataFrame([
        {
            "凭证编号": "REV-WRONG",
            "过账日期": pd.Timestamp("2026-01-05"),
            "文本": "冲回预提",
            "总账科目": "2202010001",
            "借/贷标识": "S",
            "供应商编号": "A",
            "凭证货币价值": 100_000.0,  # 差太远
        },
        {
            "凭证编号": "REV-OK",
            "过账日期": pd.Timestamp("2026-01-08"),
            "文本": "冲回预提",
            "总账科目": "2202010001",
            "借/贷标识": "S",
            "供应商编号": "A",
            "凭证货币价值": 1_000_000.0,
        },
    ])
    pairs, unmatched = _match_accrual_reversals(accruals, reversals, amount_tolerance=0.05)
    assert len(pairs) == 1
    assert pairs[0]["reversal_voucher"] == "REV-OK"
    assert unmatched.empty


def test_matched_same_party_accrual_not_flagged_as_dangling() -> None:
    year_map = {
        2025: pd.DataFrame([{
            "凭证编号": "ACC-1",
            "过账日期": pd.Timestamp("2025-12-20"),
            "文本": "年末预提费用",
            "总账科目": "2202010001",
            "借/贷标识": "H",
            "供应商编号": "V1",
            "凭证货币价值": 1_000_000.0,
            "凭证类型": "SA",
        }]),
        2026: pd.DataFrame([{
            "凭证编号": "REV-1",
            "过账日期": pd.Timestamp("2026-01-15"),
            "文本": "冲销预提",
            "总账科目": "2202010001",
            "借/贷标识": "S",
            "供应商编号": "V1",
            "凭证货币价值": 1_000_000.0,
            "凭证类型": "SA",
        }]),
    }
    findings = _accrual_reversal_pairs(year_map)
    assert not any(f.category == "预提冲回配对" for f in findings)


# ── P0-4: Population / Sampling 解耦 ─────────────────────────


def test_max_sample_size_does_not_truncate_rule_population() -> None:
    """改变样本量不得改变 run_all_rules 返回的总体异常数量。"""
    rows = []
    for i in range(30):
        rows.append({
            "凭证编号": f"R{i:03d}",
            "过账日期": pd.Timestamp("2024-12-15"),
            "凭证类型": "SA",
            "总账科目": "6602010001",
            "总账科目：长文本": "管理费用-咨询费",
            "借/贷标识": "S",
            "凭证货币价值": 2_000_000.0,  # 大额整数
            "文本": f"咨询费{i}",
            "供应商编号": f"V{i}",
            "客户": "",
            "用户名": "u1",
            "公司代码": "1000",
            "公司代码货币价值": 2_000_000.0,
        })
    df = pd.DataFrame(rows)
    cfg = copy.deepcopy(default_rules_config())
    cfg["large_amount"] = {
        **cfg.get("large_amount", {}),
        "enabled": True,
        "round_number_threshold": 1_000_000,
    }
    # 关闭其他*规则*，聚焦大额；勿动 routine_exclusion 等元配置
    rule_keys = {
        k for k, v in cfg.items()
        if isinstance(v, dict) and "enabled" in v and k not in {
            "routine_exclusion", "cross_year_detection", "sampling",
        }
    }
    for key in rule_keys:
        if key != "large_amount":
            cfg[key]["enabled"] = False

    results_50 = run_all_rules(df, {**cfg, "max_sample_size": 50})
    results_5 = run_all_rules(df, {**cfg, "max_sample_size": 5})
    pop_50 = sum(len(r.hits) for r in results_50)
    pop_5 = sum(len(r.hits) for r in results_5)
    assert pop_50 == pop_5
    assert pop_50 >= 30

    # 导出层才截断
    _, stats = generate_report_bytes(df, results_50, max_sample_size=5, rules_config=cfg)
    assert stats["sample_vouchers"] <= 5
    assert pop_50 > stats["sample_vouchers"]


# ── P0/P1-5: LLM fallback 三态 ───────────────────────────────


def test_llm_fallback_does_not_raise_confirmation_rate() -> None:
    hit = RuleHit(
        voucher_id="V1",
        rule_type="大额整数",
        evidence="金额达到阈值",
        line_indices=(0,),
        priority=3,
    )
    before = {"规则": []}
    rate_before = summarize_judgments(before).get("confirmation_rate")
    assert rate_before is None

    fallback = _fallback_judgments_for_hits([hit], "LLM 调用失败")
    assert fallback[0].status == JUDGMENT_PENDING_REVIEW
    assert fallback[0].confirmed is False

    after = {"规则": fallback}
    summary = summarize_judgments(after)
    assert summary["confirmed"] == 0
    assert summary["pending_review"] == 1
    # 无已决样本时确认率为 None（不是 0，更不是上升）
    assert summary["confirmation_rate"] is None
    assert confirmation_rate(fallback) is None

    # 真正确认才会上升
    confirmed = [LLMJudgment("V1", True, "高", "ok", "查", "llm")]
    assert confirmation_rate(confirmed) == 1.0


# ── P1: 白名单不得破坏凭证完整性 ─────────────────────────────


def test_whitelist_keeps_mixed_voucher_intact() -> None:
    df = pd.DataFrame([
        {
            "凭证编号": "MIX1",
            "凭证类型": "SA",
            "凭证货币价值": 10_000.0,
            "文本": "202401计提人工费",
            "总账科目：长文本": "费用-人工-社会保险费-基本养老保险",
            "总账科目": "6910010701",
            "借/贷标识": "S",
            "过账日期": pd.Timestamp("2024-01-15"),
        },
        {
            "凭证编号": "MIX1",
            "凭证类型": "SA",
            "凭证货币价值": 500_000.0,
            "文本": "应付高管奖金",
            "总账科目：长文本": "应付职工薪酬-奖金",
            "总账科目": "2211010001",
            "借/贷标识": "H",
            "过账日期": pd.Timestamp("2024-01-15"),
        },
        {
            "凭证编号": "PURE",
            "凭证类型": "SA",
            "凭证货币价值": 8_000.0,
            "文本": "202401计提人工费",
            "总账科目：长文本": "费用-人工-社会保险费-医疗保险",
            "总账科目": "6910010702",
            "借/贷标识": "S",
            "过账日期": pd.Timestamp("2024-01-16"),
        },
    ])
    kept, excluded = apply_whitelist(df, default_rules_config())
    # 混合凭证两行都在
    mix_rows = kept[kept["凭证编号"].astype(str) == "MIX1"]
    assert len(mix_rows) == 2
    # 纯常规凭证被整单排除
    assert "PURE" in set(excluded["凭证编号"].astype(str))


# ── P1: Excel 公式注入 ───────────────────────────────────────


@pytest.mark.parametrize("raw", ["=HYPERLINK(\"http://x\")", "+1234", "-cmd", "@SUM(A1)"])
def test_excel_safe_string_neutralizes_formula_prefixes(raw: str) -> None:
    safe = excel_safe_value(raw)
    assert isinstance(safe, str)
    assert safe.startswith("'")


def test_burst_multiplier_changes_splitting_results() -> None:
    """UI 可编辑的 burst_multiplier 必须真实改变同日拆分命中。"""
    from audit_engine.rule_engine import rule_splitting

    # 供应商日常每天 2 笔；某一天突然 6 笔相似金额
    rows = []
    for day, n in [("2024-01-01", 2), ("2024-01-02", 2), ("2024-01-03", 6)]:
        for i in range(n):
            rows.append({
                "供应商编号": "V1",
                "过账日期": pd.Timestamp(day),
                "凭证货币价值": 100.0 + i,
                "凭证编号": f"{day}-{i}",
            })
    frame = pd.DataFrame(rows)
    base = {
        "enabled": True,
        "max_single_amount": 200,
        "min_total": 400,
        "window_days": 14,
        "min_txn_count": 5,
    }
    hits_strict = rule_splitting(frame, {"splitting": {**base, "burst_multiplier": 10.0}})
    hits_loose = rule_splitting(frame, {"splitting": {**base, "burst_multiplier": 2.0}})
    # 日均其余日=2，突发日=6 → 倍数=3；阈值 10 不命中同日，阈值 2 命中
    assert not any(h.rule_type == "化整为零(同日拆分)" for h in hits_strict.hits)
    assert any(h.rule_type == "化整为零(同日拆分)" for h in hits_loose.hits)
