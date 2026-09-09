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

## 生命周期与协议

公开持久化协议固定为 `2.0`，Run 状态机为：

```text
queued -> running -> finalizing -> succeeded
                 -> retry_wait -> running
                 -> failed
queued/running/retry_wait -> cancelled
```

- API 创建 Run 时固化问题、会话上下文、最近消息、主体、路由请求、能力请求、版本和预算快照。
- Worker 使用 `FOR UPDATE SKIP LOCKED` 领取 Run，并维持租约和心跳。
- LangGraph 以 `run_id` 作为 checkpoint `thread_id`；首次执行传入初始 State，接管时从 checkpoint 继续。
- 图终态先进入 `finalizing`，再在一个数据库事务中写入 Turn、用户/助手消息、Audit、Artifact、会话上下文、结果事件和 `succeeded`。
- `finalizing` 重试读取终态 checkpoint，不重新执行整张图。

前端 reducer 以 Runtime 生命周期为唯一外层状态源，并按 SSE sequence 去重。节点状态只补充当前步骤、路由和进度；失败和取消保持终态，直到新提交或切换会话。

## 预算、超时和失败分类

每个 `RuntimeServices` 实例只服务一次图执行，持有 `run_id`、attempt、调用序号、deadline 和可替换时钟。默认上限为模型调用 3 次、工具调用 12 次、单工具 15 秒、Run 180 秒；实际工具超时取工具声明、服务端上限与 Run 剩余时间的最小值。Provider token usage 记录进 Trace，不作为硬授权边界。

只有暂时性 Provider 429/5xx/传输错误、Repository 不可用、基础设施工具超时和租约丢失可以进入 `retry_wait`。授权、输入/输出 Schema、未知工具、结构化模型契约和 Run 总超时不可重试。调用前失败不消耗预算；预算预留与调用开始事件在同一 Run 行锁事务中提交。

## 恢复与幂等

恢复语义为 at-least-once：已 checkpoint 的节点不重做，崩溃时正在执行的节点可能重做，因此当前工具必须只读且幂等。调用 ID 由 Run、attempt、节点、类型、名称和序号稳定生成；事件键防止恢复时重复追加。预算 usage 跨 attempt 保留。

取消接口幂等。排队或等待重试的 Run 立即终止；运行中的 Run 记录取消请求，在节点和调用边界生效。终态 Run 不再改变。会话存在活跃 Run 时不能删除。

## 健康门禁

`/api/livez` 只表示 API 进程可响应；`/api/readyz` 与 `runtime_ready` 同时检查数据库、唯一 Alembic head 和 Worker 心跳，不调用付费模型。前端在 Runtime 不可用时禁止提交，不切换到另一套协议或本地执行。

依据：`backend/app/agent/runtime.py`、`backend/app/agent/runtime_services.py`、`backend/app/services/runtime_service.py`、`backend/app/repositories/run_repository.py`、`backend/app/worker.py`。
