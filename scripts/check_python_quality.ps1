$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $repoRoot "backend"
$venvDir = Join-Path $backendDir ".venv"
$ruff = Join-Path $venvDir "Scripts\ruff.exe"

if (-not (Test-Path -LiteralPath $venvDir -PathType Container)) {
    throw "缺少后端虚拟环境 backend/.venv；请先运行 .\dev.ps1 安装依赖。"
}

if (-not (Test-Path -LiteralPath $ruff -PathType Leaf)) {
    throw "后端虚拟环境中缺少 Ruff；请先运行 .\dev.ps1 安装依赖。"
}

Push-Location $backendDir
try {
    & $ruff check .
    if ($LASTEXITCODE -ne 0) {
        throw "Ruff lint 检查失败。"
    }

    & $ruff format --check .
    if ($LASTEXITCODE -ne 0) {
        throw "Ruff format 检查失败。"
    }
}
finally {
    Pop-Location
}
