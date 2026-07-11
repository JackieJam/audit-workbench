# M0 → M3 里程碑

## M0 地基

- [x] Monorepo：engine + api + desktop
- [x] ProjectStore（Parquet + DuckDB 分页）
- [x] FastAPI：`/health`、`/projects`
- [x] React 壳：三页导航 + API 连通

## M1 财务画像 MVP（已完成）

- [x] 迁移 `ingestion.py` + 列名学习库
- [x] ingest API（detect + commit）
- [x] 前端上传序时账（侧边栏）
- [x] 收入成本：月度图 + 客户 Top10 + 审计关注点顶栏
- [x] 选中态条 + 疑点入库（解释→行动）

## M2 全模块 + 疑点 + 抽样（已完成核心迁移）

- [x] 迁移 profiler / cross_year / rule_engine / reporter / pipeline
- [x] Pipeline API（画像 / 跨年 / 规则 / 抽样 / Excel 导出）
- [x] 财务画像子页：统计画像 + 跨年稽核
- [x] 疑点工作台：列表 + 直入最终样本
- [x] 抽样底稿：规则执行 + 抽样 + Excel 下载
- [x] 费用 / 暂估往来 / 资产负债 / 调账冲销 分析子模块（引擎 + API + 财务画像子页签）
- [x] 投资收益 / 营业外收入 / 营业外支出独立图表与明细钻取
- [x] 统一借贷、原始正负、本位币、Period 13 与互斥费用分类语义
- [x] 数据版本化与重新导入派生结果失效；页面样本与 Excel 导出同源
- [x] Agent 上下文、工具日志、写操作人工审批与审计轨迹
- [x] 真实三年 69.3 万行序时账全链路冒烟与逐图明细勾稽
- [ ] 科目余额表 / 财务报表 adapter（用户指定后置）
- [ ] AuditCase 案件/断言/证据/复核模型与来源坐标
- [ ] 前端按模块动态加载与 ECharts 分包

## M3 桌面产品化

- Tauri 打包、Key 管理、旧 pickle 迁移工具
