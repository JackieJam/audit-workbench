# 本地测试数据

## 仓库内试用样例（推荐）

[`samples/demo_journal_2022.xlsx`](../samples/demo_journal_2022.xlsx) — 虚构脱敏序时账（约 200 行），表头与真实 SAP 导出 44 列对齐。

```bash
uv run python scripts/generate_demo_journal.py
```

## 本机真实案例（开发机）

```
/Users/jackie_m/Downloads/序时账测试案例
```

| 文件 | 用途 | 规模 |
|------|------|------|
| `2022年6-12月序时账.XLSX` | 集成测试默认（较快） | ~26MB |
| `1094 810序时账（23.1-23.12）.XLSX` | 大表压力测试 | ~72MB |
| `2212/2312/2412科目余额表.XLSX` | 余额表 adapter（待） | 小 |
| `RE010-3-报表导出-年度-*.XLSM` | 财务报表 adapter（待） | 中 |

运行真实案例测试：

```bash
uv run pytest -m slow apps/api/tests/test_analysis.py::test_real_journal_2022_monthly
```
