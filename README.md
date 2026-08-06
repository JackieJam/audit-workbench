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

## 快速开始（开发）

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

## 桌面安装包

打包后的应用会**自动拉起本地 FastAPI sidecar**（`127.0.0.1:29180`），无需另开终端。

| 平台 | 命令 | 产物 |
|------|------|------|
| macOS (arm64/x64 本机) | `./scripts/build-desktop.sh` | `apps/desktop/src-tauri/target/release/bundle/macos/`（`.app`）与 `bundle/dmg/` |
| Windows x64 | `powershell -ExecutionPolicy Bypass -File scripts/build-desktop.ps1` | `...\bundle\nsis\` |

脚本会把 `CARGO_TARGET_DIR` 固定到仓库内 `apps/desktop/src-tauri/target`，避免产物落到沙箱缓存目录。

前置：Rust（`rustup`）、Node 20+、`uv`。Windows 包须在 Windows 机或 GitHub Actions `windows-latest` 上构建（本仓库提供 [`.github/workflows/build-desktop.yml`](.github/workflows/build-desktop.yml)）。

内部分发注意：未做 Apple 公证 / Windows 代码签名。macOS 若提示无法打开，可右键「打开」或执行 `xattr -cr /path/to/审计分析工作台.app`；Windows 可能出现 SmartScreen「仍要运行」。

### 试用样例

仓库内附带脱敏序时账：[`samples/demo_journal_2022.xlsx`](samples/demo_journal_2022.xlsx)（虚构数据，表头与真实 SAP 导出 44 列对齐）。

1. 打开安装包或开发 UI  
2. 新建项目 → 上传该文件  
3. 进入「财务画像」查看收入成本等模块；需要时再跑规则 / 抽样 / Excel 导出  

重新生成样例：`uv run python scripts/generate_demo_journal.py`

## 里程碑

见 [docs/ROADMAP.md](docs/ROADMAP.md)。

| 阶段 | 内容 |
|------|------|
| M0 | Monorepo、ProjectStore、/health、/projects、React 三页壳（完成） |
| M1 | ingest + 收入成本模块完整交互（完成） |
| M2 | 可替代 Streamlit：规则 UI、疑点工作台、LLM 核验进 Excel（闭合） |
| **M3（进行中）** | Tauri 安装包（macOS + Windows sidecar）、试用样例；Key 管理继续完善 |

## 环境变量

| 变量 | 说明 |
|------|------|
| `AUDIT_API_PORT` | API 端口，默认 `29180` |
| `AUDIT_UI_PORT` | 前端 Vite 端口，默认 `5188`（`26_MutiAgentComm` 占用 `5173`） |
| `AUDIT_WORKBENCH_DATA_ROOT` | 覆盖数据根目录（测试用） |
| `AUDIT_WORKBENCH_NAMESPACE` | 多用户隔离子目录 |
| `AUDIT_WORKBENCH_CONFIG_ROOT` | 覆盖含 `config/` 的根（打包/sidecar 用） |
| `VITE_API_BASE` | 前端 API 基址；未设时浏览器走 `/api` 代理，Tauri 走 `http://127.0.0.1:29180` |

## 旧项目

Streamlit 版继续维护至本仓库 M2 parity；算法迁移时从 `15_journal-audit` 的 `modules/` 拷入 `packages/engine/audit_engine/` 并去除 Streamlit 依赖。
