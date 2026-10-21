# Agent Demo 到 CostGraph 迁移执行记录

本文件只记录迁移决策、阶段状态和实际验收证据，不是当前接口或架构的事实 owner。当前行为以 [`docs/README.md`](../README.md) 指向的原子文档、代码、唯一 baseline 和测试为准。

## 决策

- `D:\work\costgraph` 是唯一主仓；`D:\work\agent_demo` 仅作一次性迁移源，不双向维护，不读取其数据库或历史运行数据。
- 保留 CostGraph 的 React 18、Vite 7、TypeScript、Tailwind 3、ECharts 6、Lucide、IBM Plex 前端栈；后端保留 Python 3.12、FastAPI、LangGraph、Pydantic 2、SQLAlchemy、PostgreSQL、Alembic 和 DeepSeek。
- 运行面只有一套 LangGraph 外循环、PostgreSQL Worker 和 checkpoint；不引入 Agents SDK、MCP、第二套编排框架或多 Agent。
- 成本事实模型为“购置/工艺事件 -> 实际批次投入 -> 单一输出批次 -> 逐笔费用”；使用固定 48 项费用代码和服务端 Decimal 卷积。
- 实际投入图不是计划 BOM 或库存台账；当前不实现库存结存、返工分支、副产品、异常、审批和导出。
- UI 行为和 API client 只做适配复用；不迁入 Agent Demo 的 React 19/shadcn 视觉组件，所有展示按根 [`DESIGN.md`](../../DESIGN.md) 重写。
- 废弃表、接口、字段、存储适配器、旧迁移链和历史业务数据直接删除，不提供兼容层、回退、旧库升级或 Artifact 转换。

## 复用与替换边界

| 分类 | 结论 |
| --- | --- |
| 保留 | LangGraph Runtime/Harness；能力、主体与数据范围校验；Provider/Tool Registry；Worker、租约与 checkpoint；SSE；幂等、取消和 finalizing；Artifact；Trace/Replay/Eval；导入发布机制。 |
| 整体替换 | 产品期间聚合替换为批次 DAG 卷积；成本 API 替换为完工批次列表与追溯详情；Agent/Artifact 报告替换为 schema `3.0`；前端成本页替换为六类、料工费、变动/固定三视图。 |
| 不迁移 | 旧成本事实与三份样例；旧成本 `/products` 路由；旧五类成本字段；旧报告的比较、工序拆分、构成图和日期范围；旧迁移源历史数据。 |
| 当前不实现 | 计划 BOM、库存结存、返工分支、副产品、异常工单、审批、PDF/Excel 导出和多 Agent。 |

## 本轮阶段状态（2026-09-09）

| 阶段 | 目标 | 状态 | 完成条件 |
| --- | --- | --- | --- |
| 1. 数据基线 | `parts/cost_events/cost_event_inputs/cost_records`、48 项费用代码、唯一 baseline、四份样例 | 已实现；本地 fixture 验收通过 | 导入约束、样例批次和图边界测试通过；真实 PostgreSQL 迁移尚未执行 |
| 2. 确定性卷积 | DAG、多投入、部分领用、不良成本承接、叶级尾差、三视图 | 已实现；本地验收通过 | 黄金批次、恒等式、环路、超量领用、舍入尾差和租户/快照边界测试通过 |
| 3. API 与 Agent | `/overview`、`/finished-batches`、详情追溯图、Report/Artifact `2.0` | 已实现；本地验收通过 | API、Agent、lineage、Artifact 测试通过；真实 DeepSeek 未执行 |
| 4. 前端 | 三页签、批次详情、追溯树、来源记录、Dashboard 和报表中心 | 已实现；自动化验收通过 | Vitest、生产构建和 bundle 门禁通过；桌面/移动/明暗截图检查未执行 |
| 5. 文档与运行 | 原子文档、格式约定、运行手册和同步检查 | 已实现；本地验收通过 | 文档同步检查与 `git diff --check` 通过 |

在对应命令实际执行前不得把“实现中，待验收”改成“已验收”。旧产品期间模型的 `P001/2026-06/13.90` 结果及其测试数量仅是被替换版本的历史证据，不再证明当前实现。

上表保留 2026-09-09 的阶段快照；其“尚未执行”项已由下文的补充复验与收尾复验更新，当前交付状态以最新复验记录为准。

## 新 canonical 边界

- 导入文件：`parts.json`、`cost_events.json`、`cost_event_inputs.json`、`cost_records.json`。
- 成本 API：`GET /api/cost-data/overview`、`GET /api/cost-data/finished-batches`、`GET /api/cost-data/finished-batches/{finished_batch_id}`。
- 公共类型：`CostOverview`、`FinishedBatchCostList`、`FinishedBatchCostDetail`、`CostTraceGraph`、`CostReportV3`。
- Agent 输入边界：唯一产成品零件与期间；报告汇总该期间的全部最终批次。
- 数据隔离：运行数据按 `tenant_id + principal_id`，成本事实按 `tenant_id + published batch + data_scope`。

## 2026-10-20 历史验收记录

以下数字与结果仅记录 2026-10-20 当时的验收，不代表 2026-10-21 审阅修复后的验证结果；一种测试不能替代另一种。

| 检查 | 当时证据 |
| --- | --- |
| 后端完整 Pytest | 专用 `costgraph_test`：`288 passed, 0 skipped, 2 warnings`；未配置测试库时本地：`258 passed, 30 skipped` |
| Ruff lint 与 format | `All checks passed!`、`128 files already formatted`（`scripts\check_python_quality.ps1`） |
| 真实 PostgreSQL 导入、隔离与 Runtime 集成 | `alembic current` = `20260921_0005`；31 个 PostgreSQL 集成用例全部通过，包含 RLS、导入发布、durable、审计和最后一次租约领取后崩溃终态。 |
| 黄金批次 API 与三视图恒等式 | 真实库上通过：制造成本 `50000.00`、制造单位成本1 `50.00`、合计成本2 `53500.00`、合计单位成本2 `53.50`，三视图恒等式通过 |
| `verify_agent.py` 与离线 Eval | `verify_agent.py` 通过（`FG-001 2026-06`，`total_cost=50000.00`，`unit_cost=53.50`，`source_events=5`）；离线 Eval `6/6` 通过；fixture 不证明真实模型可用 |
| 真实 DeepSeek | 当时未执行；2026-10-19 补充复验已单独记录 provider、model、时间和结果（见文末） |
| 前端 Vitest、生产构建与 bundle 门禁 | Vitest `92 passed`；生产构建与 bundle 门禁通过；初始 JavaScript `211.2 KiB`，低于 `500 KiB` 门禁 |
| 只读性能验收 | `benchmark_cost_queries.py` 五项 p95 `0.38–0.49s`，均 `< 1.0s` 且 `max_queries = 2` |
| 桌面/移动、浅/深主题视觉检查 | 真实 Chromium 已执行；四种状态截图与 `result.json` 写入 `.pi/goals/evidence/reports-browser/` |
| 文档同步与 `git diff --check` | 均通过 |

导入命令未在本轮重新执行；上表 PostgreSQL 证据来自当前已发布快照的只读查询（overview、批次详情、投影计数），不等同于一次全新导入的验收。

黄金批次的固定输入与结果见[成本核算格式](../data/cost-accounting-format.md)：完工 `1000.0000`、合格 `950.0000`、不良 `50.0000`；制造成本 `50000.00`、制造单位成本1 `50.00`、合计成本2 `53500.00`、合计单位成本2 `53.50`。成本详情、Agent report schema `3.0` 和 Artifact 必须一致。

## 后续演进顺序

1. 先完成并稳定本轮批次卷积闭环及验收。
2. 有真实库存需求后独立设计库存结存与批次余额，不复用展示层派生值作为库存事实。
3. 有真实生产规则后分别增加返工、副产品、异常和审批，不预建占位抽象。

每个阶段定义唯一 canonical 契约并直接删除被替代路径；未实现能力只记录在本计划，不进入事实文档或占位 UI。

## 2026-10-19 补充复验

- 真实 DeepSeek：执行 `backend\\.venv\\Scripts\\python.exe backend\\scripts\\verify_deepseek.py`，
  `deepseek-v4-flash` 意图解析与成本分析均成功，共 3,304 tokens；本地黄金 fixture 仅提供确定性输入。
- 真实浏览器报表验收：在当前本地 `APP_ENV=demo` 环境使用
  `scripts\\verify_reports_browser.ps1 -LocalDemoAuth` 执行；该模式只发送固定本地 demo
  Bearer 令牌，不改变 staging/production 的 OIDC 强制认证。独立 Chromium 的四种视口/主题
  结果写入 `.pi/goals/evidence/reports-browser/result.json` 与四张截图。
- 未设置 `TEST_COST_DATABASE_URL` 时的本地全量后端测试为 `258 passed, 30 skipped`；本轮在专用
  `costgraph_test` 上设置该变量后全量为 `288 passed, 0 skipped`，真实 PostgreSQL 集成结果见
  [治理计划](data-management-and-permission-governance.md)与[运行手册](../operations/runbook.md)。

## 2026-10-20 收尾复验

- 真实 Chromium：执行 `scripts\\verify_reports_browser.ps1 -LocalDemoAuth` 通过。验收覆盖成本列表
  18 条批次、批次详情三页签、周期对比报表和展示型报表；桌面/移动与浅/深主题均通过，ECharts
  canvas 有实际绘制、无横向溢出、数量为整数显示，并验证 Tab 键焦点。证据位于
  `.pi/goals/evidence/reports-browser/result.json` 及四张状态截图。
- 专用 PostgreSQL：`costgraph_test` 已在 `20260921_0005`，执行 `backend\\.venv\\Scripts\\python.exe
  -m pytest backend\\tests -q` 得到 `288 passed, 0 skipped`；17 个导入任务集成用例包含最后一次
  租约领取后崩溃转 `validation_failed` 和脱敏审计验证。
- 真实 OIDC IdP：仍未执行。当前环境没有外部 issuer、audience、JWKS 和前端 client 配置；本地
  demo 认证仅用于启动与浏览器验收，不替代部署环境的真实 OIDC 验证。
