# 本地测试数据

开发/集成测试可使用：

```
/Users/jackie_m/Downloads/序时账测试案例
```

| 文件 | 用途 | 规模 |
|------|------|------|
| `2022年6-12月序时账.XLSX` | 集成测试默认（较快） | ~26MB |
| `1094 810序时账（23.1-23.12）.XLSX` | 大表压力测试 | ~72MB |
| `2212/2312/2412科目余额表.XLSX` | 余额表 adapter（待 M1+） | 小 |
| `RE010-3-报表导出-年度-*.XLSM` | 财务报表 adapter（待 M1+） | 中 |

运行真实案例测试：

```bash
uv run pytest -m slow apps/api/tests/test_analysis.py::test_real_journal_2022_monthly
```
