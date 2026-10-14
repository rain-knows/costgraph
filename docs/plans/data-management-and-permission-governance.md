# 数据管理面板与权限治理实施计划

> 状态：本轮实现完成；验证证据以当前代码、测试和交付记录为准
>
> 本计划基于已确认决策：
> 1. 数据管理面板只读起步；
> 2. 当前保持单用户运行模型，权限只分 `user` 与 `admin`；
> 3. 保留单一 Alembic baseline。

## 1. 目标与边界

### 1.1 本轮目标

交付一个可运行的只读数据治理面板，并将当前配置中的角色切换为两个代码级角色：

- `user`：保留当前普通用户能力：系统帮助、成本查询、报表生成和 AI 工作区。
- `admin`：继承 `user` 的全部能力，额外拥有只读数据治理权限。

数据治理面板用于查看导入批次、导入质量错误、当前发布快照和基础质量统计；所有数据仍由服务端从 PostgreSQL 确定性读取。

### 1.2 明确不做

本轮不实现：

- 登录、密码、OIDC、SSO、JWT、API Token 或完整身份认证；
- 多用户、主体表、角色绑定表或主体级数据范围表；
- 数据上传、导入、重新发布、审批、删除或修改事实；
- 快照回滚；
- 手工编辑零件、成本事件、投入边、费用记录或成本投影；
- 数据库 RLS；
- 新增数据库表或新增 Alembic migration 文件；
- 向 Agent 暴露数据管理接口。

当前身份仍由 `get_server_principal()` 从配置注入。由于这是单用户部署模型，`user/admin` 是当前部署主体的角色配置，不是多用户 IAM。

## 2. 现状依据与关键约束

- 导入入口只有 `backend/scripts/import_cost_data.py`；当前没有批次和错误读取 API。
- `cost_data.data_load_batches` 和 `cost_data.data_load_errors` 已存在，可直接作为只读面板数据源。
- `CostDataImporter.import_records()` 将校验、DAG 检查、确定性卷积、投影生成和发布放在同一事务中；本轮不把它改造成 HTTP 写操作。
- 当前导入使用 upsert 更新事实行的 `batch_id`，旧 `DataLoadBatch` 元数据会保留，但旧批次事实不是独立不可变快照。因此本轮只能展示 `published/superseded/failed` 历史，不能提供真实回滚。
- 当前角色在 `backend/app/domain/authorization.py` 硬编码，能力在 `backend/app/agent/capabilities.py` 定义；两处角色元数据需要统一，避免双重事实源。
- `DataScope` 仍来自全局配置的零件和期间范围；单用户范围内继续保留，不扩展到主体级治理。
- `scripts/check_docs_sync.ps1` 与 `backend/tests/test_baseline_and_boundaries.py` 均约束仓库只有一条 baseline。因为本轮不新增表，继续保留 `20260907_0001_costgraph_baseline.py`，不改迁移策略。

## 3. 目标权限模型

### 3.1 能力与业务权限分离

保留现有 `CapabilityId` 作为 Agent 路由能力，不把数据管理权限塞入 `cost_calculation`。

新增最小业务权限集合：

```text
PermissionId:
- data_management.read
```

后续可扩展的权限不在本轮实现，例如 `data.import.write`、`iam.grant.manage`、`cost.source_record.read`。

角色映射采用代码确定性配置：

```text
user:
  capabilities: system_help, cost_calculation, report_generation
  permissions: none

admin:
  capabilities: system_help, cost_calculation, report_generation
  permissions: data_management.read
```

角色直接从 `system_viewer/cost_analyst/agent_admin` 切换为 `user/admin`，不增加兼容别名或旧配置回退。默认开发配置改为 `user`；需要查看管理面板的部署通过 `AGENT_PRINCIPAL_ROLES=admin` 配置。

### 3.2 ExecutionContext 改造

在现有 `ExecutionContext` 上增加业务权限的确定性解析，保留现有能力交集不变量：

```text
有效能力 = requested_capabilities
         ∩ role_capabilities
         ∩ server_allowed_capabilities

有效权限 = requested_permissions
         ∩ role_permissions
         ∩ server_allowed_permissions
```

实施要求：

- 增加 `PermissionId` 类型、角色权限映射和 `require_permission()`；
- `data-admin` 服务只接受服务端固定请求的 `data_management.read`，客户端不能提交权限来授权自己；
- `ExecutionPrincipal` 继续是冻结值对象；
- `CapabilitySpec.required_roles` 与 `ROLE_CAPABILITIES` 的职责统一：运行时授权只使用一个权威角色映射，元数据不能单独授予权限；
- `public_summary()` 只返回必要的能力/权限摘要，不暴露配置秘密。

如确需服务端全局关闭管理面板，可新增 `AGENT_SERVER_ALLOWED_PERMISSIONS`，默认允许 `data_management.read`；否则保持最小实现，直接由 `admin` 角色权限控制，并在文档中明确该权限为代码级安全边界。

### 3.3 API 入口权限

新增 `/api/me`，返回当前服务端注入主体的非敏感授权摘要：

```json
{
  "principal_id": "...",
  "roles": ["user"],
  "capabilities": ["system_help", "cost_calculation", "report_generation"],
  "permissions": []
}
```

前端使用它控制导航展示；后端每个数据管理接口仍独立调用 `require_permission()`。隐藏导航不能替代后端授权。

## 4. 后端实施步骤

### 阶段 1：角色与权限切换

涉及文件方向：

- `backend/app/domain/authorization.py`
- `backend/app/agent/capabilities.py`
- `backend/app/settings.py`
- `backend/.env.example`
- 相关授权与 Harness 测试

工作内容：

1. 将角色类型切换为 `user/admin`。
2. 将 `user` 映射到当前普通能力，将 `admin` 映射到普通能力加 `data_management.read`。
3. 增加业务权限解析与 `ExecutionContext.require_permission()`。
4. 移除旧角色定义、旧配置说明和兼容分支。
5. 明确并测试 `CapabilitySpec.required_roles` 不得成为第二套运行时授权来源。
6. 默认主体角色改为 `user`，更新 `.env.example` 与运行手册。

验收：

- `user` 访问数据管理 API 返回稳定的 403；
- `admin` 能访问数据管理 API；
- 两个角色的普通成本/Agent 能力行为保持预期；
- 客户端请求不能改变主体、租户、角色、权限或数据范围。

### 阶段 2：数据管理 Repository 与服务

新增只读管理边界，建议文件：

- `backend/app/repositories/data_admin_repository.py`
- `backend/app/repositories/postgres_data_admin_repository.py`
- `backend/app/services/data_admin_service.py`
- `backend/app/schemas/data_admin.py`

Repository 约束：

- 所有查询强制使用当前主体的 `tenant_id`；
- 批次列表只读取 `data_load_batches` 元数据；
- 错误明细只读取 `data_load_errors`，默认不返回 `raw_payload`；
- 当前发布统计必须绑定 `status='published'` 的批次；
- 不读取旧租户数据，不允许客户端传入 tenant_id；
- 不修改任何数据，不触发导入，不执行成本重算。

建议服务能力：

1. `list_batches()`：状态、来源系统、来源文件、快照哈希、规则版本、总行数、有效行数、错误行数、时间和分页。
2. `get_batch()`：单批次元数据与错误码聚合。
3. `list_batch_errors()`：按错误 ID 分页返回来源表、来源记录、错误码、错误消息和创建时间。
4. `get_published_snapshot()`：当前发布批次、规则版本、四类事实行数、投影行数、期间范围、零件数量。
5. `get_quality_summary()`：发布快照覆盖范围和投影一致性摘要。

质量统计必须由服务端聚合；前端不得重新计算事实口径或金额。

### 阶段 3：数据管理 API

新增独立 Router，建议前缀 `/api/data-admin`：

```text
GET /api/data-admin/batches
GET /api/data-admin/batches/{batch_id}
GET /api/data-admin/batches/{batch_id}/errors
GET /api/data-admin/published
GET /api/data-admin/quality
```

接口要求：

- 所有接口都依赖当前 `ExecutionPrincipal`；
- 所有接口都执行 `data_management.read` 权限检查；
- 非 admin 使用稳定的 `data_management_access_denied` 403；
- 不存在或不属于当前租户的批次统一表现为未找到；
- 分页参数有明确上限，错误消息不含 SQL、路径、密钥或完整原始载荷；
- 遵循现有 request ID、错误外壳、Pydantic 响应和数据库不可用 503 约定；
- 不将 Router 注册到 Agent 工具或成本查询工具。

`/api/me` 也需要加入主应用，但不把它作为数据管理安全边界。

### 阶段 4：后端测试

新增或更新测试覆盖：

- `user/admin` 角色能力和业务权限矩阵；
- 未知角色配置失败；
- 非 admin 访问每个 `/api/data-admin` 接口均为 403；
- admin 的 tenant 隔离；
- 批次分页、状态过滤、空列表；
- 批次不存在、错误明细分页和错误码聚合；
- 当前发布批次统计只读当前租户的 `published` 快照；
- 数据库不可用错误映射；
- `/api/me` 返回权限摘要且不包含秘密；
- `test_baseline_and_boundaries.py` 的 canonical API 路径集合加入新路由；
- 单一 baseline 的表集合断言保持不变。

## 5. 前端实施步骤

### 阶段 5：授权摘要与导航

新增 API client：

- `frontend/src/api/me.ts`
- 对应类型与错误处理测试。

修改：

- `frontend/src/components/AppShell.tsx`
- `frontend/src/App.tsx`
- `frontend/src/components/breadcrumbItems.ts`

行为：

- `admin` 显示“数据治理”导航，链接 `/data-admin?period=...`；
- `user` 不显示导航入口；
- `/api/me` 加载失败时不默认放开管理入口；
- 直接访问 `/data-admin` 时由页面显示 unauthorized，而不是依赖前端跳转隐藏；
- 保留当前期间参数和全局面包屑行为。

### 阶段 6：数据治理页面

新增：

- `frontend/src/pages/DataAdminPage.tsx`
- `frontend/src/pages/DataAdminPage.test.tsx`
- `frontend/src/api/dataAdmin.ts`
- 必要的详情/错误明细组件。

页面结构遵循现有高密度工作区：

1. 页面标题“数据治理”。
2. 当前发布快照状态条：批次 ID、规则版本、发布时间、行数和覆盖期间。
3. 质量摘要：事实表行数、投影数量、错误数、覆盖范围和一致性状态。
4. 导入批次表：状态、来源系统、哈希、行数、错误数、创建/完成时间。
5. 批次详情抽屉或详情页：错误码摘要和错误明细分页。

必须实现以下状态且不改变固定工具栏布局：

- loading；
- empty；
- error；
- unavailable；
- unauthorized。

禁止：

- 显示上传、发布、回滚、删除或编辑按钮；
- 显示未经授权的 `raw_payload`；
- 前端自行推导成本金额或发布状态；
- 用占位批次、虚假质量分数或演示数据替代服务端响应。

视觉实现遵守根 `DESIGN.md`：密集表格、1px 结构边框、零圆角、可横向滚动、键盘焦点、明暗主题和窄屏单列阅读顺序。

### 阶段 7：前端测试与浏览器验收

测试覆盖：

- admin 导航显示、user 导航隐藏；
- `/data-admin` 直接访问的未授权状态；
- 快照摘要、批次列表、错误详情渲染；
- 分页和 URL 状态；
- loading/empty/error/unavailable 状态；
- 明暗主题和窄屏表格横向滚动；
- API 403 不被误显示为普通空数据。

## 6. 文档同步

新增原子契约文档：

- `docs/api/data-admin-contract.md`

并更新：

- `docs/README.md`：加入数据治理 API 契约入口；
- `docs/architecture/harness.md`：记录 `user/admin`、业务权限和统一授权门；
- `docs/architecture/data-flow.md`：增加只读数据治理查询流；
- `docs/data/governance.md`：记录面板只读边界、错误查询边界和不可回滚事实；
- `docs/data/data-dictionary.md`：补充数据管理只读视图使用的已有表；
- `docs/operations/runbook.md`：增加 `AGENT_PRINCIPAL_ROLES=user/admin` 与面板验证命令；
- `docs/governance/documentation.md`：增加新 API、权限配置和文档映射；
- 根 `DESIGN.md`：仅在新增页面改变产品级导航或设计约束时同步，普通组件实现不修改设计契约。

同步门禁：

- 在 `scripts/check_docs_sync.ps1` 的 required docs、`docs/README.md` 链接和变更映射中登记新契约；
- 新增后端 API 文件后补充对应的路径映射；
- 保持 `backend/alembic/versions` 只有 `20260907_0001_costgraph_baseline.py`；
- 不增加权限表、导入任务表或其他 schema 变更。

## 7. 验证与完成标准

按阶段执行以下检查：

```powershell
$env:PYTHONPATH="D:\work\costgraph\backend"
backend\.venv\Scripts\python.exe -m pytest backend\tests -q

.\scripts\check_python_quality.ps1

Push-Location frontend
npm test
npm run build
npm run test:bundle
Pop-Location

.\scripts\check_docs_sync.ps1
git diff --check
```

完成标准：

- `user` 和 `admin` 角色配置可启动，旧角色不再被接受；
- admin 可通过 API 和页面读取当前租户的数据治理信息；
- user 无法通过 API 或直接 URL 访问数据治理内容；
- 数据治理接口不修改数据库，不启动导入，不改变当前 published 快照；
- 成本总览、批次成本、Agent、报表和 Artifact 现有行为无回归；
- 所有管理查询带租户边界，错误明细不泄露原始载荷；
- 单一 Alembic baseline、PostgreSQL 运行边界和现有文档门禁保持通过；
- 全量后端/前端测试、生产构建、文档同步和 `git diff --check` 通过。

## 8. 后续候选阶段（不属于本轮）

如果未来需要写操作，另立计划，不在本计划中提前占位：

1. 选择认证来源并替换单一配置主体；
2. 设计独立导入任务与 Worker；
3. 增加操作者、审计、取消、重试和幂等展示；
4. 将事实改造成真正不可变快照；
5. 在此基础上设计可验证的快照回滚；
6. 重新评估单一 baseline 是否适合生产增量迁移。
