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

## M2 可替代 Streamlit 交付（闭合）

**完成标准**：同一真实序时账项目，在本仓库可独立完成「画像 → 疑点 → 调规则 → 抽样 →（可选）LLM 核验 → Excel」，不再依赖旧 Streamlit。

- [x] 迁移 profiler / cross_year / rule_engine / reporter / pipeline
- [x] Pipeline API（画像 / 跨年 / 规则 / 抽样 / Excel 导出）
- [x] 财务画像子页：统计画像 + 跨年稽核 + 费用/暂估/资产负债/调账等
- [x] 疑点工作台：列表 + 筛选 + 展开明细 + 编辑理由/标签/状态 + 直入最终样本
- [x] 抽样底稿：规则配置 UI（启停/阈值）+ 规则执行 + 多种抽样 + Excel 下载
- [x] 保存规则后失效命中结果 / 样本 /（必要时）跨年缓存
- [x] LLM 凭证核验 → `state.llm_judgments` → Excel 核验列
- [x] 跨年未跑时的抽样页提示
- [x] Agent 上下文、工具日志、写操作人工审批与审计轨迹
- [x] 真实三年 69.3 万行序时账全链路冒烟与逐图明细勾稽
- [ ] 列映射上传前可编辑（M2.1）
- [ ] 科目余额表 / 财务报表 adapter（用户指定后置）
- [ ] AuditCase 案件/断言/证据/复核模型与来源坐标
- [ ] 前端按模块动态加载与 ECharts 分包

## M3 桌面产品化

- Tauri 打包、Key 管理、旧 pickle 迁移工具
- `aggregates/` 预聚合（性能）
