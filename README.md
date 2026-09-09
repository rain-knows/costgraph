# CostGraph

CostGraph 是面向制造业成本分析的真实应用：LLM 负责理解请求与生成解释，服务端负责权限、数据范围、状态和 Decimal 金额计算。首版围绕“产品 + 期间/日期范围”打通成本数据、Agent 会话和可追溯报表，不展示尚未实现的批次、BOM、异常或审批流程。

## 技术与目录

- 前端：React 18、Vite 7、TypeScript、Tailwind CSS 3、ECharts 6、Lucide、IBM Plex。
- 后端：Python 3.12、FastAPI、LangGraph、Pydantic 2、SQLAlchemy、PostgreSQL、Alembic、DeepSeek。
- 运行：FastAPI API、独立 PostgreSQL Worker、Vite 前端；只有一套 LangGraph 外循环。

```text
frontend/        React 应用
backend/         FastAPI、Agent、Worker、Alembic 与测试
data/            只用于导入的样例成本数据
design-systems/  pinned IBM-inspired OpenDesign 包
docs/            原子事实文档与迁移记录
scripts/         仓库级检查脚本
```

项目事实从 [`docs/README.md`](docs/README.md) 查阅，界面约束从
[`DESIGN.md`](DESIGN.md) 查阅，协作规则见 [`AGENTS.md`](AGENTS.md)。

## 本地启动

前置条件是 Python 3.12、Node.js/npm、可创建独立数据库的 PostgreSQL，以及仅在真实模型验证时需要的 DeepSeek 凭据。CostGraph 不读取 Agent Demo 数据库。

```powershell
cd D:\work\costgraph
Copy-Item backend\.env.example backend\.env
$env:COST_DATABASE_URL="postgresql+psycopg://user:password@127.0.0.1:5432/costgraph"
py -3.12 -m venv backend\.venv
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
Push-Location backend
.\.venv\Scripts\alembic.exe upgrade head
Pop-Location
$batch = backend\.venv\Scripts\python.exe backend\scripts\import_cost_data.py | ConvertFrom-Json
backend\.venv\Scripts\python.exe backend\scripts\inspect_cost_batch.py $batch.batch_id
.\dev.ps1 start
```

打开 `http://127.0.0.1:5173/`，API 健康检查为
`http://127.0.0.1:8000/api/health`。进程和日志由同一入口管理：

```powershell
.\dev.ps1 status
.\dev.ps1 logs
.\dev.ps1 stop
.\dev.ps1 restart
```

环境变量可放在 `backend/.env`；密码和 API Key 不得提交。完整迁移、导入、健康门禁和故障顺序见
[`docs/operations/runbook.md`](docs/operations/runbook.md)。

## 验证

```powershell
$env:PYTHONPATH="D:\work\costgraph\backend"
backend\.venv\Scripts\python.exe -m pytest backend\tests -q
.\scripts\check_python_quality.ps1
Push-Location frontend
npm test
npm run build
Pop-Location
.\scripts\check_docs_sync.ps1
git diff --check
```

未配置测试数据库时，PostgreSQL 集成测试可能被明确跳过；离线测试通过不代表真实 DeepSeek 可用。验收口径见
[`docs/plans/agent-demo-to-costgraph.md`](docs/plans/agent-demo-to-costgraph.md)。
