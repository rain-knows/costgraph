[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 5174
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$frontendRoot = Join-Path $repoRoot "frontend"
$nodeScript = Join-Path $frontendRoot "scripts\verify-reports-browser.mjs"

if (-not (Test-Path -LiteralPath $nodeScript)) {
    throw "Browser verifier is missing: $nodeScript"
}
if (-not (Test-Path -LiteralPath (Join-Path $frontendRoot "node_modules\ws"))) {
    throw "Frontend dependencies are missing. Run npm install in frontend first."
}

$env:REPORTS_BROWSER_BASE_URL = "http://127.0.0.1:$Port"
Push-Location $frontendRoot
try {
    & node $nodeScript
    if ($LASTEXITCODE -ne 0) {
        throw "Browser verification failed with exit code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}
