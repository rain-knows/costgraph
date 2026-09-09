# Agent Demo 到 CostGraph 迁移执行记录

本文件记录迁移阶段、状态与验收证据，不是当前接口或架构的事实 owner。当前行为以 [`docs/README.md`](../README.md) 指向的原子文档、代码、唯一 baseline 和测试为准。

## 决策

- `D:\work\costgraph` 是唯一主仓；`D:\work\agent_demo` 仅作一次性迁移源，不双向维护，不读取其数据库或历史运行数据。
- 保留 CostGraph 的 React 18、Vite 7、TypeScript、Tailwind 3、ECharts 6、Lucide、IBM Plex 前端栈；后端保留 Python 3.12、FastAPI、LangGraph、Pydantic 2、SQLAlchemy、PostgreSQL、Alembic 和 DeepSeek。
- 运行面只有一套 LangGraph 外循环、PostgreSQL Worker 和 checkpoint；不引入 Agents SDK、MCP、第二套编排框架或多 Agent。
- 首版闭环以“产品 + 期间/日期范围”为边界。生产批次、BOM、异常工单和审批按后续阶段独立演进。
- UI 行为和 API client 只做适配复用；不迁入 Agent Demo 的 React 19/shadcn 视觉组件，所有展示按根 [`DESIGN.md`](../../DESIGN.md) 重写。
- 废弃接口、字段、存储适配器、旧迁移链和历史业务数据直接删除，不提供兼容层、回退或转换。

## 复用边界

| 分类 | 迁移结论 |
| --- | --- |
| 直接复用 | LangGraph 图；Runtime/Harness；能力、主体与数据范围校验；Provider/Tool Registry；Worker、租约与 checkpoint；SSE 事件、续传与去重；幂等、取消和 finalizing；Decimal 成本计算；Artifact；Trace/Replay/Eval；样例导入与发布机制。 |
| 适配复用 | 会话 API client、SSE reducer、澄清卡片、运行状态栏、Runtime 检查器和报表查询行为。仅迁移行为与类型，使用 CostGraph IBM UI 和 canonical API 重写展示。 |
| 不迁移 | Agent Demo 整套 React 19/shadcn 界面；旧 Alembic 链；历史会话、Run、Trace、Artifact 和运行数据；`turn_requests`、`readonly_qa`、`enabled_features`；`DATABASE_URL` 别名；`runtime_request_from_legacy`；`/outputs` 跳转和 compatibility alias。 |
| 暂不实现 | 生产批次、工厂维度、BOM/半成品层级、口径草案与审批、异常闭环、PDF/Excel 导出和多 Agent。 |

## 阶段状态（截至 2026-09-09）

| 阶段 | 本次目标 | 状态 | 已有证据与未决项 |
| --- | --- | --- | --- |
| 1. 仓库与设计基线 | `frontend/backend/data/docs/scripts` 模块化目录；固定 IBM 包；许可与 provenance；项目设计绑定 | 已验收 | `design-systems/ibm/` 共 41 个文件逐文件 SHA-256 一致；`LICENSE`、`UPSTREAM.md`、`UPSTREAM-SHA256.txt` 和根 `DESIGN.md` 存在；前端只导入上游 `tokens.css`。 |
| 2. 干净后端 | PostgreSQL-only Worker；单一 Alembic baseline；四个 schema；两项能力；无迁移源历史 | 实现完成，真实 PostgreSQL durable 验证待环境 | baseline 为 `20260907_0001`，无 `turn_requests`；样例导入 3 个产品/3326 条事实/0 错误；`/api/readyz` 已返回数据库、迁移和 Worker 正常。测试中的 3 个 durable 用例因未配置 `TEST_COST_DATABASE_URL` 明确 skip，不能计为通过。 |
| 3. 真实产品闭环 | 三条成本只读 API；真实会话、澄清、Run、SSE、取消、状态栏、Inspector、Artifact；删除业务 mock 和演示开关 | 实现完成，本地验证通过；本轮未执行专门的 live DeepSeek 验证 | 产品 A `P001`、`2026-06` 固定值已由 API、Agent 计算和 Artifact 检查；Vitest 12/12、生产构建和 bundle 门禁通过；桌面与移动端深链/历史导航已检查。当前未执行 live DeepSeek 请求，fixture/Eval 结果不替代该证据；数据库中已有历史 Artifact 的 DeepSeek trace 也不计入本轮专门验证。 |
| 4. 文档与交付 | 原子文档、运行手册、同步映射和可复核证据 | 已验收 | `docs/README.md`、架构/数据/API/运行手册与本计划互相可达；`scripts/check_docs_sync.ps1`、Ruff、`git diff --check` 通过。 |

“已验收”只表示列出的检查确实执行并通过；标为“待环境”或“待执行”的项目不计入通过率。

## 接口与数据范围

首版成本事实只通过以下 canonical 接口读取，聚合、比较、金额计算和权限过滤均在服务端完成：

- `GET /api/cost-data/overview?period=YYYY-MM`
- `GET /api/cost-data/products?period=&query=&sort=&page=&page_size=`
- `GET /api/cost-data/products/{product_id}?period=YYYY-MM`

契约为 `CostOverview`、`ProductPeriodSummary` 和 `ProductPeriodDetail`；金额使用 Decimal。详情链路固定为“产品 → 工序 → 成本项 → 来源摘要”，不伪造批次或 BOM 节点。运行数据按 `tenant_id + principal_id` 隔离，成本事实必须来自当前已发布快照。

## 验收证据

### 已执行

| 检查 | 结果 |
| --- | --- |
| `PYTHONPATH=backend backend/.venv/Scripts/python.exe -m pytest backend/tests -q` | `75 passed, 3 skipped`；skip 仅为未配置 `TEST_COST_DATABASE_URL` 的真实 PostgreSQL durable 用例。 |
| `scripts/check_python_quality.ps1` | Ruff 检查与格式化通过，`89 files already formatted`。 |
| `backend/scripts/verify_agent.py` | 离线 Agent 验证 `5/5`。 |
| `backend/scripts/evaluate_agent.py` | 离线 Eval 通过；使用显式 `FixtureModelProvider`，不访问真实模型。 |
| `frontend` 的 `npm test` | 8 个测试文件、12 个测试通过。 |
| `frontend` 的生产构建与 `npm run test:bundle` | 通过；初始 JavaScript 约 `192 KiB`，小于 `500 KB` 门禁。 |
| `scripts/check_docs_sync.ps1` 与 `git diff --check` | 通过。 |
| `Push-Location backend; .\\.venv\\Scripts\\python.exe -m alembic current; Pop-Location` | `20260907_0001 (head)`。 |
| 运行态 | Backend、Worker、Frontend 均 UP；`/api/health` 为 200 且 `runtime_ready=true`；`/api/readyz` 为 200。 |
| 浏览器检查 | 桌面总览、ECharts、成本列表、产品详情、深链、前进/后退，以及 `390x844` 移动视口已检查；未发现布局重叠或 token 偏移。 |

### 尚未执行或受环境限制

- 本轮未执行专门的真实 DeepSeek `/models` 或 `/chat/completions` 验证命令；不把离线 fixture/Eval 结果当作 live 证据，数据库中已有历史 Artifact 的 DeepSeek trace 也不计入本轮专门验证。
- 真实 PostgreSQL durable 测试需要显式配置 `TEST_COST_DATABASE_URL`。不得使用 `backend/.env` 中的运行配置替代该测试变量。
- 完整身份认证/RLS、生产批次/BOM/异常/审批和导出功能不在本次首版范围。

### 固定业务值

样例导入源为 `data/samples/products.json`、`production_outputs.json` 和 `process_cost_entries.json`。产品 A 的 canonical ID 为 `P001`；期间 `2026-06` 的工序成本、总成本和单位成本必须分别为：

`61200 / 29000 / 26400 / 22400`，总成本 `139000`，合格产量 `10000`，单位成本 `13.90`。

缺少产品或期间时，Runtime 必须先返回澄清，不读取成本事实、不生成 Artifact；跨租户、跨主体和未发布数据不可见。生产 Provider 缺少或拒绝 DeepSeek 时，Run 必须失败且不回退到规则解析、成本事实读取或 Artifact 生成；离线回放只允许显式 fixture Provider。

## 后续顺序

1. 先新增生产批次 schema、导入、Repository、确定性计算与端到端验收。
2. 在批次稳定后新增 BOM/半成品层级。
3. 最后独立设计异常工单与审批流程。

每个阶段直接定义新的 canonical 契约并删除被替代路径；没有真实需求和端到端验收前，不预建抽象、配置或占位 UI。
