# 审计分析工作台

序时账审计分析桌面产品 — **FastAPI 引擎 + React + Tauri**。

从 [`15_journal-audit_序时账分析抽样`](../15_journal-audit_序时账分析抽样) 重构而来：业务算法迁入 `packages/engine`，UI 全部重写。

## 两大功效

1. **财务画像** — 多维度理解被审单位（收入成本、费用、投资及营业外、暂估、资产负债…）
2. **抽样底稿** — 疑点圈定 → 规则/抽样 → Excel 输出

连接层：**疑点工作台**。

内置审计 Agent 可读取当前项目、图表选择和疑点上下文；所有会改变规则、疑点或样本的工具调用均进入人工批准/拒绝流程并保留审计记录。

## 当前数据边界

- 正式支持 `.xlsx` 序时账；旧 `.xls` 会给出转换提示，不做不可靠的静默解析。
- 金额统一结合凭证平衡、借贷标识和原始正负判断，保留负借方、正贷方等冲回方向。
- 优先使用公司代码货币金额；仅有凭证货币且存在混合币种时，阻止跨币种图表聚合。
- 月度图包含 Period 13；费用子类互斥；图表和钻取使用同一口径。
- 科目余额表、财务报表及原始单据 adapter 尚未纳入正式支持范围。

## 架构

```
apps/desktop     Tauri + React (Vite)
apps/api         FastAPI 本地服务
packages/engine  audit_engine（无 UI 依赖）
~/.audit_tool/   Parquet + DuckDB + JSON 状态
```

## 快速开始

```bash
./scripts/bootstrap.sh

# 终端 1
./scripts/dev-api.sh

# 终端 2
./scripts/dev-ui.sh
```

打开 **http://localhost:5188** — 应看到 API 连通、可创建项目。

> **端口约定**：`26_MutiAgentComm` 前端占 **5173**（roundtable SSE：`/api/stream`）。审计工作台前端默认 **5188**，避免浏览器标签误把 SSE 代理到本项目的 FastAPI。

### 常见问题

**日志里出现 `GET /stream ... 404`**

多半是 **MutiAgentComm** 的浏览器标签仍在 `localhost:5173` 轮询 `/api/stream`；若审计工作台也占 5173，Vite 会把 `/api/*` 转到审计 API，于是出现 `/stream` 404。

- 关闭 MutiAgentComm 相关标签，或确保审计 UI 只用 **5188**
- 审计 API 是否正常：看 `/health`、`/projects` 是否为 **200**

## 里程碑

见 [docs/ROADMAP.md](docs/ROADMAP.md)。

| 阶段 | 内容 |
|------|------|
| M0 | Monorepo、ProjectStore、/health、/projects、React 三页壳（完成） |
| M1 | ingest + 收入成本模块完整交互（完成） |
| **M2（当前）** | 全模块、疑点、抽样、Agent 与财务语义加固（完成核心能力） |
| M3 | Tauri 打包、Key 管理 |

## 环境变量

| 变量 | 说明 |
|------|------|
| `AUDIT_API_PORT` | API 端口，默认 `29180` |
| `AUDIT_UI_PORT` | 前端 Vite 端口，默认 `5188`（`26_MutiAgentComm` 占用 `5173`） |
| `AUDIT_WORKBENCH_DATA_ROOT` | 覆盖数据根目录（测试用） |
| `AUDIT_WORKBENCH_NAMESPACE` | 多用户隔离子目录 |

## 旧项目

Streamlit 版继续维护至本仓库 M2 parity；算法迁移时从 `15_journal-audit` 的 `modules/` 拷入 `packages/engine/audit_engine/` 并去除 Streamlit 依赖。
