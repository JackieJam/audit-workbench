"""LLM 逐凭证核实 — 规则命中样本的风险确认。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, Literal

from openai import APIConnectionError, APITimeoutError

from audit_engine.data_columns import VOUCHER_KEY_COLUMN
from audit_engine.json_utils import parse_json_list
from audit_engine.llm_client import make_openai_client
from audit_engine.llm_endpoint_policy import (
    assert_llm_endpoint_allowed,
    load_endpoint_allowlist,
)

logger = logging.getLogger(__name__)

RedactionMode = Literal["none", "pseudonym"]

if TYPE_CHECKING:
    import pandas as pd

    from audit_engine.rule_engine import RuleHit, RuleResult


# 三态判断：只有 CONFIRMED 可进入确认率分子；fallback / incomplete → PENDING_REVIEW
JUDGMENT_CONFIRMED = "confirmed"
JUDGMENT_REJECTED = "rejected"
JUDGMENT_PENDING_REVIEW = "pending_review"


@dataclass(frozen=True)
class LLMJudgment:
    voucher_id: str  # 展示用凭证编号；内部关联一律用 voucher_key
    confirmed: bool
    risk_level: str  # "高" | "中" | "待核验"
    reason: str
    audit_procedures: str
    source: str = "llm"  # "llm" | "fallback" | "llm_incomplete"
    status: str = ""  # confirmed | rejected | pending_review；空则由 source/confirmed 推导
    voucher_key: str = ""  # 主身份：公司代码 + 会计年度 + 凭证编号
    company_code: str = ""
    fiscal_year: str = ""

    def __post_init__(self) -> None:
        status = str(self.status or "").strip().lower()
        if status not in {JUDGMENT_CONFIRMED, JUDGMENT_REJECTED, JUDGMENT_PENDING_REVIEW}:
            if self.source in {"fallback", "llm_incomplete"}:
                status = JUDGMENT_PENDING_REVIEW
            elif self.confirmed:
                status = JUDGMENT_CONFIRMED
            else:
                status = JUDGMENT_REJECTED
        object.__setattr__(self, "status", status)
        # confirmed 与 status 对齐：pending/rejected 绝不能计入确认
        object.__setattr__(self, "confirmed", status == JUDGMENT_CONFIRMED)
        # 兼容旧 state：无 voucher_key 时降级为 voucher_id（仅展示级，不得跨主体合并）
        if not str(self.voucher_key or "").strip():
            object.__setattr__(self, "voucher_key", str(self.voucher_id or ""))


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


def _status_from_legacy(item: dict[str, Any]) -> str:
    explicit = str(item.get("status", "") or "").strip().lower()
    if explicit in {JUDGMENT_CONFIRMED, JUDGMENT_REJECTED, JUDGMENT_PENDING_REVIEW}:
        return explicit
    source = str(item.get("source", "llm") or "llm")
    if source in {"fallback", "llm_incomplete"}:
        return JUDGMENT_PENDING_REVIEW
    return JUDGMENT_CONFIRMED if item.get("confirmed", False) else JUDGMENT_REJECTED


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
            status = _status_from_legacy(item)
            voucher_id = str(item.get("voucher_id", "") or "")
            voucher_key = str(item.get("voucher_key", "") or voucher_id)
            parsed.append(
                LLMJudgment(
                    voucher_id=voucher_id,
                    confirmed=status == JUDGMENT_CONFIRMED,
                    risk_level=str(item.get("risk_level", "中") or "中"),
                    reason=str(item.get("reason", "") or ""),
                    audit_procedures=str(item.get("audit_procedures", "") or ""),
                    source=str(item.get("source", "llm") or "llm"),
                    status=status,
                    voucher_key=voucher_key,
                    company_code=str(item.get("company_code", "") or ""),
                    fiscal_year=str(item.get("fiscal_year", "") or ""),
                )
            )
        out[str(rule_name)] = parsed
    return out


def confirmation_rate(
    judgments: list[LLMJudgment],
    *,
    voucher_count: int | None = None,
) -> float | None:
    """确认率 = 模型确认 / 模型已决（确认+驳回）。

    pending_review / fallback / incomplete 不进分子也不进分母，避免 API 失败或漏答
    抬高/压低确认率，也避免「只返回 confirmed=true」时用 len(stored) 把确认率虚抬到 100%。
    """
    decided = [
        j for j in judgments
        if j.status in {JUDGMENT_CONFIRMED, JUDGMENT_REJECTED}
    ]
    denominator = voucher_count if voucher_count is not None else len(decided)
    if denominator <= 0:
        return None
    confirmed = sum(1 for j in decided if j.status == JUDGMENT_CONFIRMED)
    return confirmed / denominator


def summarize_judgments(all_judgments: dict[str, list[LLMJudgment]]) -> dict[str, Any]:
    flat = [j for items in all_judgments.values() for j in items]
    confirmed = [j for j in flat if j.status == JUDGMENT_CONFIRMED]
    rejected = [j for j in flat if j.status == JUDGMENT_REJECTED]
    pending = [j for j in flat if j.status == JUDGMENT_PENDING_REVIEW]
    return {
        "total": len(flat),
        "confirmed": len(confirmed),
        "rejected": len(rejected),
        "pending_review": len(pending),
        # unverified 仅表示尚未形成模型结论（待核验），不含明确驳回
        "unverified": len(pending),
        "high": sum(1 for j in confirmed if j.risk_level == "高"),
        "medium": sum(1 for j in confirmed if j.risk_level == "中"),
        "fallback": sum(1 for j in flat if j.source in {"fallback", "llm_incomplete"}),
        "confirmation_rate": confirmation_rate(flat),
        "rules": len(all_judgments),
    }


# LLM 外发字段清单——产品化治理边界（发送前可提示用户）
LLM_VERIFY_OUTBOUND_FIELDS: tuple[str, ...] = (
    "凭证编号",
    "过账日期",
    "凭证类型",
    "文本",
    "总账科目",
    "科目名称",
    "借贷",
    "金额",
    "供应商",
    "客户",
    "用户名",
)
_PSEUDONYM_FIELDS = ("供应商", "客户", "用户名", "凭证编号")
LLM_BOUNDARY_POLICY_VERSION = "2026-08-boundary-v1"


def default_redaction_mode() -> RedactionMode:
    mode = os.environ.get("AUDIT_WORKBENCH_LLM_REDACTION", "pseudonym").strip().lower()
    if mode not in {"none", "pseudonym"}:
        return "pseudonym"
    return mode  # type: ignore[return-value]


def _pseudo_token(kind: str, value: object) -> str:
    text = "" if value is None else str(value).strip()
    if not text or text.lower() in {"nan", "none", "未维护"}:
        return ""
    digest = hashlib.sha256(f"{kind}:{text}".encode()).hexdigest()[:10]
    return f"{kind}_{digest}"


def redact_llm_row(row: dict[str, Any], mode: RedactionMode) -> dict[str, Any]:
    if mode == "none":
        return row
    out = dict(row)
    for field in _PSEUDONYM_FIELDS:
        if field in out and out[field] not in (None, ""):
            out[field] = _pseudo_token(field, out[field])
    return out


def describe_llm_verify_boundary(
    base_url: str,
    model: str,
    *,
    redaction: RedactionMode | None = None,
) -> dict[str, Any]:
    """返回本次核实将外发的数据边界说明，供 UI/Agent 明示用户。"""
    mode = redaction or default_redaction_mode()
    fields = list(LLM_VERIFY_OUTBOUND_FIELDS)
    if mode == "pseudonym":
        note = "供应商/客户/用户名/凭证编号已伪名化（不可逆 hash）；「文本/摘要」仍为原文"
    else:
        note = "未脱敏：将发送原始供应商/客户/用户名/凭证编号；「文本/摘要」仍为原文"
    policy = load_endpoint_allowlist()
    return {
        "endpoint": base_url,
        "model": model,
        "fields": fields,
        "redaction": mode,
        "redaction_note": note,
        "plaintext_fields": ["文本"],
        "pseudonym_fields": list(_PSEUDONYM_FIELDS) if mode == "pseudonym" else [],
        "policy_version": LLM_BOUNDARY_POLICY_VERSION,
        "allowlist_enabled": bool(policy.get("enabled")),
        "allowlist_source": policy.get("source"),
        "warning": (
            f"将向模型服务 {base_url}（{model}）发送凭证级明细；{note}。"
            f"请确认该 endpoint 已纳入允许范围。"
        ),
    }


def _hit_identity_key(hit: Any) -> str:
    """内部去重/关联主键：group_id > voucher_key > voucher_id（仅降级）。"""
    group_id = str(getattr(hit, "group_id", None) or "").strip()
    if group_id:
        return group_id
    voucher_key = str(getattr(hit, "voucher_key", None) or "").strip()
    if voucher_key:
        return voucher_key
    return str(getattr(hit, "voucher_id", "") or "").strip()


def _hit_voucher_key(hit: Any) -> str:
    return str(getattr(hit, "voucher_key", None) or getattr(hit, "voucher_id", "") or "").strip()


def _hit_meta(hit: Any) -> dict[str, str]:
    year = getattr(hit, "year", None)
    return {
        "voucher_key": _hit_voucher_key(hit),
        "voucher_id": str(getattr(hit, "voucher_id", "") or "").strip(),
        "company_code": str(getattr(hit, "company_code", "") or "").strip(),
        "fiscal_year": "" if year in (None, "") else str(year),
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
    redaction: RedactionMode | None = None,
) -> dict[str, list[LLMJudgment]]:
    """对 top N 规则命中凭证调用 LLM 核实，返回 {rule_name: [LLMJudgment]}。"""

    assert_llm_endpoint_allowed(base_url)
    mode = redaction or default_redaction_mode()
    boundary = describe_llm_verify_boundary(base_url, model, redaction=mode)
    logger.warning("LLM verify data boundary: %s", boundary["warning"])
    if progress_callback:
        try:
            progress_callback({"phase": "boundary", **boundary})
        except TypeError:
            # 兼容只接受 (done, total) 的旧回调
            pass

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
        hit_key = _hit_identity_key(hit)
        if hit_key and hit_key not in seen_keys and len(top_hits) < max_verify:
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
            groups = _build_voucher_groups(df, batch, redaction=mode)
            # 伪名化凭证号 → 稳定身份，供响应回写
            display_to_identity = {
                str(g["凭证编号"]): {
                    "voucher_key": str(g.get("_raw_voucher_key") or ""),
                    "voucher_id": str(g.get("_raw_voucher_id") or g["凭证编号"]),
                    "company_code": str(g.get("_company_code") or ""),
                    "fiscal_year": str(g.get("_fiscal_year") or ""),
                }
                for g in groups
            }
            prompt_groups = [
                {
                    k: v for k, v in g.items()
                    if not str(k).startswith("_")
                }
                for g in groups
            ]
            prompt = _build_prompt(rule_name, prompt_groups)

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
                        batch_hits=batch,
                        allowed_voucher_keys={_hit_voucher_key(hit) for hit in batch},
                        display_to_identity=display_to_identity,
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


def _year_from_row(row: Any) -> str:
    for col in ("会计年度", "_year"):
        if hasattr(row, "get"):
            val = row.get(col)
        elif col in getattr(row, "index", []):
            val = row[col]
        else:
            continue
        if val is None or (isinstance(val, float) and str(val) == "nan"):
            continue
        text = str(val).strip()
        if text and text.lower() not in {"nan", "none", "<na>"}:
            try:
                return str(int(float(text)))
            except (TypeError, ValueError):
                return text
    return ""


def _build_voucher_groups(
    df: pd.DataFrame,
    hits: list[RuleHit],
    *,
    redaction: RedactionMode = "pseudonym",
) -> list[dict]:
    row_cache: dict[tuple[str, int], list[dict]] = {}

    def _cached_rows(voucher_key: str, max_rows: int) -> list[dict]:
        key = (voucher_key, max_rows)
        if key not in row_cache:
            row_cache[key] = _rows_for_voucher(
                df, voucher_key, max_rows=max_rows, redaction=redaction,
            )
        return row_cache[key]

    groups = []
    for hit in hits:
        meta = _hit_meta(hit)
        voucher_key = meta["voucher_key"]
        related_rows = []
        related_keys = list(getattr(hit, "related_voucher_keys", ()) or ())
        related_ids = list(getattr(hit, "related_voucher_ids", ()) or ())
        for index, related_vid in enumerate(related_ids):
            related_key = (
                str(related_keys[index]).strip()
                if index < len(related_keys) and str(related_keys[index]).strip()
                else str(related_vid)
            )
            display_related = (
                _pseudo_token("凭证编号", related_vid)
                if redaction == "pseudonym"
                else related_vid
            )
            related_rows.append({
                "凭证编号": display_related,
                "行项目": _cached_rows(related_key, 6),
            })
        display_vid = (
            _pseudo_token("凭证编号", meta["voucher_id"] or voucher_key)
            if redaction == "pseudonym"
            else (meta["voucher_id"] or voucher_key)
        )
        company = meta["company_code"]
        fiscal_year = meta["fiscal_year"]
        if (not company or not fiscal_year) and VOUCHER_KEY_COLUMN in df.columns and voucher_key:
            sample_rows = df[df[VOUCHER_KEY_COLUMN].astype(str) == voucher_key]
            if not sample_rows.empty:
                sample = sample_rows.iloc[0]
                if not company and "公司代码" in sample_rows.columns:
                    company = str(sample.get("公司代码") or "").strip()
                if not fiscal_year:
                    fiscal_year = _year_from_row(sample)

        groups.append({
            "凭证编号": display_vid,
            "_raw_voucher_key": voucher_key,
            "_raw_voucher_id": meta["voucher_id"] or voucher_key,
            "_company_code": company,
            "_fiscal_year": fiscal_year,
            "组合ID": hit.group_id or "",
            "关联凭证": [
                _pseudo_token("凭证编号", v) if redaction == "pseudonym" else v
                for v in related_ids
            ],
            "规则类型": hit.rule_type,
            "触发证据": hit.evidence,
            "组合证据": hit.relation_evidence,
            "主凭证行项目": _cached_rows(voucher_key, 8),
            "关联凭证行项目": related_rows,
        })
    return groups


def _rows_for_voucher(
    df: pd.DataFrame,
    voucher_key: str,
    max_rows: int = 10,
    *,
    redaction: RedactionMode = "pseudonym",
) -> list[dict]:
    """按稳定身份 `_voucher_key` 取行；缺列时才降级到展示编号（不应混年/混公司）。"""
    if df.empty or not str(voucher_key or "").strip():
        return []
    key = str(voucher_key)
    if VOUCHER_KEY_COLUMN in df.columns:
        voucher_rows = df[df[VOUCHER_KEY_COLUMN].astype(str) == key]
    elif "凭证编号" in df.columns:
        logger.warning(
            "LLM row lookup degraded to display voucher_id (missing %s): %s",
            VOUCHER_KEY_COLUMN,
            key,
        )
        voucher_rows = df[df["凭证编号"].astype(str) == key]
    else:
        return []
    rows_data = []
    for _, row in voucher_rows.head(max_rows).iterrows():
        raw = {
            "过账日期": str(row.get("过账日期", ""))[:10],
            "凭证类型": row.get("凭证类型"),
            "文本": str(row.get("文本", ""))[:60],
            "总账科目": row.get("总账科目"),
            "科目名称": _account_name(row),
            "借贷": row.get("借/贷标识"),
            "金额": row.get("_amount_raw", row.get("公司代码货币价值", row.get("凭证货币价值"))),
            "供应商": row.get("供应商科目：名称 1"),
            "客户": row.get("客户科目：姓名 1"),
            "用户名": row.get("用户名"),
        }
        rows_data.append(redact_llm_row(raw, redaction))
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
每个条目的「凭证编号」已是该核验实体的唯一展示标识；请原样回传，不要合并不同条目。

{json.dumps(groups, ensure_ascii=False, indent=2, default=str)}

对每个被核实的凭证都返回一条（confirmed=true 表示确认风险，false 表示排除）：
[
  {{
    "voucher_id": "主凭证编号字符串",
    "confirmed": true或false,
    "risk_level": "高"或"中"（confirmed=false 时可省略）,
    "reason": "1-2句判断理由",
    "audit_procedures": "1-2句建议核查步骤（排除时可写无需进一步程序）"
  }}
]"""


def _build_judgments_from_response(
    text: str,
    *,
    allowed_voucher_ids: set[str] | None = None,
    allowed_voucher_keys: set[str] | None = None,
    batch_hits: list[Any] | None = None,
    display_to_raw: dict[str, str] | None = None,
    display_to_identity: dict[str, dict[str, str]] | None = None,
) -> list[LLMJudgment]:
    """解析模型响应；batch 内未返回的凭证记为 pending_review（非 rejected）。"""
    data = parse_json_list(text)
    judgments: list[LLMJudgment] = []
    seen: set[str] = set()

    identity_map: dict[str, dict[str, str]] = dict(display_to_identity or {})
    if display_to_raw:
        for display, raw in display_to_raw.items():
            identity_map.setdefault(display, {
                "voucher_key": raw,
                "voucher_id": raw,
                "company_code": "",
                "fiscal_year": "",
            })

    # 兼容旧测试：allowed_voucher_ids 视作 allowed_voucher_keys
    allowed_keys = allowed_voucher_keys
    if allowed_keys is None and allowed_voucher_ids is not None:
        allowed_keys = set(allowed_voucher_ids)

    hit_by_key: dict[str, Any] = {}
    for hit in batch_hits or []:
        hit_by_key[_hit_voucher_key(hit)] = hit

    for item in data:
        if not isinstance(item, dict):
            continue
        displayed = str(item.get("voucher_id", "")).strip()
        identity = identity_map.get(displayed, {
            "voucher_key": displayed,
            "voucher_id": displayed,
            "company_code": "",
            "fiscal_year": "",
        })
        voucher_key = str(identity.get("voucher_key") or displayed).strip()
        voucher_id = str(identity.get("voucher_id") or displayed).strip()
        if not voucher_key or voucher_key in seen:
            continue
        if allowed_keys is not None and voucher_key not in allowed_keys:
            logger.warning(
                "Ignoring LLM judgment for voucher outside requested batch: %s",
                voucher_key,
            )
            continue
        seen.add(voucher_key)
        hit = hit_by_key.get(voucher_key)
        meta = _hit_meta(hit) if hit is not None else identity
        if item.get("confirmed", False):
            risk_level = str(item.get("risk_level", "中") or "中")
            if risk_level not in {"高", "中"}:
                risk_level = "中"
            judgments.append(
                LLMJudgment(
                    voucher_id=voucher_id or meta["voucher_id"],
                    confirmed=True,
                    risk_level=risk_level,
                    reason=str(item.get("reason", "") or ""),
                    audit_procedures=str(item.get("audit_procedures", "") or ""),
                    source="llm",
                    status=JUDGMENT_CONFIRMED,
                    voucher_key=voucher_key,
                    company_code=str(meta.get("company_code") or identity.get("company_code") or ""),
                    fiscal_year=str(meta.get("fiscal_year") or identity.get("fiscal_year") or ""),
                )
            )
        else:
            judgments.append(
                LLMJudgment(
                    voucher_id=voucher_id or meta["voucher_id"],
                    confirmed=False,
                    risk_level="中",
                    reason=str(item.get("reason", "") or "模型排除：不构成审计风险"),
                    audit_procedures=str(item.get("audit_procedures", "") or ""),
                    source="llm",
                    status=JUDGMENT_REJECTED,
                    voucher_key=voucher_key,
                    company_code=str(meta.get("company_code") or identity.get("company_code") or ""),
                    fiscal_year=str(meta.get("fiscal_year") or identity.get("fiscal_year") or ""),
                )
            )

    # 模型省略的 batch 凭证 → pending_review（不是 rejected）
    pending_keys = allowed_keys if allowed_keys is not None else set(hit_by_key)
    for voucher_key in sorted(pending_keys):
        if not voucher_key or voucher_key in seen:
            continue
        hit = hit_by_key.get(voucher_key)
        meta = _hit_meta(hit) if hit is not None else {
            "voucher_key": voucher_key,
            "voucher_id": voucher_key,
            "company_code": "",
            "fiscal_year": "",
        }
        judgments.append(
            LLMJudgment(
                voucher_id=meta["voucher_id"] or voucher_key,
                confirmed=False,
                risk_level="待核验",
                reason="模型未返回该凭证结论，待人工复核",
                audit_procedures="需人工复核（模型响应不完整）",
                source="llm_incomplete",
                status=JUDGMENT_PENDING_REVIEW,
                voucher_key=voucher_key,
                company_code=meta.get("company_code", ""),
                fiscal_year=meta.get("fiscal_year", ""),
            )
        )
    return judgments


def _fallback_judgments_for_hits(hits: list[Any], reason_prefix: str) -> list[LLMJudgment]:
    judgments = []
    for hit in hits:
        meta = _hit_meta(hit)
        judgments.append(
            LLMJudgment(
                voucher_id=meta["voucher_id"] or meta["voucher_key"],
                confirmed=False,
                risk_level="待核验",
                reason=(
                    f"{reason_prefix}，未形成模型结论；疑点仍待人工复核"
                    f"（{str(getattr(hit, 'evidence', ''))[:60]}）"
                ),
                audit_procedures="需人工复核",
                source="fallback",
                status=JUDGMENT_PENDING_REVIEW,
                voucher_key=meta["voucher_key"],
                company_code=meta.get("company_code", ""),
                fiscal_year=meta.get("fiscal_year", ""),
            )
        )
    return judgments
