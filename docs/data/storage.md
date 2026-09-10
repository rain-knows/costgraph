# 数据/PostgreSQL 存储

## 唯一应用后端

API、Worker、会话、成本事实、checkpoint 和 Artifact 全部使用同一个 CostGraph PostgreSQL 数据库，通过四个 schema 划分所有权：

| schema | owner | 表 |
| --- | --- | --- |
| `cost_data` | 成本数据服务 | `data_load_batches`、`data_load_errors`、`parts`、`cost_events`、`cost_event_inputs`、`cost_records` |
| `agent_runtime` | 会话与运行服务 | `conversations`、`turns`、`messages`、`audit_traces`、`agent_runs`、`agent_run_events`、`agent_workers` |
| `agent_checkpoint` | LangGraph 恢复状态 | `checkpoint_migrations`、`checkpoints`、`checkpoint_blobs`、`checkpoint_writes` |
| `agent_output` | 结构化产出服务 | `artifacts` |

应用运行面没有可切换的本地存储适配器。`data/samples/*.json` 只由导入脚本读取并发布进 PostgreSQL；Fixture Repository 只能由测试显式注入。

## 连接与迁移

- 唯一数据库连接配置是 `COST_DATABASE_URL`，由 `pydantic-settings` 从 `backend/.env` 与环境变量读取，环境变量优先。
- URL 使用 SQLAlchemy psycopg 方言，例如 `postgresql+psycopg://user:password@host:5432/costgraph`。密码不得进入源码、文档、日志或 Trace。
- 连接池启用 pre-ping；pool size、overflow 和超时使用服务端配置的有界值。
- Alembic revision `20260907_0001` 是唯一 CostGraph baseline，也是 schema 的唯一创建入口。API 和 Worker 启动时不自动创建业务表，也不调用 checkpoint saver 的 setup。
- `dev.ps1` 只负责启动 API、独立 Worker 和 Vite，并等待 `/api/readyz`；它不执行 Alembic、创建表或清理数据库，启动阶段进程提前退出时会立即报告对应日志。
- `/api/readyz` 校验数据库连通、数据库 revision 等于代码 head、四个 schema 的必需表和列存在，以及 Worker 心跳。使用 SQLAlchemy Inspector 按 schema 批量反射列定义；缺表或缺列返回 `checks.alembic=schema_mismatch`，服务端日志记录缺失对象。此检查不比较列类型、索引和约束，也不代替 Alembic。任一项失败时 Runtime 不接收新 Run。

## 读取与所有权边界

- 成本读取必须匹配 `tenant_id`、当前 `published` 批次、产成品零件和期间，并应用服务端 `ExecutionContext.data_scope`。
- 会话、Turn、消息、Run、Trace 和 Artifact 的业务查询必须匹配 `tenant_id + principal_id`。
- Worker 只领取数据库中已固化可信 owner 快照的 Run，不从客户端重新解释主体。
- `raw_payload`/JSONB 只做导入追溯，不直接参与金额计算。累计成本、单位成本、占比和三视图汇总均为查询时派生值，不落库。
- Artifact 只保存业务关联 ID，不对 Runtime 表建跨 schema 外键；删除会话不会删除独立历史产出。
- checkpoint `thread_id` 固定为 `run_id`，没有公共读取 API，并按终态保留规则清理。

字段和约束见 [数据字典](data-dictionary.md)，字段格式、48 项费用代码与公式见[成本核算格式](cost-accounting-format.md)，导入发布规则见 [治理与质量](governance.md)。

依据：`backend/app/settings.py`、`backend/app/db/engine.py`、`backend/app/db/models.py`、`backend/alembic/versions/`。
