/** 规则展示元数据 — 与旧 Streamlit RULE_ORDER / RULE_META / PARAM_LABELS 对齐。 */

export const RULE_ORDER = [
  "splitting",
  "large_amount",
  "manual_entry",
  "accrual_anomaly",
  "yearend_surge",
  "financing_trade",
  "cross_year_accrual",
  "cross_year_revenue",
  "cash_pool",
  "user_concentration",
  "reversal_pattern",
  "sensitive_fees",
] as const;

export const RULE_META: Record<string, { title: string; purpose: string }> = {
  splitting: {
    title: "化整为零",
    purpose: "识别把一笔大额付款拆成多笔相近金额、借此规避审批或掩盖集中付款的模式。",
  },
  large_amount: {
    title: "大额异常",
    purpose: "识别大额整数、大额重复交易、异常周末/节假日过账和凌晨录入等高风险操作。",
  },
  manual_entry: {
    title: "手工凭证",
    purpose: "识别手工干预程度高、且更可能触达损益和月末/年末调账的凭证。",
  },
  accrual_anomaly: {
    title: "计提异常",
    purpose: "识别非常规计提、用户集中计提和长期未冲回的悬空预提。",
  },
  yearend_surge: {
    title: "年末突击确认",
    purpose: "识别收入在年末异常冲高、可能影响截止测试结论的月份。",
  },
  financing_trade: {
    title: "融资性贸易",
    purpose: "把相近期间的收入凭证和成本凭证组成疑点候选，复核是否存在贸易形式包装的资金通道。",
  },
  cross_year_accrual: {
    title: "跨年预提",
    purpose: "识别跨年预提冲回不足、存在跨期悬挂的异常对象。",
  },
  cross_year_revenue: {
    title: "跨年收入波动",
    purpose: "识别跨年层面年末收入偏高、收入确认前置的异常信号。",
  },
  cash_pool: {
    title: "资金池划转",
    purpose: "识别同名划转、资金归集、上划下拨等可能对应资金占用的交易。",
  },
  user_concentration: {
    title: "用户集中度异常",
    purpose: "识别过账权限过度集中、职责分离可能失效的用户。",
  },
  reversal_pattern: {
    title: "冲销反记账异常",
    purpose: "识别高频冲销、大额冲销以及期后修饰类反记账操作。",
  },
  sensitive_fees: {
    title: "敏感费用筛查",
    purpose: "识别咨询、代理、招待、旅游、捐赠、罚款等敏感费用及异常操作者。",
  },
  cross_year_detection: {
    title: "跨年检测阈值",
    purpose: "驱动跨年稽核计算的高级阈值；修改后需重新运行跨年分析。",
  },
};

export const PARAM_LABELS: Record<string, string> = {
  max_single_amount: "单笔上限",
  min_total: "合计门槛",
  window_days: "观察窗口",
  min_txn_count: "最少笔数",
  burst_multiplier: "频次放大倍数",
  round_number_threshold: "整数金额门槛",
  repeat_threshold: "重复大额门槛",
  repeat_window_days: "重复观察窗口",
  repeat_min_count: "重复最少笔数",
  holiday_min_amount: "节假日金额门槛",
  pnl_amount_threshold: "损益金额门槛",
  month_end_days: "月末观察天数",
  match_window_days: "冲回观察窗口",
  amount_tolerance: "金额容差",
  min_amount: "最低金额门槛",
  multiplier: "放大倍数",
  months: "关注月份",
  income_account_prefixes: "收入科目前缀",
  cost_account_prefixes: "成本科目前缀",
  min_revenue_amount: "最低收入金额",
  low_margin_threshold: "低毛利阈值",
  max_loss_rate: "最大亏损容忍",
  min_match_score: "最低匹配得分",
  max_candidate_groups: "最多候选组合",
  max_related_vouchers: "最多关联凭证",
  keywords: "关键词",
  coverage_threshold: "覆盖率阈值",
  dec_multiplier: "12月放大倍数",
  large_threshold: "大额门槛",
  concentration_threshold: "集中度阈值",
  frequent_count: "频繁笔数阈值",
  baseline_multiplier: "异常用户放大倍数",
  accrual_min_amount: "预提金额下限",
  accrual_mismatch_tolerance: "预提不符容差",
  accrual_high_severity_amount: "悬空高危金额",
  balance_buildup_growth_ratio: "累计净发生累积倍率",
  circular_large_amount: "资金循环单笔门槛",
  circular_match_ratio: "资金循环匹配度",
  circular_max_vouchers: "资金循环凭证上限",
  expense_spike_multiplier: "费用突变倍率",
  manual_entry_delta_threshold: "手工占比上升阈值",
  new_pair_count_threshold: "新科目组合门槛",
  max_sample_size: "样本上限",
};

/** 嵌套对象，UI 不直接编辑（可用 Agent）。 */
export const COMPLEX_PARAM_KEYS = new Set(["categories", "routine_exclusion"]);

export function ruleTitle(ruleId: string): string {
  return RULE_META[ruleId]?.title ?? ruleId;
}

export function paramLabel(key: string): string {
  return PARAM_LABELS[key] ?? key;
}
