#!/usr/bin/env python3
"""生成试用脱敏序时账 samples/demo_journal_2022.xlsx。

表头与真实 SAP 导出「2022年6-12月序时账」44 列对齐；数据全部虚构。
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "samples" / "demo_journal_2022.xlsx"

# 与常见 SAP「序时账」导出表头对齐（44 列）；数据全部虚构。
COLUMNS = [
    "公司代码",
    "凭证类型",
    "借/贷标识",
    "凭证编号",
    "凭证日期",
    "过账期间",
    "总账科目",
    "总账科目：短文本",
    "下列凭证类型",
    "凭证货币代码",
    "凭证货币价值",
    "公司代码货币价值",
    "摘要",
    "凭证抬头摘要",
    "参考码3",
    "冲销标识",
    "备选参考编号",
    "行项目",
    "开户银行",
    "银行名称",
    "反记帐",
    "用户名",
    "功能范围",
    "固定数量",
    "数量",
    "计量单位",
    "物料：描述",
    "供应商",
    "供应商科目：名称 1",
    "款项用途描述",
    "物料",
    "物料组",
    "物料组描述",
    "成本中心",
    "功能范围：文本",
    "成本中心：长文本",
    "过帐日期",
    "原始组",
    "对象编号",
    "利润中心",
    "资产",
    "参考过程",
    "事务类型",
    "现金流量码",
]

ACCOUNTS = {
    "revenue": ("6001010000", "主营业务收入"),
    "ar": ("1122010000", "应收账款"),
    "cogs": ("6401010000", "主营业务成本"),
    "inventory": ("1405010000", "库存商品"),
    "expense": ("6910020001", "费用-物料消耗"),
    "cash": ("1002010000", "银行存款"),
    "ap": ("2202010000", "应付账款"),
    "payroll": ("6602010000", "管理费用-职工薪酬"),
    "salary_pay": ("2211010000", "应付职工薪酬"),
    "fixed_asset": ("1601010000", "固定资产"),
    "accum_dep": ("1602010000", "累计折旧"),
    "dep_exp": ("6602050000", "管理费用-折旧费"),
}

CUSTOMERS = [("C1001", "华东贸易有限公司"), ("C1002", "北方供应链股份"), ("C1003", "南方零售集团")]
VENDORS = [("V2001", "晨光物料供应"), ("V2002", "通达物流"), ("V2003", "星河办公科技")]
USERS = ["U01001", "U01002", "U01003", "U02001"]
COST_CENTERS = [
    ("DEMOM00001", "销售部", "销售费用"),
    ("DEMOM00002", "综合管理部", "管理费用"),
    ("DEMOM00003", "生产车间", "制造费用"),
]


def _blank_row() -> dict[str, object]:
    return {c: None for c in COLUMNS}


def _line(
    *,
    voucher: str,
    posting: date,
    doc_type: str,
    dc: str,
    account: tuple[str, str],
    amount: float,
    text: str,
    header: str,
    line_no: int,
    user: str,
    vendor: tuple[str, str] | None = None,
    customer_note: str | None = None,
    cost_center: tuple[str, str, str] | None = None,
    material: tuple[str, str] | None = None,
) -> dict[str, object]:
    row = _blank_row()
    code, name = account
    period = f"{posting.month:02d}"
    signed = amount if dc == "S" else -abs(amount)
    if dc == "H" and amount > 0:
        signed = -amount
    elif dc == "S":
        signed = abs(amount)

    row.update(
        {
            "公司代码": "DEMO",
            "凭证类型": doc_type,
            "借/贷标识": dc,
            "凭证编号": voucher,
            "凭证日期": posting,
            "过账期间": period,
            "总账科目": code,
            "总账科目：短文本": name,
            "凭证货币代码": "CNY",
            "凭证货币价值": float(signed),
            "公司代码货币价值": float(signed),
            "摘要": text,
            "凭证抬头摘要": header,
            "行项目": str(line_no),
            "用户名": user,
            "过帐日期": posting,
            "利润中心": "DEMO000001",
            "参考过程": "RFBU",
        }
    )
    if vendor:
        row["供应商"] = vendor[0]
        row["供应商科目：名称 1"] = vendor[1]
    if customer_note:
        row["款项用途描述"] = customer_note
    if cost_center:
        cc, cc_name, func = cost_center
        row["成本中心"] = cc
        row["成本中心：长文本"] = cc_name
        row["功能范围：文本"] = func
        row["对象编号"] = f"KSCIMC{cc}"
    if material:
        row["物料"] = material[0]
        row["物料：描述"] = material[1]
        row["数量"] = 1.0 if dc == "S" else -1.0
        row["计量单位"] = "EA"
        row["固定数量"] = 0.0
    return row


def build_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    start = date(2022, 6, 1)
    voucher_seq = 4900008000

    # 约 7 个月 × 每周若干张凭证 → 300+ 行
    for week in range(30):
        posting = start + timedelta(days=week * 7)
        if posting.month > 12 or posting.year > 2022:
            break
        user = USERS[week % len(USERS)]
        customer = CUSTOMERS[week % len(CUSTOMERS)]
        vendor = VENDORS[week % len(VENDORS)]
        cc = COST_CENTERS[week % len(COST_CENTERS)]

        # 1) 销售收入：借应收 / 贷收入
        voucher_seq += 1
        v = str(voucher_seq)
        rev_amt = 80000.0 + (week % 5) * 12500.0
        rows.append(
            _line(
                voucher=v,
                posting=posting,
                doc_type="DR",
                dc="S",
                account=ACCOUNTS["ar"],
                amount=rev_amt,
                text=f"销售-{customer[1]}",
                header="销售收入确认",
                line_no=1,
                user=user,
                customer_note=customer[0],
            )
        )
        rows.append(
            _line(
                voucher=v,
                posting=posting,
                doc_type="DR",
                dc="H",
                account=ACCOUNTS["revenue"],
                amount=rev_amt,
                text=f"销售-{customer[1]}",
                header="销售收入确认",
                line_no=2,
                user=user,
                customer_note=customer[0],
            )
        )

        # 2) 结转成本：借成本 / 贷库存
        voucher_seq += 1
        v = str(voucher_seq)
        cogs_amt = round(rev_amt * 0.62, 2)
        rows.append(
            _line(
                voucher=v,
                posting=posting,
                doc_type="WA",
                dc="S",
                account=ACCOUNTS["cogs"],
                amount=cogs_amt,
                text="结转销售成本",
                header="成本结转",
                line_no=1,
                user=user,
                material=(f"M{1000 + week}", f"演示成品-{week % 7 + 1}"),
            )
        )
        rows.append(
            _line(
                voucher=v,
                posting=posting,
                doc_type="WA",
                dc="H",
                account=ACCOUNTS["inventory"],
                amount=cogs_amt,
                text="结转销售成本",
                header="成本结转",
                line_no=2,
                user=user,
                material=(f"M{1000 + week}", f"演示成品-{week % 7 + 1}"),
            )
        )

        # 3) 费用报销：借费用 / 贷应付
        voucher_seq += 1
        v = str(voucher_seq)
        exp_amt = 3500.0 + (week % 4) * 800.0
        rows.append(
            _line(
                voucher=v,
                posting=posting,
                doc_type="KR",
                dc="S",
                account=ACCOUNTS["expense"],
                amount=exp_amt,
                text=f"领用物料-{vendor[1]}",
                header="费用报销",
                line_no=1,
                user=user,
                vendor=vendor,
                cost_center=cc,
                material=(f"74C{week:06d}", "办公/劳保物料"),
            )
        )
        rows.append(
            _line(
                voucher=v,
                posting=posting,
                doc_type="KR",
                dc="H",
                account=ACCOUNTS["ap"],
                amount=exp_amt,
                text=f"应付-{vendor[1]}",
                header="费用报销",
                line_no=2,
                user=user,
                vendor=vendor,
            )
        )

        # 4) 每月初额外：工资与折旧（扩大科目覆盖）
        if week % 4 == 0:
            voucher_seq += 1
            v = str(voucher_seq)
            pay = 120000.0
            rows.append(
                _line(
                    voucher=v,
                    posting=posting,
                    doc_type="SA",
                    dc="S",
                    account=ACCOUNTS["payroll"],
                    amount=pay,
                    text="计提工资",
                    header="月末计提",
                    line_no=1,
                    user=user,
                    cost_center=COST_CENTERS[1],
                )
            )
            rows.append(
                _line(
                    voucher=v,
                    posting=posting,
                    doc_type="SA",
                    dc="H",
                    account=ACCOUNTS["salary_pay"],
                    amount=pay,
                    text="计提工资",
                    header="月末计提",
                    line_no=2,
                    user=user,
                )
            )

            voucher_seq += 1
            v = str(voucher_seq)
            dep = 18000.0
            rows.append(
                _line(
                    voucher=v,
                    posting=posting,
                    doc_type="AF",
                    dc="S",
                    account=ACCOUNTS["dep_exp"],
                    amount=dep,
                    text="计提折旧",
                    header="固定资产折旧",
                    line_no=1,
                    user=user,
                    cost_center=COST_CENTERS[1],
                )
            )
            rows.append(
                _line(
                    voucher=v,
                    posting=posting,
                    doc_type="AF",
                    dc="H",
                    account=ACCOUNTS["accum_dep"],
                    amount=dep,
                    text="计提折旧",
                    header="固定资产折旧",
                    line_no=2,
                    user=user,
                )
            )

        # 5) 偶发大额：便于规则命中
        if week in {5, 12, 20}:
            voucher_seq += 1
            v = str(voucher_seq)
            big = 520000.0
            rows.append(
                _line(
                    voucher=v,
                    posting=posting,
                    doc_type="SA",
                    dc="S",
                    account=ACCOUNTS["ar"],
                    amount=big,
                    text="大额销售-演示异常",
                    header="手工凭证",
                    line_no=1,
                    user="U09999",
                    customer_note=CUSTOMERS[0][0],
                )
            )
            rows.append(
                _line(
                    voucher=v,
                    posting=posting,
                    doc_type="SA",
                    dc="H",
                    account=ACCOUNTS["revenue"],
                    amount=big,
                    text="大额销售-演示异常",
                    header="手工凭证",
                    line_no=2,
                    user="U09999",
                    customer_note=CUSTOMERS[0][0],
                )
            )

    return rows


def main() -> None:
    rows = build_rows()
    df = pd.DataFrame(rows, columns=COLUMNS)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(OUT, index=False, engine="openpyxl", sheet_name="Sheet1")
    print(f"wrote {OUT} ({len(df)} rows, {len(COLUMNS)} cols)")


if __name__ == "__main__":
    main()
