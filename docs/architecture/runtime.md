# 架构/Runtime

## 责任边界

Runtime 管理一次持久化 Run 的执行生命周期，不定义成本公式、不授予客户端权限，也不创建第二个 Agent Loop。LangGraph 是唯一 Workflow Engine；`RuntimeServices` 是图节点调用模型和工具的唯一控制面，`LangGraphRuntimeAdapter` 将图状态投影为公开结果。

```text
LangGraph node -> RuntimeServices
  -> cancellation/deadline/lease check
  -> Harness + Provider/Tool preflight
  -> atomic budget reservation -> bounded call
  -> output validation -> event + trace
```

生产请求只进入 PostgreSQL Run 队列，由独立 Worker 执行。Fixture Provider、Fixture Tool 和直接图调用只存在于自动化测试、Replay 与离线 Eval，不构成应用运行模式或故障回退。生产模型配置、传输或结构化响应失败时，Runtime 不继续执行后续节点，不读取额外成本事实，不生成 `report_json` 或 Artifact；Worker 按 `model_unavailable` 的重试策略处理，最终状态为 `failed` 或 `retry_wait`。

## 统一任务调度器

`backend/app/worker.py` 的 `AgentWorker` 是单一 Worker 进程，内置两个独立领取路径：一条处理
agent run（既有路径，未被导入任务重构），一条处理数据导入任务。两条路径复用同一 worker 注册表、
`worker_id` 与心跳，但各自独立领取与并发控制。导入任务的领取循环是
`run_import_jobs()`（线程池）与 `process_next_import_job()`（同步单步，供 CLI 与端到端验证）。

导入任务调度契约：

- **领取**：`claim_next_job(worker_id)` 在 `status IN ('submitted','approved')` 或租约已过期的
  `validating`/`publishing` 行上用 `FOR UPDATE SKIP LOCKED` 加行锁，取最早 `created_at` 的一条；
  同时递增 `attempt_count`、写入 `lease_owner`/`lease_expires_at`、首次写入 `started_at`。
- **状态推进**：`submitted` 领取即转 `validating`；`approved` 领取即转 `publishing`。
- **租约与心跳**：租约时长 `IMPORT_JOB_LEASE_SECONDS = 90` 秒；续租与每次 Worker 写入均要求
  `lease_expires_at > now`，过期即失去写权限，即使尚无其他 Worker 接管。任务执行期间后台线程每
  `AGENT_RUN_HEARTBEAT_SECONDS` 调用 `renew_job_lease()` 延长，更新条件是
  `lease_owner == worker_id AND status IN ('validating','publishing') AND lease_expires_at > now`。续租失败或返回零行表示租约丢失，
  Worker 放弃本任务（`ImportJobLeaseLostError`），由其他 Worker 在租约过期后接管。
- **重试退避**：可重试失败（数据库/对象存储瞬时故障）不改状态，只把 `available_at` 推后
  `RETRY_BACKOFF_SECONDS = (1, 5, 30)`（按 `attempt_count` 取上限）并释放租约。不可重试失败
  （载荷、完整性、`object_key` 语义错误）转入终态：校验阶段转 `validation_failed`，发布阶段转
  `publish_failed`。`publish_failed` 是终态，不会自动重新领取，也没有人工重试接口。可重试故障最多执行 4 次（含首次领取）；达到上限后也进入对应失败终态，
  不再重新领取。若 Worker 在最后一次领取后崩溃，下一次租约回收会补写失败终态和脱敏审计。
- **失败分类**：`classify_import_job_exception()` 把异常映射为 `(retryable, error_code)`；
  `ImportObjectStoreError` 不可重试，`SQLAlchemyError`/`OSError` 可重试，载荷解码错误不可重试。
- **执行内容**：`validating` 任务调用 `CostDataImporter.validate_payload()`，比对载荷哈希与任务
  `snapshot_hash`；`publishing` 任务调用 `publish_validated_snapshot()`（单事务），成功后写
  `published_batch_id`。
- **审计**：校验/发布结果与 `publishing` 开始各追加一条治理审计事件；可重试失败不追加终态事件。

导入任务状态机的合法迁移、幂等与快照不可变性见[治理与质量](../data/governance.md)；
接口契约见[数据治理契约](../api/data-admin-contract.md)。

### 租户上下文注入

两条领取路径（Run 与导入任务）都在领取后先以任务/运行行上的 `tenant_id` 调用
`backend/app/db/tenant.py` 的 `set_tenant()` 写入 `app.tenant_id`，再执行任何业务查询；
完成或异常退出时通过 `release_session()` 摘除身份映射、回滚、清空该设置并关闭连接。Worker 不重新解析客户端
身份，也不从环境变量推导租户：租户和主体全部来自已固化的 Run / Import Job 行。

Worker 运行路径通过受控表所有者连接完成跨租户调度与多表发布。`costgraph_worker` 调度角色及其两张表的跨租户 RLS 策略分别由 `20260921_0005` 和 `20261021_0006` 创建，并由 PostgreSQL 集成测试验证；失败原因和重试排期由 `20261021_0007` 写入审计；Worker 领取后仍以任务行固化的 `tenant_id` 注入业务会话。

`release_session()` 必须先 `expunge_all()` 再 `rollback()`：`rollback()` 会把身份映射
实例标记为 expired，而仓储习惯在会话关闭后把 ORM 行映射成 dict，后置属性访问会抛
`DetachedInstanceError`。契约由 `backend/tests/test_tenant_context.py` 固定。

ContextVar 不能用于这条链路：FastAPI 同步依赖运行在线程池的上下文拷贝中，依赖里写入的值
无法回传到 endpoint 与 Repository，只剩仓储里无人读取的副本。

## 生命周期与协议

公开持久化协议固定为 `2.0`，Run 状态机为：

```text
queued -> running -> finalizing -> succeeded
                 -> retry_wait -> running
                 -> failed
queued/running/retry_wait -> cancelled
```

- API 创建 Run 时固化问题、会话上下文、最近消息、主体（含 `data_scope`）、路由请求、能力请求、版本和预算快照。
- `RuntimeRunRequest.principal` 必填；staging/production 的主体来自 OIDC 认证后的 `ExecutionPrincipal`，本地 demo 只允许显式固定开发主体，不存在匿名主体或通用配置注入路径。
- Worker 使用 `FOR UPDATE SKIP LOCKED` 领取 Run，并维持租约和心跳。
- 主体身份在运行边界绑定：Worker 用 Run 行和 `request_json["principal"]` 校验固化主体（含 `data_scope`），再用 `execution_context_from_state(..., authoritative_principal=...)` 校验状态；跨租户或伪造 checkpoint 被 `PermissionError` 拒绝，不重新解析客户端身份。
- 治理审计事件只允许追加：`governance_audit_events` 上有 `BEFORE UPDATE OR DELETE` 触发器（`trg_governance_audit_events_append_only`）直接报错，不依赖调用方自觉。
- LangGraph 以 `run_id` 作为 checkpoint `thread_id`；首次执行传入初始 State，接管时从 checkpoint 继续。
- 图终态先进入 `finalizing`，再在一个数据库事务中写入 Turn、用户/助手消息、Audit、Artifact、会话上下文、结果事件和 `succeeded`。
- `finalizing` 重试读取终态 checkpoint，不重新执行整张图。

前端 reducer 以 Runtime 生命周期为唯一外层状态源，并按 SSE sequence 去重。节点状态只补充当前步骤、路由和进度；失败和取消保持终态，直到新提交或切换会话。

成本核算路由的公开运行事件使用 v2 节点与工具标识：`resolve_part`、
`load_finished_batches`、`calculate_finished_batch_cost` 和 `build_report`。
Runtime 只记录这些调用的元数据与结果状态，不在生命周期层复制批次成本计算。

## 预算、超时和失败分类

每个 `RuntimeServices` 实例只服务一次图执行，持有 `run_id`、attempt、调用序号、deadline 和可替换时钟。默认上限为模型调用 3 次、工具调用 12 次、单工具 15 秒、Run 180 秒；实际工具超时取工具声明、服务端上限与 Run 剩余时间的最小值。Provider token usage 记录进 Trace，不作为硬授权边界。

只有暂时性 Provider 429/5xx/传输错误、Repository 不可用、基础设施工具超时和租约丢失可以进入 `retry_wait`。授权、输入/输出 Schema、未知工具、结构化模型契约和 Run 总超时不可重试。调用前失败不消耗预算；预算预留与调用开始事件在同一 Run 行锁事务中提交。

## 恢复与幂等

恢复语义为 at-least-once：已 checkpoint 的节点不重做，崩溃时正在执行的节点可能重做，因此当前工具必须只读且幂等。调用 ID 由 Run、attempt、节点、类型、名称和序号稳定生成；事件键防止恢复时重复追加。预算 usage 跨 attempt 保留。

取消接口幂等。排队或等待重试的 Run 立即终止；运行中的 Run 记录取消请求，在节点和调用边界生效。终态 Run 不再改变。会话存在活跃 Run 时不能删除。

导入任务由同一 Worker 的独立租约循环执行。租约领取按 `FOR UPDATE SKIP LOCKED`，发布失败允许有限重试；载荷或完整性错误直接进入失败终态。发布事务同时写入新快照、将旧快照标为 `superseded`、推进任务为 `published` 并追加成功审计，因此查询不会观察到任务与快照状态分裂，也不会漏掉成功审计。

## 健康门禁

`/api/livez` 只表示 API 进程可响应；`/api/readyz` 与 `runtime_ready` 同时检查数据库、Alembic head 和 Worker 心跳，不调用付费模型。前端在 Runtime 不可用时禁止提交，不切换到另一套协议或本地执行。

就绪检查要求数据库 revision 等于代码 head；当前 head 以 Alembic 迁移链为准。
未升级到当前代码 head 的库会在 `/api/readyz` 报告 `alembic` 检查失败。

依据：`backend/app/agent/runtime.py`、`backend/app/agent/runtime_services.py`、`backend/app/services/runtime_service.py`、`backend/app/repositories/run_repository.py`、`backend/app/worker.py`、`backend/app/services/import_job_service.py`、`backend/app/repositories/postgres_import_job_repository.py`、`backend/app/db/tenant.py`。
