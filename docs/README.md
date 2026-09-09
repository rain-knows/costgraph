# CostGraph 项目事实文档

本目录是项目事实的唯一文档入口。文档按责任边界拆成可独立读取和更新的原子文件；当前代码、唯一 Alembic baseline、自动化测试和实际运行结果是事实依据，迁移源、聊天记录、截图和未验收计划只能作为线索。

## 按问题查阅

| 想确认什么 | 先读 | 变更时更新 |
| --- | --- | --- |
| 产品、模块与 Agent 责任边界 | [架构/系统](architecture/system.md) | `architecture/system.md` |
| 导入、查询、计算和产出流转 | [架构/数据流](architecture/data-flow.md) | `architecture/data-flow.md` |
| Run 生命周期、恢复、预算和取消 | [架构/Runtime](architecture/runtime.md) | `architecture/runtime.md` |
| 能力、上下文和策略门 | [架构/Harness](architecture/harness.md) | `architecture/harness.md` |
| 工具与模型 Provider 契约 | [架构/Tool 与 Provider](architecture/tool-provider.md) | `architecture/tool-provider.md` |
| 事件、Trace、Replay 和 Eval | [架构/Event、Trace、Replay 与 Eval](architecture/event-trace-replay.md) | `architecture/event-trace-replay.md` |
| PostgreSQL 与 schema 所有权 | [数据/存储](data/storage.md) | `data/storage.md` |
| 表、字段、约束和关系 | [数据/数据字典](data/data-dictionary.md) | `data/data-dictionary.md` |
| 导入发布、质量、隔离和保留 | [数据/治理与质量](data/governance.md) | `data/governance.md` |
| 会话、Run、SSE、Artifact 和错误 | [API/Agent 契约](api/agent-contract.md) | `api/agent-contract.md` |
| 总览、产品列表与成本详情 | [API/成本数据契约](api/cost-data-contract.md) | `api/cost-data-contract.md` |
| 启动、迁移、导入、验证和排障 | [运行手册](operations/runbook.md) | `operations/runbook.md` |
| 如何维护文档与同步规则 | [文档治理](governance/documentation.md) | `governance/documentation.md`、必要时根 `AGENTS.md` |
| 本次迁移阶段、状态和证据 | [迁移执行记录](plans/agent-demo-to-costgraph.md) | 仅在状态或证据变化时更新 |

界面设计另以根 [`DESIGN.md`](../DESIGN.md) 为入口；上游设计包 provenance 见
[`design-systems/ibm/UPSTREAM.md`](../design-systems/ibm/UPSTREAM.md)。

## 事实来源优先级

1. 当前运行中的 API、数据库查询和通过的集成测试。
2. `backend/app/` 与 `frontend/src/` 当前实现和类型。
3. `backend/alembic/versions/` 中唯一 baseline。
4. 本目录原子事实文档。
5. `docs/plans/` 中明确标注的计划和执行记录。

发现冲突时按此顺序核验并修正文档，不能让文档反向证明尚未实现的能力。

## 当前事实与计划

- 事实段落必须能回指代码、迁移、测试或运行命令。
- 计划、假设、未实现阶段和未执行的验证只写入 `docs/plans/`，不得混进当前能力清单。
- 新增或修改表、字段、接口、配置、状态、权限、业务规则或设计 token 时，同一变更必须更新对应原子文档与测试。
- 删除实现时同步删除失效说明、路径和兼容叙述，不保留第二套“旧接口文档”。

提交前运行：

```powershell
.\scripts\check_docs_sync.ps1
git diff --check
```
