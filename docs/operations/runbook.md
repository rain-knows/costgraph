# 运行/迁移/验证

## 前置条件

- Python 3.12 与项目虚拟环境。
- Node.js/npm 与 `frontend/package-lock.json` 锁定依赖。
- 新建且只供 CostGraph 使用的 PostgreSQL 数据库；迁移和导入账号需拥有四个 schema 的创建与写入权限。
- 真实模型检查时才需要 DeepSeek API Key。Fixture 和离线 Eval 不证明模型可用。

唯一连接变量为：

```dotenv
COST_DATABASE_URL=postgresql+psycopg://user:password@127.0.0.1:5432/costgraph
OIDC_ISSUER=https://idp.example.com
OIDC_AUDIENCE=costgraph
OIDC_JWKS_URI=https://idp.example.com/.well-known/jwks.json
```

`OIDC_TENANT_CLAIM`（默认 `tenant_id`）、`OIDC_ROLES_CLAIM`（默认 `roles`）、
`OIDC_JWKS_CACHE_SECONDS`（默认 600）与 `OIDC_LEEWAY_SECONDS`（默认 30）可按身份提供方调整。

本地非敏感配置写入 `backend/.env`，环境变量优先。不要打印或提交连接密码和 API Key。

导入载荷写入本地内容寻址对象存储。目录可用 `IMPORT_OBJECT_STORE_ROOT` 覆盖，缺省为系统临时目录下
`costgraph-import-objects`。该目录必须对 API 与 Worker 进程可写，且属于受控运行数据，不得提交。

### 身份提供方与角色

身份只来自 OIDC，没有配置主体：

- `OIDC_ISSUER`、`OIDC_AUDIENCE`、`OIDC_JWKS_URI` 在 `staging`/`production` 未配置时启动即失败，
  不存在"默认租户"或匿名主体回退。
- 令牌 `sub` 作为 `principal_id`，租户来自 `OIDC_TENANT_CLAIM`，角色来自 `OIDC_ROLES_CLAIM`；
  令牌角色必须与 `cost_data.iam_principal_roles` 的本地成员关系取交集后才生效。
- 新主体先由 `tenant_admin` 调用 `PUT /api/iam/principals/{principal_id}/roles` 建立成员关系，
  否则该主体一律 403 `principal_not_in_tenant`。
- 数据范围默认不收窄；需要限制零件/期间时调用 `PUT /api/iam/principals/{principal_id}/scope`。
- `AGENT_PRINCIPAL_ID`、`AGENT_TENANT_ID`、`AGENT_PRINCIPAL_ROLES`、`AGENT_ALLOWED_PART_IDS`、
  `AGENT_ALLOWED_PERIOD_START`、`AGENT_ALLOWED_PERIOD_END` 已删除，配置旧变量不会恢复生效。

角色 -> 权限映射见 [Harness](../architecture/harness.md)，接口细节见 [IAM 契约](../api/iam-contract.md)。
修改角色或范围后无需重启：API 每次请求重新读取成员关系与数据范围。

## 空库迁移与导入

迁移链以 `20260907_0001_costgraph_baseline.py` 为根节点，当前 head 为 `20260918_0003`。
baseline 直接定义批次成本 v2，不提供旧产品期间 schema 的升级或数据转换；增量迁移
`20260917_0002` 新增导入任务与治理审计表，并把 `batch_id` 入键改造为不可变快照；
`20260918_0003` 新增 IAM 主体角色/数据范围两张表，并扩展审计动作集合。
已经运行旧 baseline 的开发环境应新建专用空库并修改 `backend/.env` 的 `COST_DATABASE_URL`，
然后执行下述迁移与样例导入；旧库保留供操作者另行处置。也可在操作者显式授权删除后重建原专用数据库。
`dev.ps1`、API 和 Worker 均不得静默清库或自动建表。

迁移同时创建 `finished_batch_cost_projections`。导入会在发布事务中完成事实写入、420 个样例成品
批次的确定性卷积和查询投影发布；首次导入比普通查询耗时更长属于预期。规则版本改变时，即使来源
哈希不变也必须重新提交并生成新发布批次。

```powershell
cd D:\work\costgraph
Copy-Item backend\.env.example backend\.env
$env:COST_DATABASE_URL="postgresql+psycopg://user:password@127.0.0.1:5432/costgraph"
$env:IMPORT_OBJECT_STORE_ROOT="D:\work\costgraph\.import-objects"
py -3.12 -m venv backend\.venv
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
Push-Location backend
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\alembic.exe current
Pop-Location
```

`alembic current` 必须输出 `20260917_0002 (head)`。API、Worker 和 `dev.ps1` 都不执行 Alembic。

## 提交导入任务与验证

`backend/scripts/import_cost_data.py` 只**提交**导入任务，不再直接发布。校验、投影与发布由
Worker 完成，可用 `--auto-approve` 驱动完整状态机：

```powershell
cd D:\work\costgraph
$env:PYTHONPATH="D:\work\costgraph\backend"
# 只提交（需 data_operator；Worker 必须运行才能推进到 approval_pending）：
$job = backend\.venv\Scripts\python.exe backend\scripts\import_cost_data.py | ConvertFrom-Json
# 提交 + 审批 + 发布（以 tenant_admin 驱动，供开发/端到端验证；不需要另起 Worker）：
$job = backend\.venv\Scripts\python.exe backend\scripts\import_cost_data.py --auto-approve | ConvertFrom-Json
$job
backend\.venv\Scripts\python.exe backend\scripts\inspect_cost_batch.py $job.job_id
```

`--auto-approve` 输出 `job_id`、`status`、`duplicate`、`snapshot_hash`、`source_object_key`、
`total_rows`，以及 `validation_status`/`decision_status`/`publish_status`。任务发布后的批次
`batch_id` 等于 `job_id`，因此 `inspect_cost_batch.py` 传入 `$job.job_id`；若只提交（无
`--auto-approve`），该批次尚不存在，需等 Worker 走完状态机。

其他可用参数：`--source-system`、`--calculation-rule-version`、`--idempotency-key`、`--tenant-id`、
`--principal-id`。同租户同 `--idempotency-key` 重复提交返回已有任务（`duplicate: true`），不新建。

导入命令默认读取 `data/samples/parts.json`、`cost_events.json`、`cost_event_inputs.json` 和
`cost_records.json`。退出码 0 仍需核对：

- 任务状态为 `published`，`error_rows=0`；发布批次状态为 `published`。
- 四类行数与样例文件一致。
- 100 个产成品 SKU 覆盖 24 个自然月，每月至少 10 个最终批次；全部 SKU 至少可做同比，核心 SKU 可做连续环比。
- 固定黄金子集仍为 3 个汽车装饰件产成品跨 2 个期间形成的 6 个最终批次，并应追溯到注塑成型、火焰处理与表皮包覆、卡扣压装与门板总成装配三道连续工序。
- 当前租户只有一个 `published` 快照；旧快照在发布事务提交后转 `superseded`。

直接用 API 验证任务状态机（需要带 `Idempotency-Key` 和对应权限的服务端角色）：

```powershell
$headers = @{ "Idempotency-Key" = "runbook-$(Get-Date -Format yyyyMMddHHmmss)" }
$body = @{
    source_system = "mes"; source_file_name = "cost_events.json"
    calculation_rule_version = "cost-v1"
    payload = @{ parts = @(); cost_events = @(); cost_event_inputs = @(); cost_records = @() }
} | ConvertTo-Json -Depth 6
$submission = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/data-admin/imports" -Headers $headers -ContentType "application/json" -Body $body
$submission | ConvertTo-Json -Depth 5
Invoke-RestMethod "http://127.0.0.1:8000/api/data-admin/imports/$($submission.job_id)"
Invoke-RestMethod "http://127.0.0.1:8000/api/data-admin/imports/$($submission.job_id)/errors"
```

审批（`data_approver` 或 `tenant_admin`）：

```powershell
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/data-admin/imports/$($submission.job_id)/decision" -ContentType "application/json" -Body '{"decision":"approve","reason":"runbook"}'
```

缺少 `Idempotency-Key` 必须返回 `422/idempotency_key_required`；权限不足必须返回 `403`（错误码
以 `data_import_submit_access_denied`/`data_import_approve_access_denied` 区分）；非
`approval_pending` 状态审批必须返回 `409/import_job_state_conflict`；跨租户任务必须返回
`404/import_job_not_found`。响应中永远不应出现 `payload`、`raw_payload` 或对象内容。

如需从规则确定性重建仓库内样例，先运行：

```powershell
backend\.venv\Scripts\python.exe backend\scripts\generate_sample_cost_data.py
```

输出摘要应显示 `finished_products >= 100`、`periods == 24` 且 `minimum_finished_batches_per_period >= 10`；随后仍须执行正式导入命令，生成脚本本身不会写数据库。

导入后可执行只读性能验收：

```powershell
backend\.venv\Scripts\python.exe backend\scripts\benchmark_cost_queries.py
```

脚本预热后分别执行总览、默认列表、单位成本排序、过滤列表和详情各 10 次；每项必须满足 `p95_seconds < 1.0` 且 `max_queries <= 2`（包含 `SET LOCAL`）。

格式、关系和 48 项代码见[成本核算格式](../data/cost-accounting-format.md)。

## 数据治理接口验证

默认主体角色为 `viewer`，因此不会获得数据治理读取权限。验证只读治理面时把角色改为
`data_operator`/`data_approver`/`tenant_admin`，重启 API 后确认：

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/me"
Invoke-RestMethod "http://127.0.0.1:8000/api/data-admin/batches?page=1&page_size=20"
Invoke-RestMethod "http://127.0.0.1:8000/api/data-admin/published"
Invoke-RestMethod "http://127.0.0.1:8000/api/data-admin/quality"
Invoke-RestMethod "http://127.0.0.1:8000/api/data-admin/imports?page=1&page_size=20"
```

`/api/me` 必须返回当前角色的有效权限；`viewer` 访问上述治理接口固定 `403`。数据库不可用或
当前规则投影不完整时应看到 `503/database_unavailable`，而不是空数据。

批次响应新增可选 `job_status`；导入任务响应不含 `lease_owner`/`lease_expires_at`/`available_at`
和 `payload`。跨租户批次与任务按未找到（404）处理。

## 启动与健康

```powershell
cd D:\work\costgraph
.\dev.ps1 start
Invoke-RestMethod http://127.0.0.1:8000/api/livez
Invoke-RestMethod http://127.0.0.1:8000/api/health
Invoke-RestMethod http://127.0.0.1:8000/api/readyz
```

前端地址为 `http://127.0.0.1:5173/`。`dev.ps1` 统一管理 API、Worker 和 Vite：

```powershell
.\dev.ps1 status
.\dev.ps1 logs
.\dev.ps1 logs -Tail 100
.\dev.ps1 stop
.\dev.ps1 restart
```

`dev.ps1` 兼容 Windows PowerShell 5.1 与 `pwsh`；启动期间会监测新进程，若 API、Worker 或 Vite 在健康检查完成前退出，会立即返回并附对应错误日志末尾。

`dev.ps1 status` 单独显示 Runtime 的 `READY/NOT READY`，进程 UP 不代表成本数据可用。`readyz.checks.alembic=schema_mismatch` 表示实际库缺少当前模型要求的表或列，`dev.ps1 logs` 的后端错误日志会列出缺失对象。当前 head 为 `20260917_0002`；`alembic current` 必须显示该 revision，重复执行 `upgrade head` 不会修复历史库中与实际模型不一致的表结构。按“空库迁移与导入”切换到新库后执行 `dev.ps1 restart`，再验证下面的黄金批次 API。

`livez` 只检查进程，`readyz` 检查数据库、Alembic head、必需表和列以及 Worker 心跳。只有 `runtime_ready=true` 才能创建持久化 Run；健康检查不会调用 DeepSeek，也不校验列类型、索引和约束或是否已发布成本快照。

## 黄金批次 API 检查

以下命令从样例本身定位完工 `1000`、合格 `950`、不良 `50` 的黄金事件，避免把批次 ID 或期间复制成第二份配置：

```powershell
$events = Get-Content -Raw data\samples\cost_events.json | ConvertFrom-Json
$goldenEvent = $events | Where-Object {
    [decimal]$_.qualified_quantity -eq 950 -and [decimal]$_.defective_quantity -eq 50
} | Select-Object -First 1
$period = ([DateTimeOffset]::Parse($goldenEvent.completion_time)).ToString("yyyy-MM")
$overview = Invoke-RestMethod "http://127.0.0.1:8000/api/cost-data/overview?period=$period"
$list = Invoke-RestMethod "http://127.0.0.1:8000/api/cost-data/finished-batches?period=$period&page=1&page_size=100"
$detail = Invoke-RestMethod "http://127.0.0.1:8000/api/cost-data/finished-batches/$($goldenEvent.output_batch_id)"
$detail | ConvertTo-Json -Depth 20
```

详情必须满足：六类金额 `20000/8000/2000/4000/3000/13000`，制造成本 `50000.00`，料工费单位成本 `20.00/12.00/18.00`，制造单位成本1 `50.00`，变动/固定成本1 `30.00/20.00`，三项制造后单位成本 `1.00/2.00/0.50`，以及变动成本2、固定成本2、合计单位成本2 `33.00/20.50/53.50`。详情、Agent report schema `3.0` 和 Artifact 必须一致。服务端能力白名单默认包含 `system_help,cost_calculation,report_generation`；若部署配置覆盖该值，必须同时开放 `cost_calculation` 与依赖它的 `report_generation` 才能生成报表。

## 验证矩阵

```powershell
cd D:\work\costgraph
$env:PYTHONPATH="D:\work\costgraph\backend"
backend\.venv\Scripts\python.exe -m pytest backend\tests -q
.\scripts\check_python_quality.ps1
backend\.venv\Scripts\python.exe backend\scripts\verify_agent.py
backend\.venv\Scripts\python.exe backend\scripts\evaluate_agent.py
Push-Location frontend
npm test
npm run test:bundle
Pop-Location
.\scripts\check_docs_sync.ps1
git diff --check
```

`check_docs_sync.ps1` 默认只比较本工作树未提交改动，因此本地跑它必须保留工作区改动。CI 在干净检出上运行，此时它读取 `DOCS_SYNC_BASE_REF`（PR 基分支 SHA 或推送前 SHA）改为比较 `ref...HEAD`；首次推送或手动触发时该 ref 无法解析，脚本会警告并退回本地 diff 行为，不因基准缺失而失败。同一门禁由 `.github/workflows/ci.yml` 的 `docs` job 在 windows-latest 上执行。

CI 分三个 job：ubuntu 上跑后端（Ruff + Pytest）与前端（Vitest + 生产构建与 bundle 门禁），windows-latest 上跑文档同步与 `git diff --check`。CI 不配置 `COST_DATABASE_URL`，因此 PostgreSQL 集成测试固定为 3 个 skip，与下述验证矩阵一致。

- 导入测试覆盖未知费用代码、孤立引用、环路、跨快照、单位不一致和超量领用。
- 导入任务测试覆盖状态机全部合法/非法迁移、幂等提交、重复快照返回已有任务、取消、租约过期、重试退避、审批与发布事务原子性、新旧快照并存、跨租户隔离，以及审计不可修改且不含载荷。
- 成本测试覆盖多投入、部分领用、同批次分流、不良成本承接、全量领用尾差、三视图恒等式和黄金结果。
- PostgreSQL 集成覆盖发布、数据范围、owner 隔离、Run 幂等、单会话单活跃、SSE 恢复、取消、租约接管、checkpoint 恢复和 finalizing 原子提交。未配置 `TEST_COST_DATABASE_URL` 导致的 skip 必须单独报告。
- 离线 Agent/Eval 必须覆盖零件与期间澄清、Report/Artifact `3.0`、`presentation/period_comparison` 两种样式、`report_generation` 能力路由和四张表 lineage。Fixture 结果不能替代真实 DeepSeek。
- 前端测试覆盖三页签、URL 状态、批次跳转、共享节点引用、来源记录及加载/空/错误/无权限状态。
- 生产构建必须通过 bundle 门禁，初始 JS 小于 500 KB；页面与 ECharts 保持路由级懒加载。
- 浏览器验收覆盖成本页、批次详情以及展示型/周期对比报表的桌面/移动、浅/深主题；对比报表必须清晰标注基期与目标期，并在窄屏保留可横向查看的精确数值表，不得出现表头错位、文字溢出或遮挡。展示型报表可用仓库根目录的 `scripts/verify_reports_browser.ps1` 执行真实 Chromium CDP 验收；脚本输出 `.pi/goals/evidence/reports-browser/result.json` 和四种状态截图，并检查真实 ECharts canvas、横向溢出、报表关键区块和数量小数显示。

真实 DeepSeek 检查单独执行并记录时间、provider、model 和结果；它可能产生少量费用，不能成为默认测试副作用。

## 故障顺序

1. 先查 `/api/health` 和 `/api/readyz` 的 database、migration、worker 与 `runtime_ready`。
2. 确认连接配置存在但不要打印秘密；检查 `alembic current`、8000/5173 端口和 `dev.ps1 logs`。
3. 检查当前 `published` 导入批次、导入任务状态（`submitted/validating/approval_pending/approved/publishing/published/publish_failed`）、错误码和四类记录计数；旧 schema 数据库不能通过重复执行同 revision 的 `upgrade head` 转换。等待审批的任务不会发布；`publish_failed` 只允许重试发布，不回滚旧快照。
4. 检查完工批次所属期间、追溯 DAG、领用总量、单位和 48 项费用代码。
5. 检查服务端 tenant、principal 和 data scope；最终批次及其整条上游追溯链必须同时可见。
6. 检查 Run 状态、attempt、可用时间、租约 owner/过期时间和最后事件 ID；有效租约期间不要手工重复执行。
7. 最后再做显式 DeepSeek 连通性检查。
