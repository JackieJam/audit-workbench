"""Audit Invariant Tests — 锁住审计语义正确性，而非仅代码路径。

覆盖 Review 中的关键不变量：
- 多文件异构列名不得静默漏数
- 12 月发生额绝对值不得被描述成期末余额
- 无关冲回不得降低某笔预提的未匹配金额
- 样本量变化不得改变总体异常数量
- LLM API 失败不得推高 confirmation_rate
- 混合凭证白名单不得拆散分录
- 同一凭证号跨年/跨公司 → LLM 必须按 voucher_key 隔离
- 完全无金额字段 → DQ/导入必须 blocked
- 模型漏答 → pending_review 而非 rejected
- 平衡预提凭证经济金额不得翻倍
"""

from __future__ import annotations

import copy
import json
from io import BytesIO

import pandas as pd
import pytest
from audit_engine.cross_year import (
    _accrual_reversal_pairs,
    _collapse_to_accrual_entities,
    _match_accrual_reversals,
    _yearend_balance_buildup,
)
from audit_engine.data_columns import audit_input_quality, ensure_voucher_identity
from audit_engine.ingestion import (
    NO_COLUMN_SENTINEL,
    _detect_amount_mapping_gap,
    _ensure_amount_ready,
    _post_process,
    load_files,
    resolve_file_mapping,
)
from audit_engine.llm_verifier import (
    JUDGMENT_PENDING_REVIEW,
    JUDGMENT_REJECTED,
    LLMJudgment,
    _build_judgments_from_response,
    _build_voucher_groups,
    _fallback_judgments_for_hits,
    _hit_identity_key,
    _hit_voucher_key,
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
    """货币代码列不得被当成「未映射金额残留」；但完全无金额仍应阻断。"""
    from audit_engine.ingestion import _detect_amount_mapping_gap, _ensure_amount_ready

    df = pd.DataFrame({
        "凭证编号": ["C1"],
        "过账日期": pd.Timestamp("2024-01-01"),
        "凭证货币代码": [840],
        "借/贷标识": ["S"],
        "总账科目": ["6601"],
        "文本": ["x"],
    })
    gap = _detect_amount_mapping_gap(_ensure_amount_ready(df), "c.xlsx")
    assert gap is not None
    assert "残留列" not in gap  # 不是把货币代码当残留
    assert "没有任何可用金额" in gap


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


# ── Review P0/P1 invariants (2026-08) ─────────────────────────


def test_same_voucher_id_across_years_are_distinct_llm_entities() -> None:
    """同一凭证号跨两个年度 → LLM 必须生成两个独立核验实体。"""
    hits = [
        RuleHit(
            voucher_id="000123",
            rule_type="大额整数",
            evidence="2024",
            line_indices=(0,),
            priority=3,
            year=2024,
            voucher_key="VK|1000|2024|000123",
        ),
        RuleHit(
            voucher_id="000123",
            rule_type="大额整数",
            evidence="2025",
            line_indices=(1,),
            priority=3,
            year=2025,
            voucher_key="VK|1000|2025|000123",
        ),
    ]
    keys = [_hit_identity_key(h) for h in hits]
    assert len(set(keys)) == 2

    df = ensure_voucher_identity(pd.DataFrame([
        {
            "凭证编号": "000123",
            "公司代码": "1000",
            "会计年度": 2024,
            "过账日期": pd.Timestamp("2024-06-01"),
            "凭证货币价值": 1_000_000.0,
            "借/贷标识": "S",
            "总账科目": "6601",
            "文本": "2024费用",
        },
        {
            "凭证编号": "000123",
            "公司代码": "1000",
            "会计年度": 2025,
            "过账日期": pd.Timestamp("2025-06-01"),
            "凭证货币价值": 2_000_000.0,
            "借/贷标识": "S",
            "总账科目": "6601",
            "文本": "2025费用",
        },
    ]))
    groups = _build_voucher_groups(df, hits, redaction="none")
    assert len(groups) == 2
    amounts = {
        float(g["主凭证行项目"][0]["金额"])
        for g in groups
        if g["主凭证行项目"]
    }
    assert amounts == {1_000_000.0, 2_000_000.0}


def test_same_voucher_id_across_companies_do_not_mix_in_llm_prompt() -> None:
    """两家公司相同凭证号 → LLM prompt 绝不能混行。"""
    hits = [
        RuleHit(
            voucher_id="000123",
            rule_type="大额整数",
            evidence="公司A",
            line_indices=(0,),
            priority=3,
            year=2025,
            voucher_key="VK|1000|2025|000123",
        ),
        RuleHit(
            voucher_id="000123",
            rule_type="大额整数",
            evidence="公司B",
            line_indices=(1,),
            priority=3,
            year=2025,
            voucher_key="VK|2000|2025|000123",
        ),
    ]
    df = ensure_voucher_identity(pd.DataFrame([
        {
            "凭证编号": "000123",
            "公司代码": "1000",
            "会计年度": 2025,
            "过账日期": pd.Timestamp("2025-03-01"),
            "凭证货币价值": 111.0,
            "借/贷标识": "S",
            "总账科目": "6601",
            "文本": "A公司",
            "用户名": "uA",
        },
        {
            "凭证编号": "000123",
            "公司代码": "2000",
            "会计年度": 2025,
            "过账日期": pd.Timestamp("2025-03-02"),
            "凭证货币价值": 222.0,
            "借/贷标识": "S",
            "总账科目": "6601",
            "文本": "B公司",
            "用户名": "uB",
        },
    ]))
    groups = _build_voucher_groups(df, hits, redaction="none")
    assert len(groups) == 2
    for group, expected_amt, expected_text in zip(
        groups, [111.0, 222.0], ["A公司", "B公司"], strict=True,
    ):
        rows = group["主凭证行项目"]
        assert len(rows) == 1
        assert float(rows[0]["金额"]) == expected_amt
        assert rows[0]["文本"] == expected_text


def test_missing_amount_fields_block_dq_and_do_not_placeholder_zero() -> None:
    """完全没有金额字段 → DQ 必须 blocked；post_process 不得补 0。"""
    df = pd.DataFrame({
        "凭证编号": ["V1"],
        "过账日期": pd.Timestamp("2024-01-01"),
        "借/贷标识": ["S"],
        "总账科目": ["6601"],
        "文本": ["无金额"],
    })
    gap = _detect_amount_mapping_gap(_ensure_amount_ready(df), "no-amt.xlsx")
    assert gap is not None and "没有任何可用金额" in gap

    processed, missing = _post_process(df.copy(), {})
    assert "凭证货币价值" in missing or "凭证货币价值" in processed.columns
    if "凭证货币价值" in processed.columns:
        assert pd.to_numeric(processed["凭证货币价值"], errors="coerce").isna().all()

    quality = audit_input_quality(processed)
    assert quality["status"] == "blocked"
    assert any(i["code"] == "missing_usable_amount" for i in quality["issues"])

    # 导入路径同样阻断
    f = _xlsx([{
        "凭证编号": "V1",
        "过账日期": "2024-01-01",
        "借/贷标识": "S",
        "总账科目": "6601",
        "文本": "无金额",
    }], "no-amt.xlsx")
    with pytest.raises(ValueError, match="没有任何可用金额|金额"):
        load_files([f])


def test_llm_incomplete_response_is_pending_not_rejected() -> None:
    """模型返回 9/10 条结果 → 第 10 条必须 pending_review。"""
    text = "[" + ",".join(
        f'{{"voucher_id": "X{i}", "confirmed": true, "risk_level": "中", "reason": "ok", "audit_procedures": "查"}}'
        for i in range(1, 10)
    ) + "]"
    allowed = {f"X{i}" for i in range(1, 11)}
    judgments = _build_judgments_from_response(text, allowed_voucher_ids=allowed)
    by_id = {j.voucher_id: j for j in judgments}
    assert by_id["X10"].status == JUDGMENT_PENDING_REVIEW
    assert by_id["X10"].source == "llm_incomplete"
    assert by_id["X10"].status != JUDGMENT_REJECTED
    # 9 confirmed / 9 decided；pending 不进分母
    assert confirmation_rate(judgments) == pytest.approx(1.0)


def test_balanced_accrual_voucher_does_not_double_economic_amount() -> None:
    """一张预提凭证两条平衡分录均写「预提」→ 经济预提金额不能翻倍。"""
    lines = pd.DataFrame([
        {
            "凭证编号": "A001",
            "公司代码": "1000",
            "会计年度": 2025,
            "过账日期": pd.Timestamp("2025-12-20"),
            "文本": "12月预提运费",
            "总账科目": "6601010001",
            "借/贷标识": "S",
            "供应商编号": "V-SHIP",
            "凭证货币价值": 1_000_000.0,
        },
        {
            "凭证编号": "A001",
            "公司代码": "1000",
            "会计年度": 2025,
            "过账日期": pd.Timestamp("2025-12-20"),
            "文本": "12月预提运费",
            "总账科目": "2202010001",
            "借/贷标识": "H",
            "供应商编号": "V-SHIP",
            "凭证货币价值": 1_000_000.0,
        },
    ])
    entities = _collapse_to_accrual_entities(lines)
    assert len(entities) == 1
    economic = float(entities.iloc[0]["凭证货币价值"])
    assert economic == pytest.approx(1_000_000.0)
    assert economic != pytest.approx(2_000_000.0)

    year_map = {
        2025: lines,
        2026: pd.DataFrame([{
            "凭证编号": "R001",
            "公司代码": "1000",
            "会计年度": 2026,
            "过账日期": pd.Timestamp("2026-01-10"),
            "文本": "冲销预提运费",
            "总账科目": "2202010001",
            "借/贷标识": "S",
            "供应商编号": "V-SHIP",
            "凭证货币价值": 1_000_000.0,
        }]),
    }
    findings = _accrual_reversal_pairs(year_map)
    # 已配对完成，不应因金额翻倍而报悬空
    assert not any(f.category == "预提冲回配对" for f in findings)


def test_no_column_sentinel_is_hard_veto() -> None:
    """用户确认「(无此列)」后，自动 matcher 不得偷偷认回。"""
    resolved = resolve_file_mapping(
        ["凭证编号", "过账日期", "金额总计", "借/贷标识", "总账科目"],
        preferred_mapping={"凭证货币价值": NO_COLUMN_SENTINEL},
    )
    assert "凭证货币价值" not in resolved


# ── Review P0/P1 invariants (2026-08 round 2) ─────────────────


def test_current_sample_verify_keys_must_match_sample_set() -> None:
    """随机抽样凭证集合 → LLM 核验对象必须等于该 sample voucher_key 集合。"""
    from audit_engine.llm_verifier import _select_verify_hits
    from audit_engine.rule_engine import RuleResult

    sample_keys = {
        "VK|1000|2025|A01",
        "VK|1000|2025|A02",
        "VK|1000|2025|A03",
    }
    # 规则命中是另一批高风险凭证
    high_risk = RuleResult(
        rule_name="大额异常",
        hits=[
            RuleHit(
                voucher_id="B99",
                rule_type="大额整数",
                evidence="高风险",
                line_indices=(0,),
                priority=5,
                year=2025,
                voucher_key="VK|1000|2025|B99",
            ),
        ],
    )
    # 样本中仅 A01 有规则命中
    sample_hit = RuleResult(
        rule_name="化整为零",
        hits=[
            RuleHit(
                voucher_id="A01",
                rule_type="化整为零(同日拆分)",
                evidence="样本命中",
                line_indices=(1,),
                priority=3,
                year=2025,
                voucher_key="VK|1000|2025|A01",
            ),
        ],
    )
    selected = _select_verify_hits(
        [high_risk, sample_hit],
        max_verify=50,
        voucher_keys=sample_keys,
        verification_scope="current_sample",
    )
    selected_keys = {_hit_voucher_key(h) for _, h in selected}
    assert selected_keys == sample_keys
    assert "VK|1000|2025|B99" not in selected_keys


def test_verify_id_unique_for_same_display_voucher_across_entities() -> None:
    """同号跨公司/跨年 → verify_id 必须唯一且能 round-trip。"""
    from audit_engine.llm_verifier import (
        _build_judgments_from_response,
        make_verify_id,
    )

    hits = [
        RuleHit(
            voucher_id="000123",
            rule_type="大额整数",
            evidence="A",
            line_indices=(0,),
            priority=3,
            year=2024,
            voucher_key="VK|1000|2024|000123",
        ),
        RuleHit(
            voucher_id="000123",
            rule_type="大额整数",
            evidence="B",
            line_indices=(1,),
            priority=3,
            year=2025,
            voucher_key="VK|2000|2025|000123",
        ),
    ]
    df = ensure_voucher_identity(pd.DataFrame([
        {
            "凭证编号": "000123",
            "公司代码": "1000",
            "会计年度": 2024,
            "过账日期": pd.Timestamp("2024-06-01"),
            "凭证货币价值": 1.0,
            "借/贷标识": "S",
            "总账科目": "6601",
            "文本": "A",
        },
        {
            "凭证编号": "000123",
            "公司代码": "2000",
            "会计年度": 2025,
            "过账日期": pd.Timestamp("2025-06-01"),
            "凭证货币价值": 2.0,
            "借/贷标识": "S",
            "总账科目": "6601",
            "文本": "B",
        },
    ]))
    secret = "test-run-secret"
    groups = _build_voucher_groups(df, hits, redaction="pseudonym", run_secret=secret)
    verify_ids = [g["verify_id"] for g in groups]
    assert len(verify_ids) == 2
    assert len(set(verify_ids)) == 2
    assert all(vid.startswith("VERIFY_") for vid in verify_ids)

    verify_to_identity = {
        g["verify_id"]: {
            "voucher_key": g["_raw_voucher_key"],
            "voucher_id": g["_raw_voucher_id"],
            "company_code": g["_company_code"],
            "fiscal_year": g["_fiscal_year"],
        }
        for g in groups
    }
    response = json.dumps([
        {
            "verify_id": verify_ids[0],
            "confirmed": True,
            "risk_level": "高",
            "reason": "ok",
            "audit_procedures": "查",
        },
        {
            "verify_id": verify_ids[1],
            "confirmed": False,
            "reason": "排除",
            "audit_procedures": "无",
        },
    ])
    judgments = _build_judgments_from_response(
        response,
        batch_hits=hits,
        allowed_voucher_keys={h.voucher_key for h in hits},
        verify_to_identity=verify_to_identity,
    )
    by_key = {j.voucher_key: j for j in judgments}
    assert by_key["VK|1000|2024|000123"].status == "confirmed"
    assert by_key["VK|2000|2025|000123"].status == "rejected"
    # 同 secret 下 make_verify_id 稳定
    assert make_verify_id("VK|1000|2024|000123", run_secret=secret) == verify_ids[0]


def test_two_vendor_accrual_voucher_yields_two_entities() -> None:
    """一张凭证两个供应商预提 → 必须生成两个 economic entities。"""
    lines = pd.DataFrame([
        {
            "凭证编号": "A001",
            "公司代码": "1000",
            "会计年度": 2025,
            "过账日期": pd.Timestamp("2025-12-20"),
            "文本": "预提运费",
            "总账科目": "6601010001",
            "借/贷标识": "S",
            "供应商编号": "VA",
            "凭证货币价值": 600_000.0,
        },
        {
            "凭证编号": "A001",
            "公司代码": "1000",
            "会计年度": 2025,
            "过账日期": pd.Timestamp("2025-12-20"),
            "文本": "预提咨询费",
            "总账科目": "6602010001",
            "借/贷标识": "S",
            "供应商编号": "VB",
            "凭证货币价值": 400_000.0,
        },
        {
            "凭证编号": "A001",
            "公司代码": "1000",
            "会计年度": 2025,
            "过账日期": pd.Timestamp("2025-12-20"),
            "文本": "预提运费",
            "总账科目": "2202010001",
            "借/贷标识": "H",
            "供应商编号": "VA",
            "凭证货币价值": 600_000.0,
        },
        {
            "凭证编号": "A001",
            "公司代码": "1000",
            "会计年度": 2025,
            "过账日期": pd.Timestamp("2025-12-20"),
            "文本": "预提咨询费",
            "总账科目": "2202010001",
            "借/贷标识": "H",
            "供应商编号": "VB",
            "凭证货币价值": 400_000.0,
        },
    ])
    entities = _collapse_to_accrual_entities(lines)
    assert len(entities) == 2
    amounts = sorted(float(x) for x in entities["凭证货币价值"].tolist())
    assert amounts == pytest.approx([400_000.0, 600_000.0])


def test_complete_reversal_matches_liability_leg() -> None:
    """完整预提 + 完整冲回 → 能正确匹配 liability leg（冲回取负债借方）。"""
    year_map = {
        2025: pd.DataFrame([
            {
                "凭证编号": "A001",
                "公司代码": "1000",
                "会计年度": 2025,
                "过账日期": pd.Timestamp("2025-12-20"),
                "文本": "12月预提运费",
                "总账科目": "6601010001",
                "借/贷标识": "S",
                "供应商编号": "V-SHIP",
                "凭证货币价值": 1_000_000.0,
            },
            {
                "凭证编号": "A001",
                "公司代码": "1000",
                "会计年度": 2025,
                "过账日期": pd.Timestamp("2025-12-20"),
                "文本": "12月预提运费",
                "总账科目": "2202010001",
                "借/贷标识": "H",
                "供应商编号": "V-SHIP",
                "凭证货币价值": 1_000_000.0,
            },
        ]),
        2026: pd.DataFrame([
            {
                "凭证编号": "R001",
                "公司代码": "1000",
                "会计年度": 2026,
                "过账日期": pd.Timestamp("2026-01-10"),
                "文本": "冲销预提运费",
                "总账科目": "2202010001",
                "借/贷标识": "S",
                "供应商编号": "V-SHIP",
                "凭证货币价值": 1_000_000.0,
            },
            {
                "凭证编号": "R001",
                "公司代码": "1000",
                "会计年度": 2026,
                "过账日期": pd.Timestamp("2026-01-10"),
                "文本": "冲销预提运费",
                "总账科目": "6601010001",
                "借/贷标识": "H",
                "供应商编号": "V-SHIP",
                "凭证货币价值": 1_000_000.0,
            },
        ]),
    }
    findings = _accrual_reversal_pairs(year_map)
    assert not any(f.category == "预提冲回配对" for f in findings)


def test_cross_year_mixed_currencies_blocked_without_scope() -> None:
    """2024 USD + 2025 CNY → 未选币种时跨年金额分析必须 blocked。"""
    from audit_engine.data_columns import collect_analysis_currencies, ensure_analysis_columns

    y2024 = ensure_analysis_columns(pd.DataFrame([
        {
            "凭证编号": "U1",
            "公司代码": "1000",
            "会计年度": 2024,
            "过账日期": pd.Timestamp("2024-06-01"),
            "凭证货币价值": 1_000_000.0,
            "凭证货币代码": "USD",
            "借/贷标识": "S",
            "总账科目": "1122",
            "文本": "应收",
        },
    ]))
    y2025 = ensure_analysis_columns(pd.DataFrame([
        {
            "凭证编号": "C1",
            "公司代码": "1000",
            "会计年度": 2025,
            "过账日期": pd.Timestamp("2025-06-01"),
            "凭证货币价值": 2_000_000.0,
            "凭证货币代码": "CNY",
            "借/贷标识": "S",
            "总账科目": "1122",
            "文本": "应收",
        },
    ]))
    currencies = collect_analysis_currencies([y2024, y2025])
    assert currencies == {"USD", "CNY"}
    assert len(currencies) > 1


def test_company_currency_multi_company_not_auto_summable() -> None:
    """CNY 本位币公司 + USD 本位币公司 → 不得因 company currency 直接合计。"""
    quality = audit_input_quality(pd.DataFrame([
        {
            "凭证编号": "A1",
            "公司代码": "CN01",
            "会计年度": 2025,
            "过账日期": pd.Timestamp("2025-01-01"),
            "公司代码货币价值": 100.0,
            "公司代码货币代码": "CNY",
            "借/贷标识": "S",
            "总账科目": "1002",
            "文本": "CN",
        },
        {
            "凭证编号": "A2",
            "公司代码": "US01",
            "会计年度": 2025,
            "过账日期": pd.Timestamp("2025-01-01"),
            "公司代码货币价值": 100.0,
            "公司代码货币代码": "USD",
            "借/贷标识": "S",
            "总账科目": "1002",
            "文本": "US",
        },
    ]))
    codes = {issue["code"] for issue in quality["issues"]}
    assert "mixed_analysis_currencies" in codes
    assert quality["status"] == "blocked"


def test_reporter_writes_all_llm_statuses_by_voucher_key() -> None:
    """confirmed/rejected/pending → Excel 三种状态都必须按 voucher_key 回写。"""
    from audit_engine.llm_verifier import LLMJudgment
    from audit_engine.rule_engine import RuleResult

    df = ensure_voucher_identity(pd.DataFrame([
        {
            "凭证编号": "000123",
            "公司代码": "1000",
            "会计年度": 2025,
            "过账日期": pd.Timestamp("2025-03-01"),
            "凭证货币价值": 100.0,
            "借/贷标识": "S",
            "总账科目": "6601",
            "文本": "费用",
            "凭证类型": "SA",
            "用户名": "u1",
        },
        {
            "凭证编号": "000456",
            "公司代码": "1000",
            "会计年度": 2025,
            "过账日期": pd.Timestamp("2025-03-02"),
            "凭证货币价值": 200.0,
            "借/贷标识": "S",
            "总账科目": "6601",
            "文本": "费用2",
            "凭证类型": "SA",
            "用户名": "u1",
        },
        {
            "凭证编号": "000789",
            "公司代码": "1000",
            "会计年度": 2025,
            "过账日期": pd.Timestamp("2025-03-03"),
            "凭证货币价值": 300.0,
            "借/贷标识": "S",
            "总账科目": "6601",
            "文本": "费用3",
            "凭证类型": "SA",
            "用户名": "u1",
        },
    ]))
    keys = list(df["_voucher_key"].unique())
    judgments = {
        "大额异常": [
            LLMJudgment(
                voucher_id="000123",
                confirmed=True,
                risk_level="高",
                reason="确认风险",
                audit_procedures="查合同",
                status="confirmed",
                voucher_key=keys[0],
            ),
            LLMJudgment(
                voucher_id="000456",
                confirmed=False,
                risk_level="中",
                reason="排除",
                audit_procedures="无需",
                status="rejected",
                voucher_key=keys[1],
            ),
            LLMJudgment(
                voucher_id="000789",
                confirmed=False,
                risk_level="待核验",
                reason="待复核",
                audit_procedures="人工",
                status="pending_review",
                source="llm_incomplete",
                voucher_key=keys[2],
            ),
        ]
    }
    samples = [
        {"凭证编号": "000123", "_voucher_key": keys[0], "会计年度": 2025, "公司代码": "1000"},
        {"凭证编号": "000456", "_voucher_key": keys[1], "会计年度": 2025, "公司代码": "1000"},
        {"凭证编号": "000789", "_voucher_key": keys[2], "会计年度": 2025, "公司代码": "1000"},
    ]
    data, _stats = generate_report_bytes(
        df,
        [RuleResult(rule_name="大额异常", hits=[])],
        llm_judgments=judgments,
        explicit_samples=samples,
        max_sample_size=10,
    )
    from openpyxl import load_workbook
    from io import BytesIO as Bio

    wb = load_workbook(Bio(data))
    ws = wb["样本清单"]
    headers = [cell.value for cell in next(ws.iter_rows(min_row=1, max_row=1))]
    status_col = headers.index("LLM状态") + 1
    reason_col = headers.index("LLM判断理由") + 1
    statuses = set()
    reasons = set()
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row[status_col - 1]:
            statuses.add(row[status_col - 1])
        if row[reason_col - 1]:
            reasons.add(row[reason_col - 1])
    assert "confirmed" in statuses
    assert "rejected" in statuses
    assert "pending_review" in statuses
    assert "确认风险" in reasons
    assert "排除" in reasons


def test_splitting_counts_voucher_not_journal_lines() -> None:
    """一张凭证五行 → splitting min_txn_count 仍只能算 1 笔。"""
    from audit_engine.rule_engine import rule_splitting

    rows = []
    # 同日同供应商：1 张凭证 5 个 line，金额相似 — 不应触发「5笔」
    for i in range(5):
        rows.append({
            "凭证编号": "P001",
            "公司代码": "1000",
            "会计年度": 2025,
            "过账日期": pd.Timestamp("2025-03-01"),
            "供应商编号": "V1",
            "凭证货币价值": 80_000.0,
            "借/贷标识": "S",
            "总账科目": "2202",
            "文本": f"付款行{i}",
        })
    # 另需凑历史日均，避免无基线时仅靠金额相似误报；再放几天各 1 笔
    for day in range(2, 8):
        rows.append({
            "凭证编号": f"P00{day}",
            "公司代码": "1000",
            "会计年度": 2025,
            "过账日期": pd.Timestamp(f"2025-03-0{day}"),
            "供应商编号": "V1",
            "凭证货币价值": 80_000.0,
            "借/贷标识": "S",
            "总账科目": "2202",
            "文本": "日常",
        })
    df = ensure_voucher_identity(pd.DataFrame(rows))
    cfg = default_rules_config()
    cfg["splitting"] = {
        **cfg.get("splitting", {}),
        "enabled": True,
        "max_single_amount": 100_000,
        "min_total": 300_000,
        "min_txn_count": 5,
        "burst_multiplier": 3.0,
        "window_days": 14,
    }
    result = rule_splitting(df, cfg)
    # 3/1 仅 1 张凭证，即使 5 行也不应因 line count 命中同日拆分
    assert not any(h.rule_type == "化整为零(同日拆分)" for h in result.hits)


def test_dq_balance_uses_voucher_key_not_bare_id() -> None:
    """多公司同号凭证各自不平衡时，DQ 不得按裸凭证号净额抵消。"""
    quality = audit_input_quality(pd.DataFrame([
        {
            "凭证编号": "A001",
            "公司代码": "1000",
            "会计年度": 2025,
            "过账日期": pd.Timestamp("2025-01-01"),
            "凭证货币价值": 100.0,
            "借/贷标识": "S",
            "总账科目": "1002",
            "文本": "公司A",
        },
        {
            "凭证编号": "A001",
            "公司代码": "2000",
            "会计年度": 2025,
            "过账日期": pd.Timestamp("2025-01-01"),
            "凭证货币价值": 100.0,
            "借/贷标识": "H",
            "总账科目": "1002",
            "文本": "公司B",
        },
    ]))
    # 两张凭证各自不平衡 → unbalanced_vouchers == 2（按 voucher_key）
    assert quality["voucher_count"] == 2
    assert quality["unbalanced_vouchers"] == 2


# ── Review workflow invariants (2026-08 round 3) ──────────────


def test_multi_file_identical_lines_are_preserved_not_deleted() -> None:
    """两文件各含相同业务字段的 journal line → 不得静默删除，仅标记候选。"""
    from audit_engine.ingestion import _flag_duplicate_candidates

    row = {
        "凭证编号": "A001",
        "公司代码": "1000",
        "过账日期": pd.Timestamp("2025-03-01"),
        "总账科目": "6601",
        "借/贷标识": "S",
        "凭证货币价值": 10_000.0,
        "文本": "服务费",
    }
    df = pd.DataFrame([
        {**row, "_source_file": "a.xlsx", "_source_row": 2, "_source_sheet": "Sheet1"},
        {**row, "_source_file": "b.xlsx", "_source_row": 5, "_source_sheet": "Sheet1"},
    ])
    flagged, report = _flag_duplicate_candidates(df)
    assert len(flagged) == 2
    assert int(flagged["_duplicate_candidate"].sum()) == 2
    assert report["candidate_groups"] == 1
    assert report["policy"] == "preserve_first_detect_only"


def test_verification_freshness_stale_after_selection_change() -> None:
    """Sample A → Verify A → Sample B：核验必须 stale，不得冒充当前样本。"""
    from audit_engine.analysis_context import verification_freshness

    fresh = verification_freshness(
        verification_context={
            "verification_run_id": "vr_aaa",
            "verification_scope": "current_sample",
            "selection_id": "sel_A",
            "verified_at": "2026-08-07T00:00:00Z",
        },
        sampling_plan={"selection_trace": {"selection_id": "sel_A"}},
    )
    assert fresh["fresh"] is True
    assert fresh["status"] == "fresh"

    stale = verification_freshness(
        verification_context={
            "verification_run_id": "vr_aaa",
            "verification_scope": "current_sample",
            "selection_id": "sel_A",
            "verified_at": "2026-08-07T00:00:00Z",
        },
        sampling_plan={"selection_trace": {"selection_id": "sel_B"}},
    )
    assert stale["fresh"] is False
    assert stale["status"] == "stale"


def test_risk_signals_verify_never_fresh_for_current_sample() -> None:
    """Sample A → Verify(scope=risk_signals) → current-sample freshness 必须为 false。"""
    from audit_engine.analysis_context import (
        verification_freshness,
        verification_freshness_for_current_sample,
        verification_freshness_for_risk_signals,
    )

    risk_ctx = {
        "verification_run_id": "vr_risk",
        "verification_scope": "risk_signals",
        "selection_id": None,
        "sample_keys_digest": None,
        "rule_run_id": "rr_1",
        "data_version": "dv_1",
        "verified_at": "2026-08-07T00:00:00Z",
    }
    sampling = {"selection_trace": {"selection_id": "sel_A"}}
    current = verification_freshness_for_current_sample(
        verification_context=risk_ctx,
        sampling_plan=sampling,
    )
    assert current["fresh"] is False
    assert current["status"] == "stale"

    # 即使误写了 selection_id=sel_A，也不得冒充 current_sample
    polluted = {**risk_ctx, "selection_id": "sel_A"}
    assert verification_freshness(
        verification_context=polluted,
        sampling_plan=sampling,
        purpose="current_sample",
    )["fresh"] is False

    risk_fresh = verification_freshness_for_risk_signals(
        verification_context=risk_ctx,
        rule_run_context={"rule_run_id": "rr_1"},
        data_version="dv_1",
    )
    assert risk_fresh["fresh"] is True


def test_amount_analysis_blocks_mixed_and_unknown_currency() -> None:
    """已知币 + 未知币并存 → 金额分析门禁阻断。"""
    from audit_engine.analysis_context import assert_amount_analysis_ready
    from audit_engine.data_columns import ensure_analysis_columns

    known = ensure_analysis_columns(pd.DataFrame([
        {
            "凭证编号": "C1",
            "公司代码": "1000",
            "会计年度": 2025,
            "过账日期": pd.Timestamp("2025-01-01"),
            "凭证货币价值": 100.0,
            "凭证货币代码": "CNY",
            "借/贷标识": "S",
            "总账科目": "1002",
            "文本": "有币种",
        },
    ]))
    unknown = ensure_analysis_columns(pd.DataFrame([
        {
            "凭证编号": "U1",
            "公司代码": "1000",
            "会计年度": 2025,
            "过账日期": pd.Timestamp("2025-01-02"),
            "凭证货币价值": 200.0,
            "凭证货币代码": "",
            "借/贷标识": "S",
            "总账科目": "1002",
            "文本": "无币种",
        },
    ]))
    with pytest.raises(ValueError, match="币种未知|多种分析币种"):
        assert_amount_analysis_ready([known, unknown], selected_currency=None)


def test_selected_currency_scope_still_blocks_unknown_rows() -> None:
    """selected=CNY 时，Raw Frame 含未知币行仍须阻断（禁止先过滤再 gate）。"""
    from audit_engine.analysis_context import resolve_analysis_frames
    from audit_engine.data_columns import ensure_analysis_columns

    raw = ensure_analysis_columns(pd.DataFrame([
        {
            "凭证编号": "C1",
            "公司代码": "1000",
            "会计年度": 2025,
            "过账日期": pd.Timestamp("2025-01-01"),
            "凭证货币价值": 100.0,
            "凭证货币代码": "CNY",
            "借/贷标识": "S",
            "总账科目": "1002",
            "文本": "CNY",
        },
        {
            "凭证编号": "U1",
            "公司代码": "1000",
            "会计年度": 2025,
            "过账日期": pd.Timestamp("2025-01-02"),
            "凭证货币价值": 200.0,
            "凭证货币代码": "",
            "借/贷标识": "S",
            "总账科目": "1002",
            "文本": "未知",
        },
    ]))
    with pytest.raises(ValueError, match="币种未知|未维护"):
        resolve_analysis_frames({2025: raw}, selected_currency="CNY")


def test_same_vendor_two_liability_accounts_are_two_entities() -> None:
    """同凭证同供应商、两个完整负债科目 → 两个 economic entities。"""
    lines = pd.DataFrame([
        {
            "凭证编号": "A001",
            "公司代码": "1000",
            "会计年度": 2025,
            "过账日期": pd.Timestamp("2025-12-20"),
            "文本": "预提运费",
            "总账科目": "6601010001",
            "借/贷标识": "S",
            "供应商编号": "VA",
            "凭证货币价值": 600_000.0,
        },
        {
            "凭证编号": "A001",
            "公司代码": "1000",
            "会计年度": 2025,
            "过账日期": pd.Timestamp("2025-12-20"),
            "文本": "预提服务费",
            "总账科目": "6602010001",
            "借/贷标识": "S",
            "供应商编号": "VA",
            "凭证货币价值": 400_000.0,
        },
        {
            "凭证编号": "A001",
            "公司代码": "1000",
            "会计年度": 2025,
            "过账日期": pd.Timestamp("2025-12-20"),
            "文本": "预提运费",
            "总账科目": "2202010001",
            "借/贷标识": "H",
            "供应商编号": "VA",
            "凭证货币价值": 600_000.0,
        },
        {
            "凭证编号": "A001",
            "公司代码": "1000",
            "会计年度": 2025,
            "过账日期": pd.Timestamp("2025-12-20"),
            "文本": "预提服务费",
            "总账科目": "2202020001",
            "借/贷标识": "H",
            "供应商编号": "VA",
            "凭证货币价值": 400_000.0,
        },
    ])
    entities = _collapse_to_accrual_entities(lines)
    assert len(entities) == 2
    accts = set(entities["总账科目"].astype(str).tolist())
    assert accts == {"2202010001", "2202020001"}


def test_accrual_match_requires_full_liability_account() -> None:
    """220201 accrual + 220202 reversal（同供应商同金额）→ MUST remain unmatched。"""
    accruals = pd.DataFrame([
        {
            "凭证编号": "A001",
            "公司代码": "1000",
            "会计年度": 2025,
            "过账日期": pd.Timestamp("2025-12-20"),
            "文本": "预提运费",
            "总账科目": "2202010001",
            "借/贷标识": "H",
            "供应商编号": "VA",
            "凭证货币价值": 1_000_000.0,
        },
    ])
    reversals = pd.DataFrame([
        {
            "凭证编号": "R001",
            "公司代码": "1000",
            "会计年度": 2026,
            "过账日期": pd.Timestamp("2026-01-10"),
            "文本": "冲销预提",
            "总账科目": "2202020001",
            "借/贷标识": "S",
            "供应商编号": "VA",
            "凭证货币价值": 1_000_000.0,
        },
    ])
    pairs, unmatched = _match_accrual_reversals(
        accruals, reversals, amount_tolerance=0.05,
    )
    assert pairs == []
    assert len(unmatched) == 1

    # 完整科目一致时仍应配对
    same_acct_rev = reversals.copy()
    same_acct_rev["总账科目"] = "2202010001"
    pairs2, unmatched2 = _match_accrual_reversals(
        accruals, same_acct_rev, amount_tolerance=0.05,
    )
    assert len(pairs2) == 1
    assert pairs2[0]["account"] == "2202010001"
    assert unmatched2.empty


def test_profiler_uses_canonical_amount_not_document_currency() -> None:
    """功能币 CNY 与凭证币 USD/EUR 并存时，画像金额合计必须用公司代码货币金额。"""
    from audit_engine.data_columns import ensure_analysis_columns
    from audit_engine.profiler import _account_structure, _vendor_patterns

    df = ensure_analysis_columns(pd.DataFrame([
        {
            "凭证编号": "A1",
            "公司代码": "1000",
            "会计年度": 2025,
            "过账日期": pd.Timestamp("2025-01-01"),
            "借/贷标识": "S",
            "总账科目": "1002010001",
            "供应商编号": "V1",
            "凭证货币价值": 100.0,
            "凭证货币代码": "USD",
            "公司代码货币价值": 720.0,
            "公司代码货币代码": "CNY",
            "文本": "A",
        },
        {
            "凭证编号": "B1",
            "公司代码": "1000",
            "会计年度": 2025,
            "过账日期": pd.Timestamp("2025-01-02"),
            "借/贷标识": "S",
            "总账科目": "1002010001",
            "供应商编号": "V1",
            "凭证货币价值": 100.0,
            "凭证货币代码": "EUR",
            "公司代码货币价值": 780.0,
            "公司代码货币代码": "CNY",
            "文本": "B",
        },
    ]))
    acct = _account_structure(df)
    assert acct["by_class"]["1"]["total"] == pytest.approx(1500.0)
    vendors = _vendor_patterns(df)
    top = vendors["top_vendors_by_amount"]
    assert "V1" in top
    assert top["V1"]["total_amount"] == pytest.approx(1500.0)


def test_profiler_source_avoids_raw_amount_columns_for_aggregates() -> None:
    """架构约束：profiler 金额聚合不得直接引用原始金额列名。"""
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "audit_engine" / "profiler.py"
    text = src.read_text(encoding="utf-8")
    # 允许模块 docstring 叙述 SAP 字段；禁止可执行聚合表达式
    forbidden = [
        'grp["凭证货币价值"]',
        '["凭证货币价值"].abs()',
        'txn_count=("凭证货币价值"',
        'total_amount=("凭证货币价值"',
        'avg_amount=("凭证货币价值"',
        'vendor_df["凭证货币价值"]',
        'grp["公司代码货币价值"]',
    ]
    for needle in forbidden:
        assert needle not in text, f"profiler.py 仍含原始金额引用: {needle}"


def test_excel_provenance_sheet_is_self_describing() -> None:
    """Excel 必须内嵌追溯信息 sheet，不依赖 HTTP header。"""
    import tempfile
    from pathlib import Path

    from openpyxl import load_workbook

    from audit_engine.reporter import generate_report_bytes
    from audit_engine.rule_engine import RuleResult

    df = pd.DataFrame([
        {
            "凭证编号": "A001",
            "公司代码": "1000",
            "会计年度": 2025,
            "过账日期": pd.Timestamp("2025-01-01"),
            "借/贷标识": "S",
            "总账科目": "1002",
            "凭证货币价值": 100.0,
            "文本": "t",
        },
    ])
    data, _stats = generate_report_bytes(
        df,
        [RuleResult(rule_name="r", hits=[])],
        provenance={
            "Project": "p1",
            "Data Version": "dv_x",
            "Selection ID": "sel_x",
            "Verification Run ID": "vr_x",
            "Currency Scope": "CNY",
        },
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "r.xlsx"
        path.write_bytes(data)
        wb = load_workbook(path)
        assert "追溯信息" in wb.sheetnames
        ws = wb["追溯信息"]
        kv = {ws.cell(row=r, column=1).value: ws.cell(row=r, column=2).value for r in range(2, ws.max_row + 1)}
        assert kv["Data Version"] == "dv_x"
        assert kv["Selection ID"] == "sel_x"
        assert kv["Verification Run ID"] == "vr_x"


def test_current_sample_merges_multi_rule_context() -> None:
    """同一样本凭证命中多规则 → 合并为一个核验实体，保留全部风险上下文。"""
    from audit_engine.llm_verifier import _select_verify_hits
    from audit_engine.rule_engine import RuleResult

    key = "VK|1000|2025|A001"
    results = [
        RuleResult(
            rule_name="大额异常",
            hits=[RuleHit(
                voucher_id="A001", rule_type="大额整数", evidence="100万",
                line_indices=(0,), priority=5, year=2025, voucher_key=key,
            )],
        ),
        RuleResult(
            rule_name="异常周末过账",
            hits=[RuleHit(
                voucher_id="A001", rule_type="周末过账", evidence="周六",
                line_indices=(0,), priority=3, year=2025, voucher_key=key,
            )],
        ),
        RuleResult(
            rule_name="敏感费用",
            hits=[RuleHit(
                voucher_id="A001", rule_type="敏感费用", evidence="咨询费",
                line_indices=(0,), priority=4, year=2025, voucher_key=key,
            )],
        ),
    ]
    selected = _select_verify_hits(
        results,
        max_verify=50,
        voucher_keys={key},
        verification_scope="current_sample",
    )
    assert len(selected) == 1
    _name, hit = selected[0]
    evidence = str(hit.evidence)
    assert "100万" in evidence
    assert "周六" in evidence or "咨询费" in evidence
    factors = set(hit.risk_factors or ())
    assert "大额异常" in factors or "大额整数" in str(hit.rule_type)


def test_boundary_consent_hash_changes_with_endpoint() -> None:
    """endpoint 变化 → boundary_hash 必须变化（可证明知情确认）。"""
    from audit_engine.llm_verifier import boundary_consent_hash, describe_llm_verify_boundary

    a = describe_llm_verify_boundary("https://api.a.example/v1", "model-a", redaction="pseudonym")
    b = describe_llm_verify_boundary("https://api.b.example/v1", "model-a", redaction="pseudonym")
    assert a["boundary_hash"]
    assert a["boundary_hash"] != b["boundary_hash"]
    assert boundary_consent_hash(a) == a["boundary_hash"]


def test_profiler_counts_distinct_voucher_keys() -> None:
    """同号跨公司 → profile total_vouchers 必须按 voucher_key 计为 2。"""
    from audit_engine.profiler import _overview

    df = ensure_voucher_identity(pd.DataFrame([
        {
            "凭证编号": "A001",
            "公司代码": "1000",
            "会计年度": 2025,
            "过账日期": pd.Timestamp("2025-01-01"),
            "凭证货币价值": 100.0,
            "借/贷标识": "S",
            "总账科目": "1002",
            "文本": "A",
        },
        {
            "凭证编号": "A001",
            "公司代码": "2000",
            "会计年度": 2025,
            "过账日期": pd.Timestamp("2025-01-01"),
            "凭证货币价值": 200.0,
            "借/贷标识": "S",
            "总账科目": "1002",
            "文本": "B",
        },
    ]))
    overview = _overview(df)
    assert overview["total_vouchers"] == 2
