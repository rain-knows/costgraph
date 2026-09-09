$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$docsRoot = Join-Path $repoRoot "docs"
$docsIndex = Join-Path $docsRoot "README.md"

$requiredDocs = @(
    "README.md",
    "architecture/system.md",
    "architecture/data-flow.md",
    "architecture/runtime.md",
    "architecture/harness.md",
    "architecture/tool-provider.md",
    "architecture/event-trace-replay.md",
    "data/storage.md",
    "data/data-dictionary.md",
    "data/governance.md",
    "api/agent-contract.md",
    "api/cost-data-contract.md",
    "operations/runbook.md",
    "governance/documentation.md",
    "plans/agent-demo-to-costgraph.md"
)

$requiredRootFiles = @("AGENTS.md", "README.md", "DESIGN.md")
$requiredDesignFiles = @(
    "DESIGN-ar.md",
    "DESIGN-de.md",
    "DESIGN-es.md",
    "DESIGN-fr.md",
    "DESIGN-id.md",
    "DESIGN-it.md",
    "DESIGN-ja.md",
    "DESIGN-ko.md",
    "DESIGN-nl.md",
    "DESIGN-pl.md",
    "DESIGN-pt-br.md",
    "DESIGN-ru.md",
    "DESIGN-tr.md",
    "DESIGN-uk.md",
    "DESIGN-vi.md",
    "DESIGN-zh-tw.md",
    "DESIGN.md",
    "DESIGN-zh.md",
    "USAGE.md",
    "tokens.css",
    "tailwind-v4.css",
    "design-tokens.json",
    "components.html",
    "components.manifest.json",
    "manifest.json",
    "source/evidence.md",
    "source/tokens.source.json",
    "source/token-contract.report.json",
    "preview/colors.html",
    "preview/spacing.html",
    "preview/typography.html",
    "system/index.html",
    "system/kit.html",
    "system/kit.dark.html",
    "system/tokens.default.json",
    "system/artifacts/deck.html",
    "system/artifacts/email.html",
    "system/artifacts/form.html",
    "system/artifacts/landing.html",
    "system/artifacts/newsletter.html",
    "system/artifacts/poster.html",
    "LICENSE",
    "UPSTREAM.md"
)

$legacyDocs = @(
    "01-system-overview.md",
    "02-storage-architecture.md",
    "03-data-dictionary.md",
    "04-agent-contract.md",
    "05-operations.md",
    "06-documentation-governance.md"
)

foreach ($file in $requiredRootFiles) {
    if (-not (Test-Path -LiteralPath (Join-Path $repoRoot $file) -PathType Leaf)) {
        throw "缺少根入口文件 $file。"
    }
}

foreach ($doc in $requiredDocs) {
    if (-not (Test-Path -LiteralPath (Join-Path $docsRoot $doc) -PathType Leaf)) {
        throw "缺少核心原子文档 docs/$doc。"
    }
}

foreach ($doc in $legacyDocs) {
    if (Test-Path -LiteralPath (Join-Path $docsRoot $doc)) {
        throw "发现已废弃文档 docs/$doc；事实文档必须使用原子目录。"
    }
}

$designRoot = Join-Path $repoRoot "design-systems/ibm"
foreach ($file in $requiredDesignFiles) {
    if (-not (Test-Path -LiteralPath (Join-Path $designRoot $file) -PathType Leaf)) {
        throw "IBM 设计包不完整：缺少 design-systems/ibm/$file。"
    }
}

$upstreamManifestPath = Join-Path $designRoot "UPSTREAM-SHA256.txt"
if (-not (Test-Path -LiteralPath $upstreamManifestPath -PathType Leaf)) {
    throw "IBM 设计包缺少逐文件 SHA-256 清单。"
}
$manifestEntries = @(Get-Content -LiteralPath $upstreamManifestPath | Where-Object {
    $_ -and $_ -notmatch '^\s*#'
})
if ($manifestEntries.Count -ne 41) {
    throw "IBM 上游 SHA-256 清单应包含 41 个文件，实际为 $($manifestEntries.Count)。"
}
foreach ($entry in $manifestEntries) {
    if ($entry -notmatch '^([0-9a-fA-F]{64})\s{2}(.+)$') {
        throw "IBM 上游 SHA-256 清单格式无效：$entry"
    }
    $expectedHash = $Matches[1].ToLowerInvariant()
    $relativePath = $Matches[2].Replace('/', '\\')
    $upstreamFile = Join-Path $designRoot $relativePath
    if (-not (Test-Path -LiteralPath $upstreamFile -PathType Leaf)) {
        throw "IBM 上游清单列出的文件不存在：$relativePath"
    }
    $actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $upstreamFile).Hash.ToLowerInvariant()
    if ($actualHash -ne $expectedHash) {
        throw "IBM 上游文件摘要不一致：$relativePath"
    }
}

$upstreamContent = Get-Content -Raw (Join-Path $designRoot "UPSTREAM.md")
foreach ($requiredValue in @(
    "https://github.com/nexu-io/open-design",
    "c5ae6292c464094de80d0191fab50144bff8a027",
    "Apache License 2.0"
)) {
    if ($upstreamContent -notmatch [regex]::Escape($requiredValue)) {
        throw "design-systems/ibm/UPSTREAM.md 缺少 provenance：$requiredValue。"
    }
}

$licenseContent = Get-Content -Raw (Join-Path $designRoot "LICENSE")
if ($licenseContent -notmatch "Apache License\s+Version 2\.0") {
    throw "design-systems/ibm/LICENSE 不是预期的 Apache License 2.0 文本。"
}

$indexContent = Get-Content -Raw $docsIndex
foreach ($doc in $requiredDocs | Where-Object { $_ -ne "README.md" }) {
    if ($indexContent -notmatch [regex]::Escape($doc)) {
        throw "docs/README.md 未链接核心文档 docs/$doc。"
    }
}

$changed = @(
    git -C $repoRoot diff --name-only
    git -C $repoRoot diff --cached --name-only
    git -C $repoRoot ls-files --others --exclude-standard
) | Where-Object { $_ -and $_.Trim() } |
    ForEach-Object { $_.Trim().Replace("\", "/") } |
    Sort-Object -Unique

function Test-ChangedPath {
    param([string[]]$Patterns)

    foreach ($path in $changed) {
        foreach ($pattern in $Patterns) {
            if ($path -match $pattern) { return $true }
        }
    }
    return $false
}

function Assert-ChangedDocs {
    param(
        [string[]]$SourcePatterns,
        [string[]]$RequiredPaths,
        [string]$Reason
    )

    if (-not (Test-ChangedPath $SourcePatterns)) { return }
    foreach ($path in $RequiredPaths) {
        if ($changed -notcontains $path) {
            throw "$Reason 已变更，但没有同步 $path。"
        }
    }
}

Assert-ChangedDocs @(
    '^backend/app/agent/(graph|nodes)\.py$',
    '^backend/app/agent/routes/',
    '^backend/app/domain/',
    '^backend/app/services/(cost_calculation|report|lineage)_service\.py$'
) @('docs/architecture/system.md', 'docs/architecture/data-flow.md') "系统或 Agent 流程"

Assert-ChangedDocs @(
    '^backend/app/agent/runtime\.py$',
    '^backend/app/agent/checkpoint\.py$',
    '^backend/app/services/runtime_service\.py$',
    '^backend/app/repositories/run_repository\.py$',
    '^backend/app/worker\.py$'
) @('docs/architecture/runtime.md') "Runtime 生命周期"

Assert-ChangedDocs @(
    '^backend/app/agent/(harness|context|capabilities)\.py$',
    '^backend/app/domain/authorization\.py$'
) @('docs/architecture/harness.md') "Harness 或策略门"

Assert-ChangedDocs @(
    '^backend/app/agent/(tools|providers|runtime_contracts|runtime_services)\.py$',
    '^backend/requirements\.txt$'
) @('docs/architecture/tool-provider.md') "Tool 或 Provider 契约"

Assert-ChangedDocs @(
    '^backend/app/agent/(trace|replay)\.py$',
    '^backend/scripts/(evaluate_agent|replay_conversation|runtime_report)\.py$'
) @('docs/architecture/event-trace-replay.md') "Event、Trace、Replay 或 Eval"

Assert-ChangedDocs @(
    '^backend/app/db/',
    '^backend/app/repositories/',
    '^backend/alembic/'
) @('docs/data/storage.md', 'docs/data/data-dictionary.md') "存储、ORM 或迁移"

Assert-ChangedDocs @(
    '^backend/app/services/cost_data_import_service\.py$',
    '^backend/scripts/(import_cost_data|inspect_cost_batch)\.py$',
    '^data/'
) @('docs/data/governance.md', 'docs/architecture/data-flow.md') "成本导入或发布"

Assert-ChangedDocs @(
    '^backend/app/api/(conversations|runtime_runs|artifacts)\.py$',
    '^backend/app/schemas/(agent|runtime|artifact)\.py$',
    '^frontend/src/api/(agent|conversations|artifacts)\.ts$'
) @('docs/api/agent-contract.md') "Agent API 契约"

Assert-ChangedDocs @(
    '^backend/app/api/cost_data\.py$',
    '^backend/app/schemas/cost_data\.py$',
    '^backend/app/services/cost_data_query_service\.py$',
    '^frontend/src/api/costData\.ts$'
) @('docs/api/cost-data-contract.md') "成本数据 API 契约"

Assert-ChangedDocs @(
    '^backend/app/settings\.py$',
    '^backend/\.env\.example$',
    '^dev\.ps1$',
    '^scripts/check_python_quality\.ps1$'
) @('docs/operations/runbook.md', 'docs/data/storage.md') "运行配置或启动流程"

Assert-ChangedDocs @(
    '^frontend/src/index\.css$',
    '^frontend/src/components/AppShell\.tsx$',
    '^frontend/tailwind\.config\.(js|ts)$'
) @('DESIGN.md') "全局界面或设计 token"

if (Test-ChangedPath @('^design-systems/ibm/')) {
    foreach ($path in @('DESIGN.md', 'design-systems/ibm/UPSTREAM.md')) {
        if ($changed -notcontains $path) {
            throw "IBM 设计包已变更，但没有同步 $path。"
        }
    }
}

if ($changed -contains 'AGENTS.md') {
    $governanceChanged = $changed -contains 'docs/README.md' -or
        $changed -contains 'docs/governance/documentation.md'
    if (-not $governanceChanged) {
        throw "AGENTS.md 已修改，但没有同步文档索引或文档治理规则。"
    }
}

$agentsContent = Get-Content -Raw (Join-Path $repoRoot "AGENTS.md")
foreach ($requiredRule in @("docs/README.md", "DESIGN.md", "check_docs_sync.ps1")) {
    if ($agentsContent -notmatch [regex]::Escape($requiredRule)) {
        throw "AGENTS.md 缺少强制规则或入口：$requiredRule。"
    }
}

$baselineDir = Join-Path $repoRoot "backend/alembic/versions"
if (Test-Path -LiteralPath $baselineDir) {
    $baselineFiles = @(Get-ChildItem -LiteralPath $baselineDir -File -Filter '*.py' |
        Where-Object { $_.Name -ne '__init__.py' })
    if ($baselineFiles.Count -ne 1) {
        throw "CostGraph 必须只有一条 Alembic baseline，当前发现 $($baselineFiles.Count) 条迁移。"
    }
}

$legacyPatterns = @(
    @{ Label = "废弃请求占位实体"; Regex = '\bturn_requests\b|\bRuntimeTurnRequest\b' },
    @{ Label = "废弃能力标识"; Regex = '\breadonly_qa\b|\benabled_features\b' },
    @{ Label = "废弃运行时转换"; Regex = '\bruntime_request_from_legacy\b|\bRuntimeV2\w*\b|\bruntime_v2\b' },
    @{ Label = "废弃存储切换"; Regex = '\bAGENT_STORAGE_BACKEND\b|\bCOST_DATA_BACKEND\b|\bAGENT_EXECUTION_BACKEND\b' },
    @{ Label = "废弃数据库变量"; Regex = '(?<!COST_)\bDATABASE_URL\b' },
    @{ Label = "废弃应用接口"; Regex = '/api/agent/runs|/outputs(?:\b|/)' },
    @{ Label = "废弃本地 Repository"; Regex = '\bSampleJsonCostRepository\b|\bEmbeddedArtifactRepository\b|\bSqliteConversationRepository\b' }
)

$legacySearchRoots = @(
    (Join-Path $repoRoot 'backend/app'),
    (Join-Path $repoRoot 'backend/alembic'),
    (Join-Path $repoRoot 'frontend/src'),
    (Join-Path $repoRoot 'docs/architecture'),
    (Join-Path $repoRoot 'docs/api'),
    (Join-Path $repoRoot 'docs/data'),
    (Join-Path $repoRoot 'docs/operations'),
    (Join-Path $repoRoot 'README.md'),
    (Join-Path $repoRoot 'AGENTS.md')
) | Where-Object { Test-Path -LiteralPath $_ }

$legacyFiles = foreach ($root in $legacySearchRoots) {
    if (Test-Path -LiteralPath $root -PathType Leaf) {
        Get-Item -LiteralPath $root
    }
    else {
        Get-ChildItem -LiteralPath $root -Recurse -File |
            Where-Object { $_.Extension -in @('.py', '.ts', '.tsx', '.js', '.md', '.ps1', '.toml', '.ini') }
    }
}

foreach ($pattern in $legacyPatterns) {
    $matches = @($legacyFiles | Select-String -Pattern $pattern.Regex)
    if ($matches.Count -gt 0) {
        $locations = $matches | Select-Object -First 8 | ForEach-Object {
            "$($_.Path):$($_.LineNumber)"
        }
        throw "$($pattern.Label) 仍存在：$($locations -join ', ')。"
    }
}

$markdownFiles = @(Get-ChildItem -LiteralPath $repoRoot -Recurse -File -Filter '*.md' |
    Where-Object {
        $_.FullName -notmatch '\\(\.git|node_modules|\.venv|__pycache__|\.pytest_cache|\.ruff_cache|dist|build)\\'
    })
$missingLinks = [System.Collections.Generic.List[string]]::new()

foreach ($file in $markdownFiles) {
    $content = Get-Content -Raw $file.FullName
    foreach ($match in [regex]::Matches($content, '\[[^\]]+\]\(([^)]+)\)')) {
        $target = $match.Groups[1].Value.Trim()
        if ($target -match '^(https?://|mailto:|#)') { continue }
        if ($target.StartsWith('<') -and $target.EndsWith('>')) {
            $target = $target.Substring(1, $target.Length - 2)
        }
        $target = ($target -split '#', 2)[0]
        $target = ($target -split '\?', 2)[0]
        if (-not $target) { continue }
        $targetPath = Join-Path $file.DirectoryName $target
        if (-not (Test-Path -LiteralPath $targetPath)) {
            $missingLinks.Add("$($file.FullName): $target")
        }
    }
}

if ($missingLinks.Count -gt 0) {
    throw ("存在失效 Markdown 相对链接:`n" + ($missingLinks -join "`n"))
}

Write-Output "文档同步检查通过：原子文档、设计 provenance、单一 baseline、同步映射、旧标识与 Markdown 链接均符合要求。"
