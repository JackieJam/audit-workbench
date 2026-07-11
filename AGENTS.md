# 审计分析工作台

## 定位

本地桌面审计产品：**财务画像 + 疑点工作台 + 抽样底稿**。  
技术栈：Tauri · React · TypeScript · FastAPI · `audit_engine`（Python）。

## 仓库结构

```
apps/desktop/          React + Tauri 壳
apps/api/audit_api/    FastAPI 路由（薄编排层）
packages/engine/       分析内核 + ProjectStore
config/                audit_questions、default_rules（待迁入）
```

## 运行

```bash
uv sync
./scripts/dev-api.sh    # API :29180
./scripts/dev-ui.sh     # UI  :5188 → proxy /api
```

## 约定

- **engine 禁止** `import streamlit`
- 明细数据 **不经前端全量加载** — API 分页 + DuckDB
- 阈值、规则 rationale 仍从 config 读取，不硬编码
- API Key 不落盘（Tauri keyring / 环境变量，待 M3）

## 数据目录

`~/.audit_tool/projects/<id>/`

- `manifest.json` — 项目元数据
- `raw/years/{year}.parquet` — 序时账
- `derived/` — 分析就绪表（M1）
- `aggregates/` — 预聚合（M1）
- `state.json` — 疑点库、规则、LLM 结果

## 迁移来源

旧项目 `15_journal-audit_序时账分析抽样`：`modules/` → `packages/engine/audit_engine/`。


<claude-mem-context>
# Memory Context

# [47_audit-workbench_审计分析工作台] recent context, 2026-07-11 12:23pm GMT+8

No previous sessions found.
</claude-mem-context>