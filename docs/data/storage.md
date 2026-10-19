# 数据/PostgreSQL 存储

## 唯一应用后端

API、Worker、会话、成本事实、checkpoint 和 Artifact 全部使用同一个 CostGraph PostgreSQL 数据库，通过四个 schema 划分所有权：

| schema | owner | 表 |
| --- | --- | --- |
| `cost_data` | 成本数据服务 | `data_load_batches`、`data_load_errors`、`data_import_jobs`、`governance_audit_events`、`iam_principal_roles`、`iam_data_scopes`、`parts`、`cost_events`、`cost_event_inputs`、`cost_records`、`finished_batch_cost_projections` |
| `agent_runtime` | 会话与运行服务 | `conversations`、`turns`、`messages`、`audit_traces`、`agent_runs`、`agent_run_events`、`agent_workers` |
| `agent_checkpoint` | LangGraph 恢复状态 | `checkpoint_migrations`、`checkpoints`、`checkpoint_blobs`、`checkpoint_writes` |
| `agent_output` | 结构化产出服务 | `artifacts` |

应用运行面没有可切换的本地存储适配器。`data/samples/*.json` 只由导入脚本读取并发布进 PostgreSQL；Fixture Repository 只能由测试显式注入。

## 连接与迁移

- 唯一数据库连接配置是 `COST_DATABASE_URL`，由 `pydantic-settings` 从 `backend/.env` 与环境变量读取，环境变量优先。
- URL 使用 SQLAlchemy psycopg 方言，例如 `postgresql+psycopg://user:password@host:5432/costgraph`。密码不得进入源码、文档、日志或 Trace。
- 连接池启用 pre-ping；pool size、overflow 和超时使用服务端配置的有界值。
- Alembic 是 schema 的唯一创建入口：`20260907_0001` 是迁移图根节点 baseline，`20260917_0002`
  在其上叠加导入任务表与不可变快照键改造；`20260918_0003` 新增 `iam_principal_roles`、
  `iam_data_scopes` 并扩展 `governance_audit_events` 的动作约束；`20260920_0004` 创建受限应用角色
  `costgraph_app` 并为全部业务表启用行级安全。`backend/alembic/versions/` 允许继续追加增量迁移，
  但必须能从 baseline 线性到达且只有一个 head。API 和 Worker 启动时不自动创建业务表，也不调用
  checkpoint saver 的 setup。
- 迁移只使用显式 `op.` DDL，禁止 `Base.metadata.create_all`、`autogenerate` 和导入运行时模型。
  `20260917_0002` 先把五张成本事实表的 `batch_id` 纳入主键与唯一约束、删除租户级全局唯一约束，
  再把主子表外键组合化；实现顺序为“drop 旧外键 -> drop 旧唯一约束 -> drop 旧主键 -> 建新唯一约束
  -> 建新主键 -> 建新外键”。
- `dev.ps1` 只负责启动 API、独立 Worker 和 Vite，并等待 `/api/readyz`；它不执行 Alembic、创建表或清理数据库，启动阶段进程提前退出时会立即报告对应日志。
- `/api/readyz` 校验数据库连通、数据库 revision 等于代码 head、四个 schema 的必需表和列存在，以及 Worker 心跳。使用 SQLAlchemy Inspector 按 schema 批量反射列定义；缺表或缺列返回 `checks.alembic=schema_mismatch`，服务端日志记录缺失对象。此检查不比较列类型、索引和约束，也不代替 Alembic。任一项失败时 Runtime 不接收新 Run。

## 读取与所有权边界

- 成本读取必须匹配 `tenant_id`、当前 `published` 批次、产成品零件和期间，并应用服务端 `ExecutionContext.data_scope`。投影表带有 `batch_id` 列但不引用成本事件，因此任何 join 必须显式带 `batch_id` 谓词，不能匿名按 `event_id` 关联。
- 权限与数据范围只来自 `cost_data.iam_principal_roles`（租户内角色）与 `cost_data.iam_data_scopes`（零件/期间范围）；两张表以 `(tenant_id, principal_id)` 为主键，角色取值由 `ck_iam_principal_roles_known` 约束锁定为四个角色。数据范围为空集表示未被收窄。
- 租户隔离有两层：Repository 的 `WHERE tenant_id = ...` 是第一层；数据库 RLS 是第二层。RLS 策略以 `current_setting('app.tenant_id', true)` 为唯一判据，未设置时返回 0 行（fail-closed），不存在“缺省租户”的越权路径。RLS 必须能独立拦住漏写 `tenant_id` 的查询，不能与仓储层互相替代。
- RLS 实现约束：`20260920_0004` 对带 `tenant_id` 的 15 张表直接比较该列；`agent_runtime.messages`、`agent_run_events`、`audit_traces`、`agent_workers` 没有该列，改为经父表推导（分别经 `conversations.conversation_id`、`agent_runs.run_id` 和 `agent_workers.current_run_id`）。策略使用 `ENABLE` 而非 `FORCE`：表所有者（Alembic 与受控运维连接）不受策略约束，受限应用角色 `costgraph_app` 必须写入 `app.tenant_id` 才能看到本租户数据。因此 **API 必须以 `costgraph_app` 连接才享受 RLS**；以表所有者连接时策略完全不生效，只剩 Repository 一层。二者的接线与分工见 [运行手册](../operations/runbook.md)。
- 验证状态：真实数据库上的拦截已实测。以受限角色 `costgraph_app` 连 `costgraph_test` 后，`test_rls_postgres.py`（4）与 `test_data_admin_postgres.py`（3）、`test_durable_postgres.py`（3）、`test_governance_audit_postgres.py`（2）共 12 个用例通过；未配置 `TEST_COST_DATABASE_URL` 的环境仍会跳过这些文件。
- 前置条件：`set_tenant()` 必须在事务开始前生效，仓储必须在会话上下文内把 ORM 行映射成 dict。`release_session()` 先 `expunge_all()` 再 `rollback()`，因为 `rollback()` 会把身份映射实例标记为 expired，会话关闭后的属性访问会抛 `DetachedInstanceError`。
- 租户上下文的唯一入口是 `backend/app/db/tenant.py`：`set_tenant()` 写 `app.tenant_id`，`release_session()` 先回滚再清空该设置后关闭连接；租户用会话级 `set_config(..., false)` 写入，`rollback()` 不会清除它，所以 RESET 失败时必须 `invalidate()` 作废物理连接，不能把可能仍带旧租户的连接还给连接池。禁止在依赖中用 ContextVar 传递租户——FastAPI 同步依赖运行在线程池的上下文拷贝中，设置无法回传到 endpoint。
- 导入任务 Repository 复用同一 PostgreSQL 工厂：任务与审计按租户隔离，审计只追加。审计表由 `trg_governance_audit_events_append_only` 触发器拦下任何 UPDATE/DELETE，测试清理需显式禁用触发器。导入载荷存在本地内容寻址对象存储（默认 `IMPORT_OBJECT_STORE_ROOT`，缺省为系统临时目录下 `costgraph-import-objects`），数据库只存 `source_object_key`/`source_object_size`/`source_object_sha256`；`object_key` 由内容 SHA-256 派生，读回时重新校验摘要。
- 数据治理只读 Repository 复用同一 PostgreSQL 工厂；批次和错误按租户隔离，发布质量统计绑定当前 published batch、当前规则版本和 DataScope。
- 会话、Turn、消息、Run、Trace 和 Artifact 的业务查询必须匹配 `tenant_id + principal_id`。
- Worker 只领取数据库中已固化可信 owner 快照的 Run，不从客户端重新解释主体。
- `raw_payload`/JSONB 只做导入追溯，不直接参与金额计算。累计成本、单位成本、占比和三视图由同一确定性服务在发布时计算，并写入绑定快照与规则版本的只读投影；事实表不保存派生值，投影不能成为事实或安全边界。
- Artifact 只保存业务关联 ID，不对 Runtime 表建跨 schema 外键；删除会话不会删除独立历史产出。
- Artifact 的 `report_json` 直接保存并读取当前 `CostReportV3`；不在 Repository 中转换旧报告。展示型和周期对比型共用 `agent_output.artifacts`，目标期间继续写入索引列 `period`，基准期间保存在报告 JSON 的 `comparison.baseline_period`。
- checkpoint `thread_id` 固定为 `run_id`，没有公共读取 API，并按终态保留规则清理。

字段和约束见 [数据字典](data-dictionary.md)，字段格式、48 项费用代码与公式见[成本核算格式](cost-accounting-format.md)，导入发布规则见 [治理与质量](governance.md)。

依据：`backend/app/settings.py`、`backend/app/db/engine.py`、`backend/app/db/models.py`、`backend/app/db/tenant.py`、`backend/alembic/versions/`。
