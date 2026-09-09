# 架构/Tool 与 Provider

## Tool Registry

`ToolSpec` 是 Harness 的内部注册契约，包含工具 ID、描述、Draft 2020-12 输入/输出 Schema、只读/确定性/幂等属性、所需能力、数据范围、owner、风险、启用状态、timeout、retry policy、版本和 Schema 版本。

注册时校验 Schema 本身；执行前校验输入，执行后校验输出。`execution_context` 只由 Runtime 注入。当前工具只读且幂等：成本事实查询工具要求 `published_cost_data`，确定性计算与报表工具不额外读取事实，但仍要求 `cost_calculation` 能力。

Repository 的暂时性基础设施失败可重试；授权、未知工具、参数、Schema 与确定性业务校验不可重试。新增通用工具前先评估当前注册表、LangGraph 和已安装库，不增加第二套工具协议。

## Model Provider

`ModelProvider` 只公开已注册操作：

- `classify_route`
- `parse_cost_question`
- `generate_cost_analysis`

每个操作都有输出 JSON Schema，Provider 返回值在进入业务 State 前验证。生产 Adapter 使用配置的 DeepSeek HTTP API；配置、认证、模型可用性或结构化输出失败时显式失败，错误沿 Runtime 传播为 `model_unavailable`，图进入 `failed`，不生成报告或 Artifact，不用 fixture、规则文本或另一模型静默替代。Fixture Provider 只通过显式依赖注入服务于测试、Replay 和离线 Eval；生产 `run_runtime`/`stream_runtime` 未注入时始终使用 DeepSeek Adapter。

Replay 可以在其自身的固定 Fixture 中为未声明的模型响应构造确定性值，但该行为只属于 `app.agent.replay` 的离线回放边界，不会被生产节点或 Provider 调用。

Runtime 记录 provider、model、adapter version、usage、duration、timeout、结果形状和稳定错误分类，不记录 Prompt、模型正文、reasoning content 或密钥。Provider 429、5xx 和传输错误可重试；认证、请求、未知操作和结构化结果错误不可重试。

模型可以提出路由和槽位并生成解释，但最终路由、能力、权限、范围、金额和报表字段仍由服务端决定。

依据：`backend/app/agent/runtime_contracts.py`、`backend/app/agent/harness.py`、`backend/app/agent/providers.py`、`backend/app/agent/tools.py`、`backend/app/agent/runtime_services.py`。
