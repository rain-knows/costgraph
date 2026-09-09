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

## 首次迁移与导入

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

应用不会自动创建业务表。仓库只保留 revision `20260907_0001` 这一条 CostGraph baseline；空库一次 `upgrade head` 即得到最终 schema。导入退出码 0 仍需核对批次为 `published`、错误数为 0，并用产品 A 的 `2026-06` 口径验收。

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

`livez` 只检查进程，`readyz` 检查数据库、Alembic head 和 Worker 心跳。只有 `runtime_ready=true` 才能创建持久化 Run；健康检查不会调用 DeepSeek。

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
npm run build
Pop-Location
.\scripts\check_docs_sync.ps1
git diff --check
```

- 离线测试覆盖路由、澄清、Decimal 结果、Runtime/Harness、SSE reducer、Replay 和固定 fixture Eval。
- PostgreSQL 集成覆盖导入发布、owner 隔离、Run 幂等、单会话单活跃、SSE 恢复、取消、租约接管、checkpoint 恢复和 finalizing 原子提交。测试数据库未配置导致的 skip 必须单独报告。
- 真实 DeepSeek 检查单独执行并标记时间、provider、model 和结果；它可能产生少量费用，不能成为默认测试副作用。
- 生产构建必须通过 bundle 门禁，初始 JS 小于 500 KB；页面与 ECharts 保持路由级懒加载。
- 浏览器验收覆盖深链、前进/后退、加载/空/错误/无权限、浅/深主题及桌面/移动截图，无重叠和 token 偏移。

产品 A、`2026-06` 的确定性验收值为：四道工序成本依次 `61200.00/29000.00/26400.00/22400.00`，总成本 `139000.00`，单位成本 `13.90`。成本详情、Agent 报告和 Artifact 必须一致。

## 故障顺序

1. 先查 `/api/health` 和 `/api/readyz` 的 database、migration、worker 与 `runtime_ready`。
2. 确认连接配置存在但不要打印秘密；检查 `alembic current`、8000/5173 端口和 `dev.ps1 logs`。
3. 检查 Run 状态、attempt、可用时间、租约 owner/过期时间和最后事件 ID；有效租约期间不要手工重复执行。
4. 检查当前 published 批次、导入错误和产品/期间数据。
5. 检查服务端 tenant、principal 和 data scope。
6. 最后再做显式 DeepSeek 连通性检查。
