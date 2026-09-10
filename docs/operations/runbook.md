# 运行/迁移/验证

## 前置条件

- Python 3.12 与项目虚拟环境。
- Node.js/npm 与 `frontend/package-lock.json` 锁定依赖。
- 新建且只供 CostGraph 使用的 PostgreSQL 数据库；迁移和导入账号需拥有四个 schema 的创建与写入权限。
- 真实模型检查时才需要 DeepSeek API Key。Fixture 和离线 Eval 不证明模型可用。

唯一连接变量为：

```dotenv
COST_DATABASE_URL=postgresql+psycopg://user:password@127.0.0.1:5432/costgraph
```

本地非敏感配置写入 `backend/.env`，环境变量优先。不要打印或提交连接密码和 API Key。

## 空库迁移与导入

当前唯一 baseline 直接定义批次成本 v2，不提供旧产品期间 schema 的升级或数据转换。已经运行旧 baseline 的开发环境应新建专用空库并修改 `backend/.env` 的 `COST_DATABASE_URL`，然后执行下述迁移与样例导入；旧库保留供操作者另行处置。也可在操作者显式授权删除后重建原专用数据库。`dev.ps1`、API 和 Worker 均不得静默清库或自动建表。

```powershell
cd D:\work\costgraph
Copy-Item backend\.env.example backend\.env
$env:COST_DATABASE_URL="postgresql+psycopg://user:password@127.0.0.1:5432/costgraph"
py -3.12 -m venv backend\.venv
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
Push-Location backend
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\alembic.exe current
Pop-Location
$batch = backend\.venv\Scripts\python.exe backend\scripts\import_cost_data.py | ConvertFrom-Json
backend\.venv\Scripts\python.exe backend\scripts\inspect_cost_batch.py $batch.batch_id
```

导入命令默认读取 `data/samples/parts.json`、`cost_events.json`、`cost_event_inputs.json` 和 `cost_records.json`。退出码 0 仍需核对：

- 批次状态为 `published`，`error_rows=0`。
- 四类行数与样例文件一致。
- 3 个汽车装饰件产成品跨 2 个期间形成 6 个最终批次；黄金批次应追溯到注塑成型、火焰处理与表皮包覆、卡扣压装与门板总成装配三道连续工序。
- 当前租户只有一个 `published` 快照。

格式、关系和 48 项代码见[成本核算格式](../data/cost-accounting-format.md)。

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

`dev.ps1 status` 单独显示 Runtime 的 `READY/NOT READY`，进程 UP 不代表成本数据可用。`readyz.checks.alembic=schema_mismatch` 表示实际库缺少当前模型要求的表或列，`dev.ps1 logs` 的后端错误日志会列出缺失对象。历史开发 baseline 可能与当前 baseline 共用 `20260907_0001`，因此 `alembic current` 为 head 或重复执行 `upgrade head` 都不能证明旧库表结构已更新。按“空库迁移与导入”切换到新库后执行 `dev.ps1 restart`，再验证下面的黄金批次 API。

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

- 导入测试覆盖未知费用代码、孤立引用、环路、跨快照、单位不一致和超量领用。
- 成本测试覆盖多投入、部分领用、同批次分流、不良成本承接、全量领用尾差、三视图恒等式和黄金结果。
- PostgreSQL 集成覆盖发布、数据范围、owner 隔离、Run 幂等、单会话单活跃、SSE 恢复、取消、租约接管、checkpoint 恢复和 finalizing 原子提交。未配置 `TEST_COST_DATABASE_URL` 导致的 skip 必须单独报告。
- 离线 Agent/Eval 必须覆盖零件与期间澄清、Report/Artifact `3.0`、`presentation/period_comparison` 两种样式、`report_generation` 能力路由和四张表 lineage。Fixture 结果不能替代真实 DeepSeek。
- 前端测试覆盖三页签、URL 状态、批次跳转、共享节点引用、来源记录及加载/空/错误/无权限状态。
- 生产构建必须通过 bundle 门禁，初始 JS 小于 500 KB；页面与 ECharts 保持路由级懒加载。
- 浏览器验收覆盖成本页、批次详情以及展示型/周期对比报表的桌面/移动、浅/深主题；对比报表必须清晰标注基期与目标期，并在窄屏保留可横向查看的精确数值表，不得出现表头错位、文字溢出或遮挡。

真实 DeepSeek 检查单独执行并记录时间、provider、model 和结果；它可能产生少量费用，不能成为默认测试副作用。

## 故障顺序

1. 先查 `/api/health` 和 `/api/readyz` 的 database、migration、worker 与 `runtime_ready`。
2. 确认连接配置存在但不要打印秘密；检查 `alembic current`、8000/5173 端口和 `dev.ps1 logs`。
3. 检查当前 `published` 导入批次、错误码和四类记录计数；旧 schema 数据库不能通过重复执行同 revision 的 `upgrade head` 转换。
4. 检查完工批次所属期间、追溯 DAG、领用总量、单位和 48 项费用代码。
5. 检查服务端 tenant、principal 和 data scope；最终批次及其整条上游追溯链必须同时可见。
6. 检查 Run 状态、attempt、可用时间、租约 owner/过期时间和最后事件 ID；有效租约期间不要手工重复执行。
7. 最后再做显式 DeepSeek 连通性检查。
