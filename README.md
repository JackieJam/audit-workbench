# 审计分析工作台

把序时账变成能看、能查、能交出去的底稿。本地桌面工具，账留在你电脑里。

打开软件，上传 Excel 序时账，然后可以：

1. **财务画像** — 收入、成本、费用、往来摊开看，点图就能看到凭证
2. **疑点工作台** — 化整为零、大额、年末突击等常见问题先筛一遍，也可以自己从图上圈
3. **抽样底稿** — 从疑点或全部凭证里抽样本，导出 Excel

旁边有审计助手，能对着当前项目和疑点说话。改规则、动样本都要你点头。

技术栈：Tauri · React · FastAPI · `audit_engine`（Python）。

## 现在到哪一步

主流程已经能独立走完：导入 → 画像 → 疑点 → 抽样 →（可选）大模型帮看样本 → 导出 Excel。不再依赖旧的 Streamlit 原型。

当前按 **Trusted Audit Beta** 交付：规则命中先全部列出来再抽样；每张样本能说清为什么入选；大模型失败只会标「未核验」，不会写成已确认。

| 阶段 | 状态 |
|------|------|
| 地基、财务画像 | 完成 |
| 可替代旧工具的主流程 | 闭合 |
| 桌面安装包、试用样例 | 脚手架已完成 |
| Key 管理、列映射编辑、余额表 / 报表 | 还在做 |

更细的清单见 [docs/ROADMAP.md](docs/ROADMAP.md)。

**目前正式支持** `.xlsx` 序时账。旧 `.xls` 会提示你先转换，不会偷偷乱解析。科目余额表、财务报表、原始单据还不能当正式输入。

## 试用

仓库里有一份虚构脱敏样例：[samples/demo_journal_2022.xlsx](samples/demo_journal_2022.xlsx)。

```bash
./scripts/bootstrap.sh

# 终端 1
./scripts/dev-api.sh

# 终端 2
./scripts/dev-ui.sh
```

打开 http://localhost:5188 ，新建项目，上传该文件。先看「财务画像」，再跑规则、抽样、导出。

真实客户账放本机，不要提交到 git。说明见 [docs/TEST_DATA.md](docs/TEST_DATA.md)。

重新生成样例：

```bash
uv run python scripts/generate_demo_journal.py
```

## 桌面安装包

打包后的应用会自己拉起本机服务（`127.0.0.1:29180`），不用另开终端。

| 平台 | 命令 | 产物 |
|------|------|------|
| macOS | `./scripts/build-desktop.sh` | `apps/desktop/src-tauri/target/release/bundle/` 下的 `.app` / `.dmg` |
| Windows x64 | `scripts/build-desktop.ps1` | NSIS 安装包 |

前置：Rust、Node 20+、`uv`。Windows 包要在 Windows 或 GitHub Actions 上打（[`.github/workflows/build-desktop.yml`](.github/workflows/build-desktop.yml)）。

还没有 Apple 公证和 Windows 签名。macOS 打不开时，右键「打开」，或执行 `xattr -cr /path/to/审计分析工作台.app`。

## 仓库结构

```
apps/desktop     桌面界面（Tauri + React）
apps/api         本地 FastAPI，只做编排
packages/engine  分析内核（不依赖界面）
config/          规则阈值、审计问题清单
samples/         试用样例账
```

项目数据在本机 `~/.audit_tool/projects/<id>/`：导入的账、分析结果、疑点和抽样状态都在这里。明细走分页查询，不会把整本账灌进浏览器。

## 环境变量

| 变量 | 说明 |
|------|------|
| `AUDIT_API_PORT` | API 端口，默认 `29180` |
| `AUDIT_UI_PORT` | 开发界面端口，默认 `5188` |
| `AUDIT_WORKBENCH_DATA_ROOT` | 覆盖数据目录（测试用） |
| `AUDIT_WORKBENCH_CONFIG_ROOT` | 打包时指定 `config/` 所在根目录 |
| `VITE_API_BASE` | 前端 API 地址；未设时浏览器走 `/api` 代理，桌面端走 `http://127.0.0.1:29180` |

## 从哪来

由 [JackieJam/journal-audit](https://github.com/JackieJam/journal-audit)（Streamlit 序时账分析抽样）演进而来：算法迁到 `packages/engine`，界面重做成桌面产品。主流程已在本仓库独立完成。
