# 测试 / 试用数据

## 仓库内示例（可提交）

[`samples/demo_journal_2022.xlsx`](../samples/demo_journal_2022.xlsx) — **虚构脱敏**序时账，表头与常见 SAP 导出 44 列对齐，仅供试用与冒烟。

```bash
uv run python scripts/generate_demo_journal.py
```

## 本机真实案例（不可提交）

真实客户序时账、余额表、报表等**不得**进入 git。仓库已忽略 `*.xlsx` / `*.xls` / `*.xlsm` / `*.parquet`（仅 `samples/**/*.xlsx` 例外）。

本机开发请设置环境变量指向你的私有目录：

```bash
export AUDIT_REAL_CASE_DIR="/path/to/your/private/journals"
```

可选：复制 [`TEST_DATA.local.md.example`](TEST_DATA.local.md.example) 为 `TEST_DATA.local.md`（已 gitignore）记录文件名备忘。

慢速集成测试（目录不存在或未设置时自动 skip）：

```bash
export AUDIT_REAL_CASE_DIR="..."
uv run pytest -m slow apps/api/tests/test_analysis.py::test_real_journal_2022_monthly
```

真实案例冒烟：

```bash
export AUDIT_REAL_CASE_DIR="..."
uv run python scripts/real_case_smoke.py
```
