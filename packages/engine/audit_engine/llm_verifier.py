"""LLM 逐凭证核实 — 规则命中样本的风险确认。"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from openai import APIConnectionError, APITimeoutError

from audit_engine.json_utils import parse_json_list
from audit_engine.llm_client import make_openai_client

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    import pandas as pd

    from audit_engine.rule_engine import RuleHit, RuleResult


@dataclass(frozen=True)
class LLMJudgment:
    voucher_id: str
    confirmed: bool
    risk_level: str  # "高" | "中"
    reason: str
    audit_procedures: str
    source: str = "llm"  # "llm" | "fallback"


RULE_CONTEXTS = {
    "化整为零": "同一供应商短期多笔小额付款合计大额，疑似规避审批额度拆分付款。",
    "大额异常": "大额整数付款、同供应商短期重复大额。",
    "异常周末过账": "用户周末过账率远高于公司均值，可能绕过正常审批流程。",
    "手工凭证": "用户手工凭证率远高于同事，涉及损益科目大额或月末/年末时点。",
    "计提异常": "非常规计提用户独占超50%，或计提后长期无冲销（隐藏负债/调节利润）。",
    "收入突增": "某月收入远超月均，疑似提前确认收入或冲量。",
    "资金池划转": "大额资金池/同名划转，可能存在关联方资金占用。",
    "用户集中度异常": "单用户过账量占比过高，职责分离失效。",
    "冲销反记账异常": "高频冲销或大额冲销，可能存在财务修饰。",
    "融资性贸易": "收入与成本凭证在时间、对象、文本或金额上形成低毛利组合，需复核是否为贸易形式但缺少真实货物流转的资金融通通道。",
    "跨年异常": "跨年度的预提冲回、收入时点漂移或资金循环。",
    "敏感费用": "敏感费用类别（咨询、代理、招待、旅游、捐赠、罚款等）金额超过阈值或用户敏感费用率异常高，可能存在利益输送、商业贿赂或不正当支出。",
}

SYSTEM_PROMPT = """你是一名内部审计专家，正在核实序时账中的疑似风险凭证。
对每个凭证，判断是否构成真实审计风险，并给出核查建议。
仅输出 JSON 数组，不要其他文字。"""

_LLM_VERIFY_TIMEOUT_SECONDS = 90.0
_LLM_RETRY_BACKOFF_BASE = 1.5


def judgment_to_dict(j: LLMJudgment) -> dict[str, Any]:
    return asdict(j)


def judgments_to_state(all_judgments: dict[str, list[LLMJudgment]]) -> dict[str, list[dict[str, Any]]]:
    return {
        rule_name: [judgment_to_dict(j) for j in items]
        for rule_name, items in all_judgments.items()
    }


def judgments_from_state(raw: dict[str, Any] | None) -> dict[str, list[LLMJudgment]]:
    out: dict[str, list[LLMJudgment]] = {}
    if not isinstance(raw, dict):
        return out
    for rule_name, items in raw.items():
        parsed: list[LLMJudgment] = []
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, LLMJudgment):
                parsed.append(item)
                continue
            if not isinstance(item, dict):
                continue
            parsed.append(
                LLMJudgment(
                    voucher_id=str(item.get("voucher_id", "")),
                    confirmed=bool(item.get("confirmed", False)),
                    risk_level=str(item.get("risk_level", "中") or "中"),
                    reason=str(item.get("reason", "") or ""),
                    audit_procedures=str(item.get("audit_procedures", "") or ""),
                    source=str(item.get("source", "llm") or "llm"),
                )
            )
        out[str(rule_name)] = parsed
    return out


def summarize_judgments(all_judgments: dict[str, list[LLMJudgment]]) -> dict[str, Any]:
    flat = [j for items in all_judgments.values() for j in items]
    confirmed = [j for j in flat if j.confirmed]
    unverified = [j for j in flat if not j.confirmed]
    return {
        "total": len(flat),
        "confirmed": len(confirmed),
        "unverified": len(unverified),
        "high": sum(1 for j in confirmed if j.risk_level == "高"),
        "medium": sum(1 for j in confirmed if j.risk_level != "高"),
        "fallback": sum(1 for j in flat if j.source == "fallback"),
        "rules": len(all_judgments),
    }


def verify_with_llm(
    df: pd.DataFrame,
    rule_results: list[RuleResult],
    api_key: str,
    model: str = "deepseek-chat",
    base_url: str = "https://api.deepseek.com",
    batch_size: int = 10,
    max_retries: int = 2,
    max_verify: int = 50,
    progress_callback=None,
) -> dict[str, list[LLMJudgment]]:
    """对 top N 规则命中凭证调用 LLM 核实，返回 {rule_name: [LLMJudgment]}。"""

    client = make_openai_client(
        api_key=api_key,
        base_url=base_url,
        max_retries=3,
        timeout=_LLM_VERIFY_TIMEOUT_SECONDS,
    )
    all_judgments: dict[str, list[LLMJudgment]] = {}

    all_hits: list[tuple[str, RuleHit]] = []
    for rr in rule_results:
        for h in rr.hits:
            all_hits.append((rr.rule_name, h))
    all_hits.sort(key=lambda x: x[1].priority, reverse=True)

    seen_keys: set[str] = set()
    top_hits: list[tuple[str, RuleHit]] = []
    for rule_name, hit in all_hits:
        hit_key = hit.group_id or hit.voucher_id
        if hit_key not in seen_keys and len(top_hits) < max_verify:
            seen_keys.add(hit_key)
            top_hits.append((rule_name, hit))

    hits_by_rule: dict[str, list[RuleHit]] = {}
    for rule_name, hit in top_hits:
        hits_by_rule.setdefault(rule_name, []).append(hit)

    total_batches = sum((len(hits) + batch_size - 1) // batch_size for hits in hits_by_rule.values())
    batch_num = 0

    for rule_name, hits in hits_by_rule.items():
        rule_judgments: list[LLMJudgment] = []
        for i in range(0, len(hits), batch_size):
            batch = hits[i: i + batch_size]
            groups = _build_voucher_groups(df, batch)
            prompt = _build_prompt(rule_name, groups)

            response_text = None
            for attempt in range(max_retries + 1):
                try:
                    resp = client.chat.completions.create(
                        model=model,
                        max_tokens=2048,
                        messages=[
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": prompt},
                        ],
                        temperature=0.1,
                        timeout=_LLM_VERIFY_TIMEOUT_SECONDS,
                    )
                    response_text = resp.choices[0].message.content
                    break
                except (APITimeoutError, APIConnectionError):
                    if attempt < max_retries:
                        time.sleep(_LLM_RETRY_BACKOFF_BASE ** attempt)
                        continue
                    break
                except Exception:
                    logger.warning(
                        "LLM verify call failed (attempt %d/%d)",
                        attempt + 1,
                        max_retries + 1,
                        exc_info=True,
                    )
                    if attempt >= max_retries:
                        break
                    time.sleep(_LLM_RETRY_BACKOFF_BASE ** attempt)

            if response_text:
                try:
                    batch_judgments = _build_judgments_from_response(
                        response_text,
                        allowed_voucher_ids={str(hit.voucher_id) for hit in batch},
                    )
                except ValueError:
                    logger.warning(
                        "LLM verify response parse failed for rule %s",
                        rule_name,
                        exc_info=True,
                    )
                    batch_judgments = _fallback_judgments_for_hits(batch, "LLM 返回解析失败")
                rule_judgments.extend(batch_judgments)
            else:
                rule_judgments.extend(_fallback_judgments_for_hits(batch, "LLM 调用失败"))

            batch_num += 1
            if progress_callback:
                progress_callback(batch_num, total_batches)

        all_judgments[rule_name] = rule_judgments

    for rr in rule_results:
        if rr.rule_name not in all_judgments:
            all_judgments[rr.rule_name] = []

    return all_judgments


def _account_name(row: Any) -> Any:
    for col in ("科目名称", "总账科目：长文本", "总账科目：短文本"):
        if col in getattr(row, "index", []) or (hasattr(row, "get") and row.get(col) is not None):
            val = row.get(col) if hasattr(row, "get") else row[col]
            if val is not None and str(val).strip():
                return val
    return None


def _build_voucher_groups(df: pd.DataFrame, hits: list[RuleHit]) -> list[dict]:
    row_cache: dict[tuple[str, int], list[dict]] = {}

    def _cached_rows(voucher_id: str, max_rows: int) -> list[dict]:
        key = (voucher_id, max_rows)
        if key not in row_cache:
            row_cache[key] = _rows_for_voucher(df, voucher_id, max_rows=max_rows)
        return row_cache[key]

    groups = []
    for hit in hits:
        related_rows = []
        for related_vid in hit.related_voucher_ids:
            related_rows.append({
                "凭证编号": related_vid,
                "行项目": _cached_rows(related_vid, 6),
            })
        groups.append({
            "凭证编号": hit.voucher_id,
            "组合ID": hit.group_id or "",
            "关联凭证": list(hit.related_voucher_ids),
            "规则类型": hit.rule_type,
            "触发证据": hit.evidence,
            "组合证据": hit.relation_evidence,
            "主凭证行项目": _cached_rows(hit.voucher_id, 8),
            "关联凭证行项目": related_rows,
        })
    return groups


def _rows_for_voucher(df: pd.DataFrame, voucher_id: str, max_rows: int = 10) -> list[dict]:
    if df.empty or "凭证编号" not in df.columns:
        return []
    voucher_rows = df[df["凭证编号"].astype(str) == str(voucher_id)]
    rows_data = []
    for _, row in voucher_rows.head(max_rows).iterrows():
        rows_data.append({
            "过账日期": str(row.get("过账日期", ""))[:10],
            "凭证类型": row.get("凭证类型"),
            "文本": str(row.get("文本", ""))[:60],
            "总账科目": row.get("总账科目"),
            "科目名称": _account_name(row),
            "借贷": row.get("借/贷标识"),
            "金额": row.get("凭证货币价值"),
            "供应商": row.get("供应商科目：名称 1"),
            "客户": row.get("客户科目：姓名 1"),
            "用户名": row.get("用户名"),
        })
    return rows_data


def _build_prompt(rule_name: str, groups: list[dict]) -> str:
    context = ""
    for key, ctx in RULE_CONTEXTS.items():
        if key in rule_name:
            context = ctx
            break
    if not context:
        context = "请判断是否存在审计风险。"

    return f"""风险类型：{rule_name}
背景：{context}

以下凭证或凭证组合已被规则标记为疑似风险，请逐一判断是否构成真实审计风险。若存在“组合ID/关联凭证”，请基于整组收入、成本、日期和文本证据判断，不要只看主凭证。

{json.dumps(groups, ensure_ascii=False, indent=2, default=str)}

对每个凭证返回（仅返回 confirmed=true 的，全部排除则返回空数组[]）：
[
  {{
    "voucher_id": "主凭证编号字符串",
    "confirmed": true,
    "risk_level": "高"或"中",
    "reason": "1-2句判断理由",
    "audit_procedures": "1-2句建议核查步骤"
  }}
]"""


def _build_judgments_from_response(
    text: str,
    *,
    allowed_voucher_ids: set[str] | None = None,
) -> list[LLMJudgment]:
    data = parse_json_list(text)
    judgments = []
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, dict) or not item.get("confirmed", False):
            continue
        voucher_id = str(item.get("voucher_id", "")).strip()
        if not voucher_id or voucher_id in seen:
            continue
        if allowed_voucher_ids is not None and voucher_id not in allowed_voucher_ids:
            logger.warning("Ignoring LLM judgment for voucher outside requested batch: %s", voucher_id)
            continue
        seen.add(voucher_id)
        risk_level = str(item.get("risk_level", "中") or "中")
        if risk_level not in {"高", "中"}:
            risk_level = "中"
        judgments.append(
            LLMJudgment(
                voucher_id=voucher_id,
                confirmed=True,
                risk_level=risk_level,
                reason=str(item.get("reason", "") or ""),
                audit_procedures=str(item.get("audit_procedures", "") or ""),
            )
        )
    return judgments


def _fallback_judgments_for_hits(hits: list[Any], reason_prefix: str) -> list[LLMJudgment]:
    judgments = []
    for hit in hits:
        judgments.append(
            LLMJudgment(
                voucher_id=str(getattr(hit, "voucher_id", "")),
                confirmed=False,
                risk_level="待核验",
                reason=f"{reason_prefix}，未形成模型结论；疑点仍待人工复核（{str(getattr(hit, 'evidence', ''))[:60]}）",
                audit_procedures="需人工复核",
                source="fallback",
            )
        )
    return judgments
