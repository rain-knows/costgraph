# 治理/文档同步

## 稳定入口与原子所有权

根 `AGENTS.md` 定义协作规则，根 `DESIGN.md` 定义产品界面约束，`docs/README.md` 负责按问题定位事实。字段表、API、运行命令和状态机只在对应原子文档维护；跨域内容使用链接和最小摘要，不复制整段事实。

迁移计划与当前事实严格分开。`docs/plans/` 可以记录来源、阶段、假设、未通过验证和后续顺序，但不能被 API 或产品文档引用为已实现依据。

## 迁移策略

仓库以 `backend/alembic/versions/20260907_0001_costgraph_baseline.py` 为迁移图根节点，
允许在它之上叠加增量迁移（当前 head `20260917_0002`）。结构变更只能通过 Alembic：
新表、新列、新索引和键变更均需新增增量迁移，并用显式 `op.` DDL；禁止运行时建表、
`Base.metadata.create_all`、`autogenerate` 或在迁移中导入运行时模型。
`scripts/check_docs_sync.ps1` 断言 baseline 存在且 `down_revision = None`，不再限制迁移文件数量；
`backend/tests/test_baseline_and_boundaries.py` 额外断言迁移图只有一个 head，且等于运行时模型预期的 head。
删除实现时必须同时删除对应迁移之后的失效文档与断言，不留兼容层。

计划与事实的边界同样适用于批次：二期批次 1 已交付的导入任务闭环写入当前事实文档，
批次 2（OIDC/IAM/RLS）与批次 3（前端/审计页面）只写入 `docs/plans/` 与
[IAM 契约](../api/iam-contract.md) 骨架，不得写成已实现事实。

## 变更映射

| 代码或配置变更 | 必须同步的原子文档 |
| --- | --- |
| 模块、Agent 图、责任边界 | `architecture/system.md`、`architecture/data-flow.md` |
| Runtime 生命周期、预算、租约、恢复 | `architecture/runtime.md` |
| 能力、策略、主体与数据范围 | `architecture/harness.md` |
| 数据治理 API、导入任务、权限和治理页面 | `api/data-admin-contract.md`、`architecture/system.md`、`architecture/data-flow.md`、`data/governance.md` |
| 角色、权限分类法与授权门 | `architecture/harness.md` |
| IAM/审计接口（批次 2 计划，尚未实现） | `api/iam-contract.md` |
| 导入任务/Worker 调度、租约、重试 | `architecture/runtime.md`、`data/governance.md`、`api/data-admin-contract.md` |
| Tool/Provider 注册、Schema、重试 | `architecture/tool-provider.md` |
| Event、Trace、Replay、Eval、脱敏 | `architecture/event-trace-replay.md` |
| Repository、数据库连接、存储生命周期 | `data/storage.md`、`architecture/data-flow.md` |
| SQLAlchemy 模型、schema、迁移、索引 | `data/data-dictionary.md`、`data/cost-accounting-format.md`（成本事实格式和费用代码） |
| 不可变快照键、被删除的旧唯一约束 | `data/data-dictionary.md`、`data/governance.md` |
| 导入校验、发布、隔离、保留 | `data/governance.md`、`data/cost-accounting-format.md`（卷积、舍入和三视图规则） |
| 会话、Run、SSE、Artifact、错误 | `api/agent-contract.md` |
| 成本总览、完工批次列表、追溯详情契约 | `api/cost-data-contract.md` |
| 数据治理批次、错误、发布快照和权限摘要 | `api/data-admin-contract.md` |
| 启动、迁移、导入、检查命令 | `operations/runbook.md` |
| token、主题、布局与组件视觉规则 | 根 `DESIGN.md` |
| 文档规则、索引或 Agent 规则 | `docs/README.md`、本文件、根 `AGENTS.md` |

## 查档流程

1. 从 `docs/README.md` 找到事实 owner。
2. 沿原子文档代码锚点检查当前实现与 baseline。
3. 用测试、真实 API 或 SQL 结果确认行为。
4. 仍不能确认时记录未知并与 owner 对齐，不补写猜测。

## 完成定义

- 所有架构原子文档、三份 API 契约（Agent、成本数据、数据治理）、一份 IAM 计划契约、四份数据文档（含成本核算格式）、运行手册和设计入口存在且互相可达。
- 每个新增或修改的接口、字段、配置、状态和业务规则都有唯一文档 owner 和对应测试。
- 当前事实中没有已删除实体、运行模式、接口或迁移链；迁移图以 baseline 为根、只有一个 head，增量迁移与 `backend/app/db/models.py` 一致。
- 尚未实现的批次只出现在 `docs/plans/` 与 `api/iam-contract.md` 骨架中，不混入当前能力清单。
- 上游设计包固定 commit、许可和 provenance 完整，产品覆盖不直接修改上游 token 源。
- `scripts/check_docs_sync.ps1` 与 `git diff --check` 通过。

检查脚本只能发现结构、同步映射、失效链接和已知旧标识，不能替代人工核对代码与运行证据。
