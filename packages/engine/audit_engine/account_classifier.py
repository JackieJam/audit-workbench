"""
基于科目名称的自动分类器。

每家被审单位科目体系不同，但「主营业务收入」「制造费用-人工」「应付账款-暂估」
这类科目名称的语义是稳定的。比起让用户维护一组组前缀清单，直接按
科目名称做关键词匹配更直观。

匹配规则：
- 单向、按优先级顺序，先命中先终止；
- 名称为空 / 不命中任何规则 → "未分类"，相关分析自动跳过；
- 用户可针对个别科目编号在 UI 中手动覆盖（per-project，跟随项目状态保存）。

不依赖 streamlit；脱离 UI 也能调用 auto_classify 和 classify_dataframe。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

# ── 类别常量 ────────────────────────────────────────────────
# 字符串值与 UI 下拉、各分析模块比较保持一致。

CAT_REVENUE = "收入"
CAT_COST = "成本"
CAT_EXPENSE = "费用"
CAT_RD_EXPENSE = "研发费用"
CAT_FINANCIAL_EXPENSE = "财务费用"
CAT_TAX_SURCHARGE = "税金及附加"
CAT_INVESTMENT_INCOME = "投资收益"
CAT_FAIR_VALUE_CHANGE = "公允价值变动损益"
CAT_OTHER_INCOME = "其他收益"
CAT_ASSET_DISPOSAL = "资产处置收益"
CAT_CREDIT_IMPAIRMENT = "信用减值损失"
CAT_ASSET_IMPAIRMENT = "资产减值损失"
CAT_INCOME_TAX = "所得税费用"
CAT_NON_OPERATING_INCOME = "营业外收入"
CAT_NON_OPERATING_EXPENSE = "营业外支出"
CAT_PRIOR_PERIOD_ADJUSTMENT = "以前年度损益调整"
CAT_MANUFACTURING_COST = "制造成本"
CAT_COST_VARIANCE = "成本差异"
CAT_AR = "应收"
CAT_OTHER_RECEIVABLE = "其他应收"
CAT_AP = "应付"
CAT_AP_ACCRUAL = "应付暂估"
CAT_OTHER_PAYABLE = "其他应付"
# ── 资产负债表类别（流量分析用，非余额表）──
CAT_CASH = "货币资金"
CAT_INVENTORY = "存货"
CAT_CONTRACT_ASSET = "合同资产"
CAT_CONTRACT_COST = "合同成本"
CAT_BAD_DEBT_ALLOWANCE = "坏账准备"
CAT_FIXED_ASSET = "固定资产"
CAT_ACCUMULATED_DEPRECIATION = "累计折旧"
CAT_RIGHT_OF_USE_ASSET = "使用权资产"
CAT_INTANGIBLE_ASSET = "无形资产"
CAT_ACCUMULATED_AMORTIZATION = "累计摊销"
CAT_DEFERRED_TAX_ASSET = "递延所得税资产"
CAT_DERIVATIVE_ASSET = "衍生金融资产"
CAT_OTHER_CURRENT_ASSET = "其他流动资产"
CAT_LONG_TERM_PREPAID = "长期待摊费用"
CAT_PENDING_PROPERTY = "待处理财产损溢"
CAT_PREPAY = "预付账款"
CAT_ADVANCE_RECEIPT = "预收账款"
CAT_TAX_PAYABLE = "应交税费"
CAT_LOAN = "借款"
CAT_LEASE_LIABILITY = "租赁负债"
CAT_UNRECOGNIZED_FINANCE_CHARGE = "未确认融资费用"
CAT_DEFERRED_TAX_LIABILITY = "递延所得税负债"
CAT_DERIVATIVE_LIABILITY = "衍生金融负债"
CAT_PROVISION = "预计负债"
CAT_EQUITY = "权益"
CAT_SPECIAL_RESERVE = "专项储备"
CAT_EMPLOYEE_PAYABLE = "应付职工薪酬"
CAT_BOND_PAYABLE = "应付债券"
CAT_DIVIDEND_PAYABLE = "应付股利"
CAT_TECHNICAL_CLEARING = "技术清算"
CAT_UNCATEGORIZED = "未分类"

ALL_CATEGORIES: tuple[str, ...] = (
    CAT_REVENUE,
    CAT_COST,
    CAT_EXPENSE,
    CAT_RD_EXPENSE,
    CAT_FINANCIAL_EXPENSE,
    CAT_TAX_SURCHARGE,
    CAT_INVESTMENT_INCOME,
    CAT_FAIR_VALUE_CHANGE,
    CAT_OTHER_INCOME,
    CAT_ASSET_DISPOSAL,
    CAT_CREDIT_IMPAIRMENT,
    CAT_ASSET_IMPAIRMENT,
    CAT_INCOME_TAX,
    CAT_NON_OPERATING_INCOME,
    CAT_NON_OPERATING_EXPENSE,
    CAT_PRIOR_PERIOD_ADJUSTMENT,
    CAT_MANUFACTURING_COST,
    CAT_COST_VARIANCE,
    CAT_AR,
    CAT_OTHER_RECEIVABLE,
    CAT_AP,
    CAT_AP_ACCRUAL,
    CAT_OTHER_PAYABLE,
    CAT_CASH,
    CAT_INVENTORY,
    CAT_CONTRACT_ASSET,
    CAT_CONTRACT_COST,
    CAT_BAD_DEBT_ALLOWANCE,
    CAT_FIXED_ASSET,
    CAT_ACCUMULATED_DEPRECIATION,
    CAT_RIGHT_OF_USE_ASSET,
    CAT_INTANGIBLE_ASSET,
    CAT_ACCUMULATED_AMORTIZATION,
    CAT_DEFERRED_TAX_ASSET,
    CAT_DERIVATIVE_ASSET,
    CAT_OTHER_CURRENT_ASSET,
    CAT_LONG_TERM_PREPAID,
    CAT_PENDING_PROPERTY,
    CAT_PREPAY,
    CAT_ADVANCE_RECEIPT,
    CAT_TAX_PAYABLE,
    CAT_LOAN,
    CAT_LEASE_LIABILITY,
    CAT_UNRECOGNIZED_FINANCE_CHARGE,
    CAT_DEFERRED_TAX_LIABILITY,
    CAT_DERIVATIVE_LIABILITY,
    CAT_PROVISION,
    CAT_EQUITY,
    CAT_SPECIAL_RESERVE,
    CAT_EMPLOYEE_PAYABLE,
    CAT_BOND_PAYABLE,
    CAT_DIVIDEND_PAYABLE,
    CAT_TECHNICAL_CLEARING,
    CAT_UNCATEGORIZED,
)

OTHER_PNL_CATEGORIES: tuple[str, ...] = (
    CAT_INVESTMENT_INCOME,
    CAT_FAIR_VALUE_CHANGE,
    CAT_OTHER_INCOME,
    CAT_ASSET_DISPOSAL,
    CAT_CREDIT_IMPAIRMENT,
    CAT_ASSET_IMPAIRMENT,
    CAT_INCOME_TAX,
    CAT_NON_OPERATING_INCOME,
    CAT_NON_OPERATING_EXPENSE,
)

# 这些类别由明确科目前缀或不易歧义的会计语义识别。系统口径优先于历史人工覆盖，
# 避免把信用减值、递延税、衍生工具等重新塞回宽泛的应收/费用类别。
SYSTEM_PROTECTED_CATEGORIES: frozenset[str] = frozenset({
    *OTHER_PNL_CATEGORIES,
    CAT_PRIOR_PERIOD_ADJUSTMENT,
    CAT_MANUFACTURING_COST,
    CAT_COST_VARIANCE,
    CAT_CONTRACT_ASSET,
    CAT_CONTRACT_COST,
    CAT_BAD_DEBT_ALLOWANCE,
    CAT_ACCUMULATED_DEPRECIATION,
    CAT_RIGHT_OF_USE_ASSET,
    CAT_INTANGIBLE_ASSET,
    CAT_ACCUMULATED_AMORTIZATION,
    CAT_DEFERRED_TAX_ASSET,
    CAT_DERIVATIVE_ASSET,
    CAT_OTHER_CURRENT_ASSET,
    CAT_LONG_TERM_PREPAID,
    CAT_PENDING_PROPERTY,
    CAT_LEASE_LIABILITY,
    CAT_UNRECOGNIZED_FINANCE_CHARGE,
    CAT_DEFERRED_TAX_LIABILITY,
    CAT_DERIVATIVE_LIABILITY,
    CAT_PROVISION,
    CAT_EQUITY,
    CAT_SPECIAL_RESERVE,
    CAT_TECHNICAL_CLEARING,
})

# ── 资产负债页：类别 -> 大类（资产/负债/权益）──
# 决定月度净变动的符号约定：资产借增（净=借-贷），负债/权益贷增（净=贷-借）。
BALANCE_SHEET_SIDE: dict[str, str] = {
    CAT_CASH: "资产",
    CAT_AR: "资产",
    CAT_OTHER_RECEIVABLE: "资产",
    CAT_PREPAY: "资产",
    CAT_INVENTORY: "资产",
    CAT_CONTRACT_ASSET: "资产",
    CAT_CONTRACT_COST: "资产",
    CAT_BAD_DEBT_ALLOWANCE: "资产抵减",
    CAT_FIXED_ASSET: "资产",
    CAT_ACCUMULATED_DEPRECIATION: "资产抵减",
    CAT_RIGHT_OF_USE_ASSET: "资产",
    CAT_INTANGIBLE_ASSET: "资产",
    CAT_ACCUMULATED_AMORTIZATION: "资产抵减",
    CAT_DEFERRED_TAX_ASSET: "资产",
    CAT_DERIVATIVE_ASSET: "资产",
    CAT_OTHER_CURRENT_ASSET: "资产",
    CAT_LONG_TERM_PREPAID: "资产",
    CAT_PENDING_PROPERTY: "资产",
    CAT_TECHNICAL_CLEARING: "清算",
    CAT_AP: "负债",
    CAT_AP_ACCRUAL: "负债",
    CAT_OTHER_PAYABLE: "负债",
    CAT_ADVANCE_RECEIPT: "负债",
    CAT_TAX_PAYABLE: "负债",
    CAT_LOAN: "负债",
    CAT_LEASE_LIABILITY: "负债",
    CAT_UNRECOGNIZED_FINANCE_CHARGE: "负债抵减",
    CAT_DEFERRED_TAX_LIABILITY: "负债",
    CAT_DERIVATIVE_LIABILITY: "负债",
    CAT_PROVISION: "负债",
    CAT_EMPLOYEE_PAYABLE: "负债",
    CAT_BOND_PAYABLE: "负债",
    CAT_DIVIDEND_PAYABLE: "负债",
    CAT_EQUITY: "权益",
    CAT_SPECIAL_RESERVE: "权益",
}

# 资产负债页可选类别：往来类优先（默认打开更有审计信息量），货币资金靠后。
BALANCE_SHEET_CATEGORIES: tuple[str, ...] = (
    CAT_AR, CAT_OTHER_RECEIVABLE, CAT_PREPAY, CAT_CONTRACT_ASSET, CAT_CONTRACT_COST,
    CAT_BAD_DEBT_ALLOWANCE,
    CAT_AP, CAT_AP_ACCRUAL, CAT_OTHER_PAYABLE, CAT_ADVANCE_RECEIPT,
    CAT_TAX_PAYABLE, CAT_EMPLOYEE_PAYABLE, CAT_BOND_PAYABLE, CAT_DIVIDEND_PAYABLE, CAT_LOAN,
    CAT_LEASE_LIABILITY, CAT_UNRECOGNIZED_FINANCE_CHARGE, CAT_PROVISION,
    CAT_DERIVATIVE_LIABILITY, CAT_DEFERRED_TAX_LIABILITY,
    CAT_INVENTORY, CAT_FIXED_ASSET, CAT_ACCUMULATED_DEPRECIATION, CAT_RIGHT_OF_USE_ASSET,
    CAT_INTANGIBLE_ASSET, CAT_ACCUMULATED_AMORTIZATION,
    CAT_DERIVATIVE_ASSET, CAT_DEFERRED_TAX_ASSET, CAT_OTHER_CURRENT_ASSET,
    CAT_LONG_TERM_PREPAID,
    CAT_PENDING_PROPERTY, CAT_TECHNICAL_CLEARING,
    CAT_CASH,
    CAT_EQUITY, CAT_SPECIAL_RESERVE,
)


# ── 优先级匹配规则 ──────────────────────────────────────────
# 每条规则：(类别, 必含关键词列表)。
# 单向匹配——按本表顺序，第一个命中的关键词决定类别。
# 「应付暂估」放最前避免被「应付」吞掉；「其他应收/应付」放在「应收/应付」之前。

@dataclass(frozen=True)
class _Rule:
    category: str
    keywords: tuple[str, ...]


# SAP 标准科目前缀兜底（名称缺失或仅写「应付账款」时仍能归类）。
# 仅在名称分类为未分类，或明确需要升级（如 220204）时使用。
_PREFIX_CATEGORY_EXACT4: dict[str, str] = {
    "6001": CAT_REVENUE,
    "6051": CAT_REVENUE,
    "6401": CAT_COST,
    "6402": CAT_COST,
    "6403": CAT_TAX_SURCHARGE,
    "6601": CAT_EXPENSE,
    "6602": CAT_EXPENSE,
}
_PREFIX_FORCE_CATEGORY_EXACT4: dict[str, str] = {
    "5001": CAT_MANUFACTURING_COST,
    "6101": CAT_FAIR_VALUE_CHANGE,
    "6603": CAT_FINANCIAL_EXPENSE,
    "6604": CAT_RD_EXPENSE,
    "6111": CAT_INVESTMENT_INCOME,
    "6115": CAT_ASSET_DISPOSAL,
    "6117": CAT_OTHER_INCOME,
    "6301": CAT_NON_OPERATING_INCOME,
    "6701": CAT_ASSET_IMPAIRMENT,
    "6702": CAT_CREDIT_IMPAIRMENT,
    "6711": CAT_NON_OPERATING_EXPENSE,
    "6801": CAT_INCOME_TAX,
    "6901": CAT_PRIOR_PERIOD_ADJUSTMENT,
    "6910": CAT_EXPENSE,
    "6920": CAT_RD_EXPENSE,
    "6990": CAT_COST_VARIANCE,
    "8142": CAT_MANUFACTURING_COST,
    "8143": CAT_MANUFACTURING_COST,
    "1464": CAT_CONTRACT_ASSET,
    "1602": CAT_ACCUMULATED_DEPRECIATION,
    "1701": CAT_INTANGIBLE_ASSET,
    "1702": CAT_ACCUMULATED_AMORTIZATION,
    "1703": CAT_RIGHT_OF_USE_ASSET,
    "1801": CAT_LONG_TERM_PREPAID,
    "1811": CAT_DEFERRED_TAX_ASSET,
    "1901": CAT_PENDING_PROPERTY,
    "2102": CAT_DERIVATIVE_LIABILITY,
    "2712": CAT_UNRECOGNIZED_FINANCE_CHARGE,
    "2801": CAT_PROVISION,
    "4104": CAT_EQUITY,
}
_AP_ACCRUAL_CODE_PREFIX = "220204"


_PRIORITY_RULES: tuple[_Rule, ...] = (
    # ── 独立损益 / 制造结转（必须早于“应收/固定资产/费用”等裸词）──
    _Rule(CAT_CREDIT_IMPAIRMENT, ("信用减值损失",)),
    _Rule(CAT_ASSET_IMPAIRMENT, ("资产减值损失",)),
    _Rule(CAT_INCOME_TAX, ("所得税费用",)),
    _Rule(CAT_DERIVATIVE_ASSET, ("衍生金融资产", "衍生工具资产")),
    _Rule(CAT_DERIVATIVE_LIABILITY, ("衍生金融负债", "衍生工具负债")),
    _Rule(CAT_FAIR_VALUE_CHANGE, ("公允价值变动",)),
    _Rule(CAT_ASSET_DISPOSAL, ("资产处置收益", "资产处置损益")),
    _Rule(CAT_OTHER_INCOME, ("其他收益",)),
    _Rule(CAT_PRIOR_PERIOD_ADJUSTMENT, ("以前年度损益调整",)),
    _Rule(CAT_COST_VARIANCE, (
        "差异-差异结转",
        "差异-调整结转",
        "成本差异",
        "采购价格差异",
        "生产成本差异",
        "价格重估差异",
        "在制品差异",
    )),
    _Rule(CAT_FINANCIAL_EXPENSE, ("财务费用", "汇兑损益")),
    _Rule(CAT_RD_EXPENSE, ("研发费用",)),
    _Rule(CAT_EXPENSE, ("固定资产折旧费", "使用权折旧费", "长期待摊费用摊销")),
    _Rule(CAT_MANUFACTURING_COST, ("生产成本", "制造费用")),
    _Rule(CAT_CONTRACT_COST, ("合同履约成本", "合同取得成本", "劳务成本")),
    _Rule(CAT_TECHNICAL_CLEARING, ("技术清算",)),
    # ── 具体资产负债项目 ──
    _Rule(CAT_CONTRACT_ASSET, ("合同资产",)),
    _Rule(CAT_BAD_DEBT_ALLOWANCE, ("坏账准备",)),
    _Rule(CAT_ACCUMULATED_DEPRECIATION, ("累计折旧",)),
    _Rule(CAT_RIGHT_OF_USE_ASSET, ("使用权资产",)),
    _Rule(CAT_LEASE_LIABILITY, ("租赁负债",)),
    _Rule(CAT_UNRECOGNIZED_FINANCE_CHARGE, ("未确认融资费用",)),
    _Rule(CAT_DEFERRED_TAX_ASSET, ("递延所得税资产",)),
    _Rule(CAT_DEFERRED_TAX_LIABILITY, ("递延所得税负债",)),
    _Rule(CAT_ACCUMULATED_AMORTIZATION, ("累计摊销",)),
    _Rule(CAT_INTANGIBLE_ASSET, ("无形资产",)),
    _Rule(CAT_PROVISION, ("预计负债",)),
    _Rule(CAT_OTHER_CURRENT_ASSET, ("其他流动资产",)),
    _Rule(CAT_LONG_TERM_PREPAID, ("长期待摊费用",)),
    _Rule(CAT_PENDING_PROPERTY, ("待处理财产损溢", "待处理财产损益")),
    _Rule(CAT_SPECIAL_RESERVE, ("专项储备",)),
    _Rule(CAT_EQUITY, ("利润分配", "未分配利润", "本年利润", "资本公积", "盈余公积")),
    _Rule(CAT_AP_ACCRUAL, ("暂估", "GR/IR", "GRIR")),
    _Rule(CAT_OTHER_RECEIVABLE, ("其他应收",)),
    _Rule(CAT_OTHER_PAYABLE, ("其他应付",)),
    # ── 资产负债类（关键词具体，放在通用损益规则之前，避免被"收入/成本/费用"误吞）──
    _Rule(CAT_ADVANCE_RECEIPT, ("预收账款", "合同负债", "预收")),
    _Rule(CAT_PREPAY, ("预付账款", "预付")),
    _Rule(CAT_TAX_PAYABLE, ("应交税费", "应交税金", "应缴税费")),
    _Rule(CAT_EMPLOYEE_PAYABLE, ("应付职工薪酬", "应付工资", "应付福利费")),
    _Rule(CAT_BOND_PAYABLE, ("应付债券",)),
    _Rule(CAT_DIVIDEND_PAYABLE, ("应付股利", "应付利润")),
    _Rule(CAT_AR, ("应收",)),
    _Rule(CAT_AP, ("应付",)),
    # 不用裸「现金」：避免「销售费用-现金折扣」等被误判为货币资金
    _Rule(CAT_CASH, ("货币资金", "银行存款", "库存现金", "现金等价物")),
    _Rule(CAT_INVENTORY, ("存货", "原材料", "库存商品", "周转材料", "在产品", "产成品",
                          "发出商品", "委托加工", "包装物", "低值易耗")),
    _Rule(CAT_FIXED_ASSET, ("固定资产", "在建工程", "工程物资")),
    _Rule(CAT_LOAN, ("短期借款", "长期借款", "借款")),
    _Rule(CAT_EQUITY, ("实收资本", "股本", "资本公积", "盈余公积", "未分配利润",
                       "利润分配", "本年利润")),
    _Rule(CAT_TAX_SURCHARGE, ("税金及附加",)),
    _Rule(CAT_RD_EXPENSE, ("研发",)),
    _Rule(CAT_FINANCIAL_EXPENSE, ("财务费用", "汇兑损益")),
    _Rule(CAT_INVESTMENT_INCOME, ("投资收益",)),
    _Rule(CAT_NON_OPERATING_INCOME, ("营业外收入",)),
    _Rule(CAT_NON_OPERATING_EXPENSE, ("营业外支出",)),
    _Rule(CAT_REVENUE, ("收入",)),
    _Rule(CAT_COST, ("成本",)),
    _Rule(CAT_EXPENSE, ("费用",)),
)


# ─────────────────────────────────────────────
# 公开 API
# ─────────────────────────────────────────────


def auto_classify(account_name: str | None) -> str:
    """按科目名称自动分类。

    - 名称为空、None、NaN → "未分类"
    - 生产成本等排除项 → "未分类"（避免污染毛利与期间费用）
    - 投资收益、营业外收支 → 各自独立分类（不进入经营收入/成本/费用）
    - 不命中任一关键词 → "未分类"
    """
    if account_name is None:
        return CAT_UNCATEGORIZED
    name = str(account_name).strip()
    if not name or name.lower() == "nan":
        return CAT_UNCATEGORIZED
    for rule in _PRIORITY_RULES:
        for keyword in rule.keywords:
            if keyword in name:
                return rule.category
    return CAT_UNCATEGORIZED


def apply_prefix_category(account_code: object, current_category: str) -> str:
    """科目前缀兜底 / 升级。

    - 220204* 一律视为应付暂估（即使名称只写「应付账款」）
    - 5001/8142/8143 强制进入制造成本独立口径（不进毛利与期间费用）
    - 明确标准前缀强制进入独立口径，防止历史人工映射污染通用图表
    - 标准损益前缀仅在当前为「未分类」时补全，不覆盖名称已判定的类别
    """
    if account_code is None or (isinstance(account_code, float) and pd.isna(account_code)):
        code = ""
    else:
        code = str(account_code).strip()
        if not code or code.lower() == "nan":
            code = ""
    if code.startswith(_AP_ACCRUAL_CODE_PREFIX):
        return CAT_AP_ACCRUAL
    acct4 = code[:4]
    if acct4 in _PREFIX_FORCE_CATEGORY_EXACT4:
        return _PREFIX_FORCE_CATEGORY_EXACT4[acct4]
    if current_category != CAT_UNCATEGORIZED:
        return current_category
    return _PREFIX_CATEGORY_EXACT4.get(acct4, current_category)


def uncategorized_reason(account_code: object, account_name: object) -> str:
    """解释科目为何仍为未分类，供数据质量复核使用。"""
    code = _as_text(account_code)
    name = _as_text(account_name)
    if not code and not name:
        return "missing_identity"
    return "needs_mapping"


# 毛利成本口径排除：这些科目进存货/在产品，结转销售时才进 6401/6402。
_GROSS_MARGIN_COST_NAME_EXCLUSIONS: tuple[str, ...] = (
    "生产成本",
    "制造费用",
    "合同履约成本",
    "合同取得成本",
    "劳务成本",
)
_OPERATING_COGS_PREFIXES: frozenset[str] = frozenset({"6401", "6402"})


def _as_text(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    return "" if not text or text.lower() == "nan" else text


def is_inventory_manufacturing_account(account_code: object, account_name: object = "") -> bool:
    """生产成本/制造费用等：归集进存货，不属于毛利「净成本」。"""
    code = _as_text(account_code)
    name = _as_text(account_name)
    if code[:4] in {"5001", "8142", "8143"}:
        return True
    return any(marker in name for marker in _GROSS_MARGIN_COST_NAME_EXCLUSIONS)


def operating_revenue_mask(work: pd.DataFrame) -> pd.Series:
    """经营收入口径：收入类，排除营业外收入。"""
    cat = work["_acct_category"].eq(CAT_REVENUE)
    name = work["_account_name"].astype(str) if "_account_name" in work.columns else pd.Series("", index=work.index)
    return cat & ~name.str.contains("营业外", na=False)


def operating_cost_mask(work: pd.DataFrame) -> pd.Series:
    """毛利成本口径：主营/其他业务成本（6401/6402），不含生产成本等存货科目。

    财务路径：生产成本 → 在产品/库存商品 → 销售时结转主营业务成本。
    收入成本分析只取最后一步（COGS），避免把存货归集误当毛利成本。
    """
    if "_acct4" in work.columns:
        code4 = work["_acct4"].astype(str)
    elif "_acct" in work.columns:
        code4 = work["_acct"].astype(str).str[:4]
    else:
        code4 = work["总账科目"].astype(str).str[:4]
    name = work["_account_name"].astype(str) if "_account_name" in work.columns else pd.Series("", index=work.index)
    by_prefix = code4.isin(_OPERATING_COGS_PREFIXES)
    excluded = work["_acct_category"].isin(
        {CAT_MANUFACTURING_COST, CAT_COST_VARIANCE, CAT_CONTRACT_COST}
    ) | name.apply(
        lambda n: any(m in n for m in _GROSS_MARGIN_COST_NAME_EXCLUSIONS)
    ).astype(bool)
    by_cat = work["_acct_category"].eq(CAT_COST)
    return (by_prefix | by_cat) & ~excluded


def manufacturing_cost_mask(work: pd.DataFrame) -> pd.Series:
    """制造归集口径：生产成本和制造费用，不进入经营毛利。"""
    code4 = work.get("_acct4", pd.Series("", index=work.index)).astype(str)
    category = work.get("_acct_category", pd.Series("", index=work.index)).astype(str)
    name = work.get("_account_name", pd.Series("", index=work.index)).astype(str)
    return (
        category.eq(CAT_MANUFACTURING_COST)
        | code4.isin({"5001", "8142", "8143"})
        | name.str.contains("生产成本|制造费用", na=False)
    )


def cost_variance_mask(work: pd.DataFrame) -> pd.Series:
    """标准成本与实际成本差异及其结转口径。"""
    code4 = work.get("_acct4", pd.Series("", index=work.index)).astype(str)
    category = work.get("_acct_category", pd.Series("", index=work.index)).astype(str)
    name = work.get("_account_name", pd.Series("", index=work.index)).astype(str)
    return (
        category.eq(CAT_COST_VARIANCE)
        | code4.eq("6990")
        | name.str.contains("差异-差异结转|差异-调整结转|成本差异", na=False)
    )


def is_system_protected_category(category: object) -> bool:
    return str(category or "").strip() in SYSTEM_PROTECTED_CATEGORIES


def investment_income_mask(work: pd.DataFrame) -> pd.Series:
    """投资收益口径：标准前缀优先，兼容非标准科目名称。"""
    code4 = work.get("_acct4", pd.Series("", index=work.index)).astype(str)
    name = work.get("_account_name", pd.Series("", index=work.index)).astype(str)
    category = work.get("_acct_category", pd.Series("", index=work.index)).astype(str)
    return (
        category.eq(CAT_INVESTMENT_INCOME)
        | code4.eq("6111")
        | name.str.contains("投资收益", na=False)
    )


def _category_name_mask(
    work: pd.DataFrame,
    category_name: str,
    name_pattern: str,
) -> pd.Series:
    category = work.get("_acct_category", pd.Series("", index=work.index)).astype(str)
    name = work.get("_account_name", pd.Series("", index=work.index)).astype(str)
    return category.eq(category_name) | name.str.contains(name_pattern, na=False)


def fair_value_change_mask(work: pd.DataFrame) -> pd.Series:
    return _category_name_mask(work, CAT_FAIR_VALUE_CHANGE, "公允价值变动")


def other_income_mask(work: pd.DataFrame) -> pd.Series:
    return _category_name_mask(work, CAT_OTHER_INCOME, "其他收益")


def asset_disposal_mask(work: pd.DataFrame) -> pd.Series:
    return _category_name_mask(work, CAT_ASSET_DISPOSAL, "资产处置收益|资产处置损益")


def credit_impairment_mask(work: pd.DataFrame) -> pd.Series:
    return _category_name_mask(work, CAT_CREDIT_IMPAIRMENT, "信用减值损失")


def asset_impairment_mask(work: pd.DataFrame) -> pd.Series:
    return _category_name_mask(work, CAT_ASSET_IMPAIRMENT, "资产减值损失")


def income_tax_expense_mask(work: pd.DataFrame) -> pd.Series:
    return _category_name_mask(work, CAT_INCOME_TAX, "所得税费用")


def non_operating_income_mask(work: pd.DataFrame) -> pd.Series:
    """营业外收入口径。"""
    code4 = work.get("_acct4", pd.Series("", index=work.index)).astype(str)
    name = work.get("_account_name", pd.Series("", index=work.index)).astype(str)
    category = work.get("_acct_category", pd.Series("", index=work.index)).astype(str)
    return (
        category.eq(CAT_NON_OPERATING_INCOME)
        | code4.eq("6301")
        | name.str.contains("营业外收入", na=False)
    )


def non_operating_expense_mask(work: pd.DataFrame) -> pd.Series:
    """营业外支出口径。"""
    code4 = work.get("_acct4", pd.Series("", index=work.index)).astype(str)
    name = work.get("_account_name", pd.Series("", index=work.index)).astype(str)
    category = work.get("_acct_category", pd.Series("", index=work.index)).astype(str)
    return (
        category.eq(CAT_NON_OPERATING_EXPENSE)
        | code4.eq("6711")
        | name.str.contains("营业外支出", na=False)
    )


def classify_dataframe(
    df: pd.DataFrame,
    *,
    overrides: dict[str, str] | None = None,
) -> pd.DataFrame:
    """为 DataFrame 添加 _acct_category 列，不修改原 df。

    Args:
        df: 必须含 `总账科目` 列；若有 `总账科目：长文本` 用作分类依据。
        overrides: {科目编号: 类别}，用户的手动覆盖。优先于自动分类。

    Returns:
        新的 DataFrame（拷贝），多了 `_acct_category` 列。
    """
    out = df.copy()
    overrides = overrides or {}

    name_col = _resolve_name_column(out)
    if name_col is None:
        names = pd.Series("", index=out.index)
    else:
        names = out[name_col].fillna("").astype(str)

    acct_str = out["总账科目"].astype(str).str.strip()
    # 自动分类 + 科目前缀兜底
    automatic = pd.Series(
        [
            apply_prefix_category(code, auto_classify(name))
            for code, name in zip(acct_str, names, strict=False)
        ],
        index=out.index,
    )

    # 应用用户覆盖（按完整科目编号字符串）
    if overrides:
        # 用户也可能填了 nan 之类的脏数据，先过滤
        valid_overrides = {
            str(k).strip(): str(v).strip()
            for k, v in overrides.items()
            if str(k).strip() and str(v).strip() in ALL_CATEGORIES
        }
        if valid_overrides:
            mapped = acct_str.map(valid_overrides)
            effective = mapped.where(mapped.notna(), automatic)
        else:
            effective = automatic
    else:
        effective = automatic

    # 明确会计语义由系统口径保护：历史人工覆盖保留在 state 供追溯，但不再污染图表。
    effective = pd.Series(
        [apply_prefix_category(code, cat) for code, cat in zip(acct_str, effective, strict=False)],
        index=out.index,
    )
    protected = automatic.map(is_system_protected_category)
    out["_acct_category"] = effective.where(~protected, automatic)
    return out


def build_account_overview(
    df: pd.DataFrame,
    *,
    overrides: dict[str, str] | None = None,
) -> pd.DataFrame:
    """生成"科目 -> 行数 / 金额 / 自动分类 / 人工调整" 的总览表，给 UI 用。"""
    if df is None or df.empty or "总账科目" not in df.columns:
        return pd.DataFrame()

    name_col = _resolve_name_column(df)
    amount_col = "公司代码货币价值" if "公司代码货币价值" in df.columns else "凭证货币价值"

    work = df.copy()
    work["_code"] = work["总账科目"].astype(str).str.strip()
    work["_name"] = work[name_col].fillna("").astype(str) if name_col else ""
    work["_amt"] = pd.to_numeric(work.get(amount_col, 0), errors="coerce").fillna(0).abs()

    name_per_code = (
        work.groupby("_code")["_name"]
        .agg(lambda s: next((n for n in s if n), ""))
    )

    grouped = (
        work.groupby("_code")
        .agg(行数=("_code", "count"), 金额=("_amt", "sum"))
        .reset_index()
        .rename(columns={"_code": "科目编号"})
    )
    grouped["科目名称"] = grouped["科目编号"].map(name_per_code).fillna("")
    grouped["自动分类"] = [
        apply_prefix_category(code, auto_classify(name))
        for code, name in zip(grouped["科目编号"], grouped["科目名称"], strict=False)
    ]

    overrides = overrides or {}
    grouped["人工分类"] = grouped["科目编号"].map(overrides).fillna("")
    chosen = grouped["人工分类"].where(
        grouped["人工分类"].astype(bool), grouped["自动分类"]
    )
    effective = pd.Series(
        [
            apply_prefix_category(code, category)
            for code, category in zip(grouped["科目编号"], chosen, strict=False)
        ],
        index=grouped.index,
    )
    grouped["生效分类"] = effective.where(
        ~grouped["自动分类"].map(is_system_protected_category),
        grouped["自动分类"],
    )

    grouped = grouped.sort_values("金额", ascending=False).reset_index(drop=True)
    return grouped[["科目编号", "科目名称", "行数", "金额", "自动分类", "人工分类", "生效分类"]]


def _resolve_name_column(df: pd.DataFrame) -> str | None:
    for col in ("总账科目：长文本", "总账科目：短文本"):
        if col in df.columns:
            return col
    return None
