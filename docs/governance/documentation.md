# 治理/文档同步

## 稳定入口与原子所有权

根 `AGENTS.md` 定义协作规则，根 `DESIGN.md` 定义产品界面约束，`docs/README.md` 负责按问题定位事实。字段表、API、运行命令和状态机只在对应原子文档维护；跨域内容使用链接和最小摘要，不复制整段事实。

迁移计划与当前事实严格分开。`docs/plans/` 可以记录来源、阶段、假设、未通过验证和后续顺序，但不能被 API 或产品文档引用为已实现依据。

## 变更映射

| 代码或配置变更 | 必须同步的原子文档 |
| --- | --- |
| 模块、Agent 图、责任边界 | `architecture/system.md`、`architecture/data-flow.md` |
| Runtime 生命周期、预算、租约、恢复 | `architecture/runtime.md` |
| 能力、策略、主体与数据范围 | `architecture/harness.md` |
| Tool/Provider 注册、Schema、重试 | `architecture/tool-provider.md` |
| Event、Trace、Replay、Eval、脱敏 | `architecture/event-trace-replay.md` |
| Repository、数据库连接、存储生命周期 | `data/storage.md`、`architecture/data-flow.md` |
| SQLAlchemy 模型、schema、迁移、索引 | `data/data-dictionary.md` |
| 导入校验、发布、隔离、保留 | `data/governance.md` |
| 会话、Run、SSE、Artifact、错误 | `api/agent-contract.md` |
| 成本总览、列表、详情契约 | `api/cost-data-contract.md` |
| 启动、迁移、导入、检查命令 | `operations/runbook.md` |
| token、主题、布局与组件视觉规则 | 根 `DESIGN.md` |
| 文档规则、索引或 Agent 规则 | `docs/README.md`、本文件、根 `AGENTS.md` |

## 查档流程

1. 从 `docs/README.md` 找到事实 owner。
2. 沿原子文档代码锚点检查当前实现与 baseline。
3. 用测试、真实 API 或 SQL 结果确认行为。
4. 仍不能确认时记录未知并与 owner 对齐，不补写猜测。

## 完成定义

- 所有架构原子文档、两份 API 契约、三份数据文档、运行手册和设计入口存在且互相可达。
- 每个新增或修改的接口、字段、配置、状态和业务规则都有唯一文档 owner 和对应测试。
- 当前事实中没有已删除实体、运行模式、接口或迁移链；仓库只有一条 CostGraph baseline。
- 上游设计包固定 commit、许可和 provenance 完整，产品覆盖不直接修改上游 token 源。
- `scripts/check_docs_sync.ps1` 与 `git diff --check` 通过。

检查脚本只能发现结构、同步映射、失效链接和已知旧标识，不能替代人工核对代码与运行证据。
