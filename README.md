# CostGraph

> 用可追溯的成本图，把制造费用还原到每一个产成品批次。

[快速开始](#快速开始) · [工作原理](#工作原理) · [项目文档](docs/README.md) · [参与贡献](#参与贡献) · [MIT License](LICENSE)

CostGraph 是一个面向制造业的开源成本分析应用。它将采购、生产工艺和实际领用关系组织成成本图，提供产成品批次总览、多口径成本分析、逐笔来源追溯，以及基于 Agent 的自然语言问答和结构化报表。

项目把 AI 与业务事实严格分开：LLM 负责理解问题和组织解释，权限、数据范围、批次关系、状态、金额与舍入均由服务端确定性代码和数据库约束负责。

## 核心能力

- **批次级成本图**：沿真实采购、工艺事件和领用边追溯多层投入，不以计划 BOM 代替实际发生。
- **多口径成本分析**：同时呈现六类制造成本、料工费和变动/固定成本视图。
- **可核验的金额计算**：全程使用 `Decimal`，由服务端完成 DAG 校验、成本卷积、分摊和舍入。
- **可控的 AI 工作流**：以单一 LangGraph 工作流完成意图识别、参数澄清、成本查询和报表生成。
- **持久化运行时**：PostgreSQL Run 队列、独立 Worker、租约、重试、Checkpoint、SSE 恢复和原子化产出。
- **租户与用户隔离**：运行数据、产出和成本事实按租户、主体、已发布快照及数据范围过滤。

## 当前范围

CostGraph 当前聚焦一条可以端到端运行的制造成本分析闭环：

```text
采购 / 工艺事件 -> 实际领用关系 -> 产成品批次 -> 成本卷积 -> 追溯与报表
```

已实现产成品批次总览、三套成本视图、批次追溯、Agent 问答，以及展示型和周期对比型结构化报表。库存结存、返工分支、副产品、异常工单、审批、PDF/Excel 导出和多 Agent 编排尚不在当前实现中。

> [!IMPORTANT]
> 当前仓库处于早期开发阶段，默认配置用于本地演示与验证。用于生产环境前，请自行补充身份认证、密钥管理、网络边界、监控和备份策略。

## 工作原理

```mermaid
flowchart LR
    UI[React / Vite] --> API[FastAPI API]
    API --> Policy[主体与策略门]
    Policy --> Queue[(PostgreSQL Run Queue)]
    Queue --> Worker[独立 Worker]
    Worker --> Graph[LangGraph 工作流]
    Graph --> Services[领域与服务层]
    Services --> CostDB[(已发布成本快照)]
    Services --> Calc[Decimal 成本卷积]
    Calc --> Artifact[结构化报表与 Trace]
    Artifact --> API
```

服务端职责边界如下：

| 层 | 主要职责 |
| --- | --- |
| 前端 | 查询、导航、会话、SSE 状态和可视化 |
| API | HTTP/SSE 传输、参数校验和错误映射 |
| 领域与服务 | 权限、数据范围、DAG 校验、成本计算和报表结构 |
| Repository | PostgreSQL 查询、隔离、幂等写入和 Run 租约 |
| Runtime / Worker | LangGraph 执行、重试、恢复、事件和终态提交 |

完整边界与数据流见[系统架构](docs/architecture/system.md)和[数据流](docs/architecture/data-flow.md)。

## 技术栈

- **前端**：React 18、TypeScript、Vite 7、Tailwind CSS 3、ECharts 6
- **后端**：Python 3.12、FastAPI、Pydantic 2、SQLAlchemy、Alembic
- **Agent**：LangGraph、DeepSeek（真实模型验证时可选）
- **基础设施**：PostgreSQL、独立 Worker

## 快速开始

### 1. 准备环境

需要：

- Windows PowerShell 5.1 或 PowerShell 7+
- Python 3.12
- Node.js 与 npm
- 一个可由当前用户创建 schema 的独立 PostgreSQL 数据库

只有真实模型验证需要 DeepSeek API Key；本地成本查询和离线测试不依赖它。

### 2. 克隆并配置

```powershell
git clone https://github.com/rain-knows/costgraph.git
cd costgraph
Copy-Item backend\.env.example backend\.env
```

编辑 `backend/.env`，至少配置数据库连接：

```dotenv
COST_DATABASE_URL=postgresql+psycopg://user:password@127.0.0.1:5432/costgraph
```

如需连接真实模型，再填写 `DEEPSEEK_API_KEY`。不要提交数据库密码或 API Key。

### 3. 安装、迁移并导入样例

```powershell
py -3.12 -m venv backend\.venv
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt

Push-Location backend
.\.venv\Scripts\alembic.exe upgrade head
Pop-Location

$batch = backend\.venv\Scripts\python.exe backend\scripts\import_cost_data.py | ConvertFrom-Json
backend\.venv\Scripts\python.exe backend\scripts\inspect_cost_batch.py $batch.batch_id
```

导入完成后，应确认批次状态为 `published` 且 `error_rows=0`。样例 JSON 只用于导入，不是应用运行时的数据源。

### 4. 启动应用

```powershell
.\dev.ps1 start
```

打开：

- Web 应用：<http://127.0.0.1:5173/>
- API 健康检查：<http://127.0.0.1:8000/api/health>
- Runtime 就绪检查：<http://127.0.0.1:8000/api/readyz>

常用开发命令：

```powershell
.\dev.ps1 status
.\dev.ps1 logs -Tail 100
.\dev.ps1 restart
.\dev.ps1 stop
```

数据库必须由 Alembic 管理。已有旧开发库不会自动升级为当前 baseline，请按照[运行手册](docs/operations/runbook.md)使用新的专用空库。

## 项目结构

```text
frontend/        React 应用
backend/         FastAPI、Agent、Worker、Alembic 与测试
data/samples/    仅用于导入的样例成本数据
docs/            按职责拆分的项目事实文档
design-systems/  固定版本的 IBM-inspired 设计资源
scripts/         仓库级质量与文档检查
```

## 项目文档

[`docs/README.md`](docs/README.md) 是项目事实的统一入口，按问题链接到架构、数据、API、运行和治理文档。常用入口：

- [系统架构](docs/architecture/system.md)
- [Runtime 生命周期](docs/architecture/runtime.md)
- [成本核算格式与公式](docs/data/cost-accounting-format.md)
- [Agent API 契约](docs/api/agent-contract.md)
- [成本数据 API 契约](docs/api/cost-data-contract.md)
- [本地运行与排障](docs/operations/runbook.md)
- [界面设计约束](DESIGN.md)

当前代码、唯一 Alembic baseline、自动化测试和实际运行结果是事实依据；计划文档或截图不能替代当前实现。

## 开发与验证

```powershell
$env:PYTHONPATH="$PWD\backend"
backend\.venv\Scripts\python.exe -m pytest backend\tests -q
.\scripts\check_python_quality.ps1

Push-Location frontend
npm test
npm run test:bundle
Pop-Location

.\scripts\check_docs_sync.ps1
git diff --check
```

未配置 `TEST_COST_DATABASE_URL` 时，PostgreSQL 集成测试会明确跳过；离线 Fixture 与 Eval 通过也不代表真实 DeepSeek 服务可用。完整验证矩阵见[运行手册](docs/operations/runbook.md#验证矩阵)。

## 参与贡献

欢迎通过 [Issues](https://github.com/rain-knows/costgraph/issues) 报告问题、讨论需求或提交改进建议。开始编码前，请先：

1. 阅读[项目事实文档](docs/README.md)和[协作规则](AGENTS.md)。
2. 确认提议属于当前产品范围；未实现的规划只写入 `docs/plans/`。
3. 为行为变更补充相应测试，并同步受影响的原子文档。
4. 提交前运行仓库验证命令，并在变更说明中列出未执行的检查。

本项目不以向后兼容为目标：废弃的接口、配置和代码路径会直接移除，不新增兼容层或静默回退。

## License

除另有注明外，CostGraph 自有代码与文档基于 [MIT License](LICENSE) 开源。仓库内引入的第三方资源保留各自许可证；例如 `design-systems/ibm/` 遵循其目录中的 [Apache License 2.0](design-systems/ibm/LICENSE) 与[来源说明](design-systems/ibm/UPSTREAM.md)。
