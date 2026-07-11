"""分录明细展示列 — 各分析模块共用。"""

from __future__ import annotations

import pandas as pd


def entry_display_columns(detail: pd.DataFrame, amount_label: str) -> pd.DataFrame:
    columns = [
        "凭证编号", "过账日期", "行项目", "凭证类型", "总账科目", "_account_name",
        "借/贷标识", "公司代码货币价值", "凭证货币价值", amount_label, "用户名",
        "_customer_display", "_vendor_display", "_material_group_display", "_material_display",
        "_cost_center_display", "_header_text", "_line_text", "_reversal_text",
    ]
    columns = [c for c in columns if c in detail.columns]
    return detail[columns].rename(columns={
        "_account_name": "科目名称",
        "_customer_display": "客户",
        "_vendor_display": "供应商",
        "_material_group_display": "物料组",
        "_material_display": "物料",
        "_cost_center_display": "成本中心",
        "_header_text": "凭证抬头摘要",
        "_line_text": "摘要",
        "_reversal_text": "反记账/冲销标识",
    })
