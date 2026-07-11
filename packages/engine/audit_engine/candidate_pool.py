"""疑点库数据层 — 保存可疑样本群体，非最终审计结论。"""

from __future__ import annotations

import hashlib
import json
import random
from datetime import datetime
from typing import Any

import pandas as pd

DEFAULT_STATUS = "候选"
MANUAL_FINAL_STATUS = "人工直入最终样本"
EXCLUDED_STATUS = "排除"


def candidate_id_for(
    *,
    source_view: str,
    selector: dict[str, Any],
    voucher_ids: list[str],
) -> str:
    raw = json.dumps(
        {
            "source_view": source_view,
            "selector": selector,
            "voucher_ids": sorted(set(str(v) for v in voucher_ids)),
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return "cand_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def build_candidate_group(
    *,
    title: str,
    source_module: str,
    source_view: str,
    detail: pd.DataFrame,
    tags: list[str] | None = None,
    reason: str = "",
    selector: dict[str, Any] | None = None,
    status: str = DEFAULT_STATUS,
    created_by: str = "manual",
) -> dict[str, Any]:
    voucher_ids = _voucher_ids(detail)
    group_id = candidate_id_for(
        source_view=source_view,
        selector=selector or {},
        voucher_ids=voucher_ids,
    )
    return {
        "group_id": group_id,
        "title": title,
        "source_module": source_module,
        "source_view": source_view,
        "selector": selector or {},
        "tags": sorted({str(t).strip() for t in tags or [] if str(t).strip()}),
        "reason": reason.strip(),
        "status": status,
        "created_by": created_by,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "voucher_ids": voucher_ids,
        "row_count": int(len(detail)),
        "voucher_count": len(voucher_ids),
        "amount_total": _amount_total(detail),
    }


def add_candidate_group(pool: list[dict[str, Any]] | None, group: dict[str, Any]) -> list[dict[str, Any]]:
    groups = list(pool or [])
    existing_idx = next(
        (idx for idx, item in enumerate(groups) if item.get("group_id") == group.get("group_id")),
        None,
    )
    if existing_idx is None:
        groups.append(group)
    else:
        groups[existing_idx] = {**groups[existing_idx], **group}
    return groups


def remove_candidate_group(pool: list[dict[str, Any]] | None, group_id: str) -> list[dict[str, Any]]:
    return [group for group in pool or [] if group.get("group_id") != group_id]


def update_candidate_status(
    pool: list[dict[str, Any]] | None,
    group_id: str,
    status: str,
) -> list[dict[str, Any]]:
    groups = list(pool or [])
    for group in groups:
        if group.get("group_id") == group_id:
            group["status"] = status
            break
    return groups


def active_candidate_voucher_ids(pool: list[dict[str, Any]] | None) -> set[str]:
    statuses = {DEFAULT_STATUS, MANUAL_FINAL_STATUS}
    return {
        str(vid)
        for group in pool or []
        if group.get("status", DEFAULT_STATUS) in statuses
        for vid in group.get("voucher_ids", [])
    }


def pool_stats(pool: list[dict[str, Any]] | None) -> dict[str, int | float]:
    groups = list(pool or [])
    active_vouchers = {
        str(vid)
        for group in groups
        if group.get("status", DEFAULT_STATUS) != EXCLUDED_STATUS
        for vid in group.get("voucher_ids", [])
    }
    return {
        "groups": len(groups),
        "active_groups": sum(1 for group in groups if group.get("status", DEFAULT_STATUS) != EXCLUDED_STATUS),
        "active_vouchers": len(active_vouchers),
        "amount_total": sum(float(group.get("amount_total", 0) or 0) for group in groups),
    }


def _voucher_ids(detail: pd.DataFrame) -> list[str]:
    if detail.empty or "凭证编号" not in detail.columns:
        return []
    return sorted({str(v) for v in detail["凭证编号"].dropna().astype(str).tolist()})


def _amount_total(detail: pd.DataFrame) -> float:
    priority_cols = [
        "收入影响",
        "成本发生额",
        "毛利影响",
        "费用发生额",
        "收入S影响",
        "成本H影响",
        "公司代码货币价值",
        "凭证货币价值",
    ]
    for col in priority_cols:
        if col in detail.columns:
            return float(pd.to_numeric(detail[col], errors="coerce").fillna(0).abs().sum())
    return 0.0

# ── 科目分类 ──
# 把基础类别和审计语义对齐：
# - "收入/成本/费用/往来/税金"直接复用 account_classifier 的自动分类结果；
# - "营业外/资产相关"是审计抽样权重需要保留的兜底分类，按编号前缀走。

ACCOUNT_CATEGORY_RULES: dict[str, tuple[tuple[str, ...], str]] = {
    "营业外": (("6301", "6711", "6111"), "营业外收支+投资收益"),
    "资产相关": (("1403", "1405", "1602", "5001", "8142", "8143"), "存货/折旧/生产/制造分摊"),
}


# 把 account_classifier 类别名映射到候选池权重表里的简化标签。
_CLASSIFIER_TO_WEIGHT: dict[str, str] = {
    "收入": "收入",
    "成本": "成本",
    "费用": "费用",
    "研发费用": "费用",
    "财务费用": "费用",
    "税金及附加": "税金",
    "应收": "往来",
    "其他应收": "往来",
    "应付": "往来",
    "应付暂估": "往来",
    "其他应付": "往来",
}

DEFAULT_ACCOUNT_WEIGHTS: dict[str, float] = {
    "收入": 0.25,
    "成本": 0.25,
    "费用": 0.20,
    "往来": 0.15,
    "其他": 0.15,
}


def _classify_account(acct_code: str, acct_name: str = "") -> str:
    """根据科目名称（优先）+ 编号前缀（兜底）归类。

    名称命中 account_classifier 自动分类的 11 大类时，按 _CLASSIFIER_TO_WEIGHT
    映射到候选池权重表的简化标签；
    名称没命中（或为空）时，才回退到 ACCOUNT_CATEGORY_RULES 中的"营业外"/"资产相关"
    这两类（这两类基本只能通过编号识别）。
    """
    from audit_engine.account_classifier import CAT_UNCATEGORIZED, auto_classify

    name_cat = auto_classify(acct_name) if acct_name else CAT_UNCATEGORIZED
    if name_cat != CAT_UNCATEGORIZED:
        mapped = _CLASSIFIER_TO_WEIGHT.get(name_cat)
        if mapped:
            return mapped

    # 兜底：营业外 / 资产相关 按编号前缀识别
    acct = str(acct_code).strip()
    for cat, (prefixes, _) in ACCOUNT_CATEGORY_RULES.items():
        if any(acct.startswith(p) for p in prefixes):
            return cat
    return "其他"


def _build_voucher_amount_map(df: pd.DataFrame) -> dict[str, float]:
    """构建凭证编号 → 金额绝对值 的映射。"""
    amt_col = "公司代码货币价值" if "公司代码货币价值" in df.columns else "凭证货币价值"
    if amt_col not in df.columns or "凭证编号" not in df.columns:
        return {}
    amounts = df.groupby("凭证编号")[amt_col].apply(
        lambda x: pd.to_numeric(x, errors="coerce").fillna(0).abs().sum()
    )
    return {str(k): float(v) for k, v in amounts.items()}


def voucher_ids_from_rule_results(rule_results: list[Any], size: int | None = None) -> list[str]:
    """按规则命中优先级提取最终样本凭证号，包含关联凭证。"""
    voucher_priority: dict[str, int] = {}
    first_seen: dict[str, int] = {}

    for rr in rule_results or []:
        for hit in getattr(rr, "hits", []) or []:
            priority = int(getattr(hit, "priority", 1) or 1)
            hit_ids = [getattr(hit, "voucher_id", "")]
            hit_ids.extend(getattr(hit, "related_voucher_ids", ()) or ())
            for raw_vid in hit_ids:
                vid = str(raw_vid).strip()
                if not vid:
                    continue
                if vid not in first_seen:
                    first_seen[vid] = len(first_seen)
                voucher_priority[vid] = max(voucher_priority.get(vid, 0), priority)

    ordered = sorted(
        voucher_priority,
        key=lambda v: (-voucher_priority[v], first_seen[v], v),
    )
    if size and size > 0:
        return ordered[:size]
    return ordered


def _sample_amount(row: pd.Series) -> float:
    normalized = pd.to_numeric(row.get("_amount_raw"), errors="coerce")
    if pd.notna(normalized):
        return float(normalized)
    for col in ("公司代码货币价值", "凭证货币价值"):
        value = row.get(col)
        amount = pd.to_numeric(value, errors="coerce")
        if pd.notna(amount):
            return float(amount)
    return 0.0


def samples_for_voucher_ids(
    voucher_ids: set[str] | list[str] | tuple[str, ...],
    df: pd.DataFrame,
    pool: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """按凭证号从原始序时账展开样本明细。"""
    if "凭证编号" not in df.columns:
        return []

    selected_voucher_ids = {str(v) for v in voucher_ids if str(v)}
    if not selected_voucher_ids:
        return []

    groups = list(pool or [])
    manual_vids = {
        str(vid)
        for group in groups
        if group.get("status") == MANUAL_FINAL_STATUS
        for vid in group.get("voucher_ids", [])
    }

    result_samples = []
    matched_df = df[df["凭证编号"].astype(str).isin(selected_voucher_ids)].copy()

    if "_amount_abs" in matched_df.columns:
        matched_df["_sort_amount"] = matched_df["_amount_abs"]
    elif "公司代码货币价值" in matched_df.columns:
        matched_df["_sort_amount"] = pd.to_numeric(matched_df["公司代码货币价值"], errors="coerce").abs()
    elif "凭证货币价值" in matched_df.columns:
        matched_df["_sort_amount"] = pd.to_numeric(matched_df["凭证货币价值"], errors="coerce").abs()
    else:
        matched_df["_sort_amount"] = 0

    matched_df = matched_df.sort_values("_sort_amount", ascending=False)

    for _, row in matched_df.iterrows():
        vid = str(row["凭证编号"])
        is_manual = vid in manual_vids
        amount = _sample_amount(row)
        dc = str(row.get("借/贷标识", ""))
        result_samples.append({
            "凭证编号": vid,
            "过账日期": str(row.get("过账日期", ""))[:10] if pd.notna(row.get("过账日期")) else "",
            "凭证类型": str(row.get("凭证类型", "")),
            "文本": str(row.get("文本", ""))[:60],
            "总账科目": str(row.get("总账科目", "")),
            "科目名称": str(row.get("总账科目：长文本", "")),
            "借方金额": amount if dc == "S" else 0,
            "贷方金额": -amount if dc == "H" else 0,
            "来源模块": _find_group_source(groups, vid),
            "是否为人工直入": is_manual,
        })

    return result_samples


def sample_from_rule_results(
    rule_results: list[Any],
    df: pd.DataFrame,
    size: int | None = None,
    pool: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """直接从已执行的规则结果生成最终样本，避免二次运行规则导致口径漂移。"""
    voucher_ids = voucher_ids_from_rule_results(rule_results, size=size)
    return samples_for_voucher_ids(voucher_ids, df, pool=pool)


def sample_from_pool(
    pool: list[dict[str, Any]] | None,
    df: pd.DataFrame,
    method: str = "by_rule",
    size: int | None = None,
    rules_config: dict | None = None,
    seed: int = 42,
    account_weights: dict[str, float] | None = None,
    stratify_by: str | None = None,
    stratify_mode: str = "proportional",
) -> list[dict[str, Any]]:
    """
    从候选池中按指定方式和规则抽取最终样本。

    Parameters
    ----------
    pool : 候选群体列表
    df : 全量序时账 DataFrame
    method : 抽样方式，可选 "by_rule" | "random" | "all"
        - "by_rule": 按规则引擎命中结果抽取（需提供 rules_config）
        - "random": 随机抽样
        - "all": 取候选池中所有凭证（不抽样）
    size : 样本量上限（凭证数），仅 method="random" 时生效
    rules_config : 规则配置，仅 method="by_rule" 时需要
    seed : 随机种子

    Returns
    -------
    list[dict] : 每个元素包含凭证编号、过账日期、金额、来源模块、风险等级等
    """
    from audit_engine.rule_engine import run_all_rules

    groups = list(pool or [])
    if not groups and method != "by_rule":
        return []

    # 收集候选池中所有活动凭证
    candidate_voucher_ids = active_candidate_voucher_ids(groups)

    if method == "all":
        # 全量：直接取所有候选凭证
        selected_voucher_ids = candidate_voucher_ids
    elif method == "random":
        # 随机抽样
        if not candidate_voucher_ids:
            return []
        ids_list = sorted(candidate_voucher_ids)
        random.seed(seed)
        if size and size < len(ids_list):
            selected_voucher_ids = set(random.sample(ids_list, size))
        else:
            selected_voucher_ids = ids_list
    elif method == "by_rule":
        # 规则引擎筛选
        if not rules_config:
            rules_config = {}
        if candidate_voucher_ids:
            results = run_all_rules(
                df, rules_config,
                candidate_voucher_ids=candidate_voucher_ids,
            )
        else:
            results = run_all_rules(df, rules_config)

        selected_voucher_ids = set(voucher_ids_from_rule_results(results, size=size))
    elif method == "by_account_weight":
        # ── 科目权重抽样 ──
        weights = account_weights or DEFAULT_ACCOUNT_WEIGHTS
        # 归一化权重
        total_w = sum(weights.values()) or 1.0
        weights = {k: v / total_w for k, v in weights.items()}

        amt_map = _build_voucher_amount_map(df)
        candidate_list = sorted(candidate_voucher_ids)

        # 按科目分类凭证
        voucher_acct_map: dict[str, str] = {}
        if "总账科目" in df.columns and "凭证编号" in df.columns:
            name_col = next(
                (c for c in ("总账科目:长文本", "总账科目:短文本", "总账科目：长文本", "总账科目：短文本")
                 if c in df.columns),
                None,
            )
            cols = ["凭证编号", "总账科目"] + ([name_col] if name_col else [])
            voucher_meta = (
                df[cols]
                .drop_duplicates(subset="凭证编号")
                .assign(_vid=lambda d: d["凭证编号"].astype(str))
            )
            if name_col:
                voucher_acct_map = {
                    vid: _classify_account(str(acct), str(name) if pd.notna(name) else "")
                    for vid, acct, name in zip(
                        voucher_meta["_vid"], voucher_meta["总账科目"], voucher_meta[name_col],
                        strict=False,
                    )
                    if vid in candidate_voucher_ids
                }
            else:
                voucher_acct_map = {
                    vid: _classify_account(str(acct))
                    for vid, acct in zip(voucher_meta["_vid"], voucher_meta["总账科目"], strict=False)
                    if vid in candidate_voucher_ids
                }

        # 按科目分组
        by_cat: dict[str, list[str]] = {}
        for vid in candidate_list:
            cat = voucher_acct_map.get(vid, "其他")
            by_cat.setdefault(cat, []).append(vid)

        # 每类内按金额降序
        for cat in by_cat:
            by_cat[cat].sort(key=lambda v: amt_map.get(v, 0), reverse=True)

        # 按权重分配名额
        selected_voucher_ids: set[str] = set()
        if size:
            for cat, weight in weights.items():
                cat_vids = by_cat.get(cat, [])
                quota = max(1, int(round(size * weight)))
                selected_voucher_ids.update(cat_vids[:quota])

            # 如果还不足 size，从剩余中补齐（按金额优先）
            if len(selected_voucher_ids) < size:
                remaining = [v for v in candidate_list if v not in selected_voucher_ids]
                remaining.sort(key=lambda v: amt_map.get(v, 0), reverse=True)
                selected_voucher_ids.update(remaining[:size - len(selected_voucher_ids)])

            # 截断到 size
            if len(selected_voucher_ids) > size:
                sorted_selected = sorted(selected_voucher_ids, key=lambda v: amt_map.get(v, 0), reverse=True)
                selected_voucher_ids = set(sorted_selected[:size])
        else:
            selected_voucher_ids = candidate_voucher_ids

    elif method == "monetary_unit":
        # ── 货币单元抽样 (MUS) ──
        amt_map = _build_voucher_amount_map(df)
        candidate_list = [v for v in sorted(candidate_voucher_ids) if amt_map.get(v, 0) > 0]
        if not candidate_list:
            selected_voucher_ids = candidate_voucher_ids
        elif not size or size <= 0:
            selected_voucher_ids = set(candidate_list)
        else:
            random.seed(seed)
            # 构建累积金额
            cumulative = 0.0
            intervals: list[tuple[str, float, float]] = []  # (vid, start, end)
            for vid in candidate_list:
                amt = amt_map.get(vid, 0)
                if amt <= 0:
                    continue
                intervals.append((vid, cumulative, cumulative + amt))
                cumulative += amt

            total_amount = cumulative
            if total_amount <= 0:
                selected_voucher_ids = set(candidate_list[:size]) if candidate_list else set()
            else:
                interval = total_amount / size
                start = random.uniform(0, interval)
                selected_voucher_ids = set()
                for i in range(size):
                    point = start + i * interval
                    if point >= total_amount:
                        point -= total_amount  # wrap around
                    # 二分查找
                    for vid, lo, hi in intervals:
                        if lo <= point < hi:
                            selected_voucher_ids.add(vid)
                            break

    elif method == "stratified":
        # ── 分层抽样 ──
        strata_attr = stratify_by or "凭证类型"
        candidate_list = sorted(candidate_voucher_ids)
        if not size:
            selected_voucher_ids = set(candidate_list)
        else:
            # 构建凭证属性映射
            vid_attr: dict[str, str] = {}
            if strata_attr in df.columns and "凭证编号" in df.columns:
                attr_meta = (
                    df[["凭证编号", strata_attr]]
                    .drop_duplicates(subset="凭证编号")
                    .assign(_vid=lambda d: d["凭证编号"].astype(str))
                )
                vid_attr = {
                    vid: str(attr)[:30]
                    for vid, attr in zip(attr_meta["_vid"], attr_meta[strata_attr], strict=False)
                    if vid in candidate_voucher_ids
                }
            elif strata_attr == "科目大类":
                if "总账科目" in df.columns and "凭证编号" in df.columns:
                    name_col = next(
                        (c for c in ("总账科目:长文本", "总账科目:短文本", "总账科目：长文本", "总账科目：短文本")
                         if c in df.columns),
                        None,
                    )
                    cols = ["凭证编号", "总账科目"] + ([name_col] if name_col else [])
                    acct_meta = (
                        df[cols]
                        .drop_duplicates(subset="凭证编号")
                        .assign(_vid=lambda d: d["凭证编号"].astype(str))
                    )
                    if name_col:
                        vid_attr = {
                            vid: _classify_account(str(acct), str(name) if pd.notna(name) else "")
                            for vid, acct, name in zip(
                                acct_meta["_vid"], acct_meta["总账科目"], acct_meta[name_col],
                                strict=False,
                            )
                            if vid in candidate_voucher_ids
                        }
                    else:
                        vid_attr = {
                            vid: _classify_account(str(acct))
                            for vid, acct in zip(acct_meta["_vid"], acct_meta["总账科目"], strict=False)
                            if vid in candidate_voucher_ids
                        }
            elif strata_attr == "月份":
                if "过账日期" in df.columns and "凭证编号" in df.columns:
                    month_meta = (
                        df[["凭证编号", "过账日期"]]
                        .drop_duplicates(subset="凭证编号")
                        .assign(_vid=lambda d: d["凭证编号"].astype(str))
                    )
                    vid_attr = {}
                    for vid, raw_date in zip(month_meta["_vid"], month_meta["过账日期"], strict=False):
                        if vid not in candidate_voucher_ids:
                            continue
                        try:
                            vid_attr[vid] = f"{pd.to_datetime(raw_date).month}月"
                        except Exception:
                            vid_attr[vid] = "未知"

            # 按属性分组
            by_stratum: dict[str, list[str]] = {}
            for vid in candidate_list:
                attr = vid_attr.get(vid, "未分类")
                by_stratum.setdefault(attr, []).append(vid)

            random.seed(seed)
            selected_voucher_ids = set()
            strata = list(by_stratum.keys())

            if stratify_mode == "equal":
                # 每层等量
                per_stratum = max(1, size // max(len(strata), 1))
                for st in strata:
                    vids = by_stratum[st]
                    n = min(per_stratum, len(vids))
                    selected_voucher_ids.update(random.sample(vids, n))
            else:
                # 按层规模比例分配
                total_candidates = len(candidate_list)
                for st in strata:
                    vids = by_stratum[st]
                    quota = max(1, int(round(size * len(vids) / total_candidates)))
                    n = min(quota, len(vids))
                    selected_voucher_ids.update(random.sample(vids, n))

            # 截断到 size
            if len(selected_voucher_ids) > size:
                selected_voucher_ids = set(random.sample(sorted(selected_voucher_ids), size))

    else:
        raise ValueError(f"不支持的抽样方式: {method}")

    return samples_for_voucher_ids(selected_voucher_ids, df, pool=groups)


def _find_group_source(groups: list[dict], voucher_id: str) -> str:
    """查找凭证所属的候选群体来源模块。"""
    for group in groups:
        if voucher_id in group.get("voucher_ids", []):
            return group.get("source_module", "未知")
    return "其他"


def sample_to_table(samples: list[dict[str, Any]]) -> pd.DataFrame:
    """将抽样结果列表转换为 DataFrame 用于展示。"""
    if not samples:
        return pd.DataFrame()
    return pd.DataFrame(samples)


def candidate_pool_summary_text(pool: list[dict[str, Any]] | None, limit: int = 20) -> str:
    groups = [group for group in pool or [] if group.get("status", DEFAULT_STATUS) != EXCLUDED_STATUS]
    if not groups:
        return "当前尚未建立疑点库，规则仍按全量序时账执行。"
    lines = [
        f"当前疑点库共有 {len(groups)} 个群体，涉及 {len(active_candidate_voucher_ids(groups))} 个凭证。",
    ]
    for group in groups[:limit]:
        lines.append(
            "- "
            f"[{group.get('status', DEFAULT_STATUS)}] {group.get('title', '')}；"
            f"来源={group.get('source_module', '')}/{group.get('source_view', '')}；"
            f"凭证={group.get('voucher_count', 0)}；"
            f"标签={ '、'.join(group.get('tags', [])) or '未标注' }；"
            f"理由={group.get('reason', '') or '未填写'}"
        )
    if len(groups) > limit:
        lines.append(f"...其余 {len(groups) - limit} 个群体未展开。")
    return "\n".join(lines)
