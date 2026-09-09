[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8001,

    [ValidateRange(10, 180)]
    [int]$TimeoutSeconds = 90
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $RootDir "backend"
$PythonPath = Join-Path $BackendDir ".venv\Scripts\python.exe"
$LogDir = Join-Path $RootDir ".dev\logs"
$BaseUrl = "http://127.0.0.1:$Port"
$SmokeVersion = "durable-smoke-$PID-$Port"
$apiProcess = $null
$workerProcess = $null
$conversationId = $null
$runId = $null

function Wait-Endpoint {
    param(
        [string]$Uri,
        [int]$Seconds
    )

    $deadline = (Get-Date).AddSeconds($Seconds)
    do {
        try {
            return Invoke-RestMethod -Uri $Uri -TimeoutSec 2
        }
        catch {
            Start-Sleep -Milliseconds 500
        }
    } while ((Get-Date) -lt $deadline)
    return $null
}

function Stop-ExactProcess {
    param([object]$Process)

    if ($null -ne $Process -and -not $Process.HasExited) {
        Stop-Process -Id $Process.Id -ErrorAction SilentlyContinue
    }
}

if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw "Backend virtual environment is missing. Run .\dev.ps1 first."
}
if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
    throw "Port $Port is already in use."
}

New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
$env:APP_VERSION = $SmokeVersion
$env:DEEPSEEK_API_KEY = ""

try {
    $apiProcess = Start-Process `
        -FilePath $PythonPath `
        -ArgumentList @(
            "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
            "--port", [string]$Port
        ) `
        -WorkingDirectory $BackendDir `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogDir "durable-smoke-api.out.log") `
        -RedirectStandardError (Join-Path $LogDir "durable-smoke-api.err.log") `
        -PassThru
    $workerProcess = Start-Process `
        -FilePath $PythonPath `
        -ArgumentList @("-m", "app.worker") `
        -WorkingDirectory $BackendDir `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogDir "durable-smoke-worker.out.log") `
        -RedirectStandardError (Join-Path $LogDir "durable-smoke-worker.err.log") `
        -PassThru

    $live = Wait-Endpoint -Uri "$BaseUrl/api/livez" -Seconds 30
    if ($null -eq $live) {
        throw "Durable smoke API did not become live."
    }
    $ready = Wait-Endpoint -Uri "$BaseUrl/api/readyz" -Seconds 30
    if ($null -eq $ready) {
        throw "Durable smoke Worker did not become ready."
    }

    $conversation = Invoke-RestMethod `
        -Method Post `
        -Uri "$BaseUrl/api/conversations" `
        -ContentType "application/json" `
        -Body '{"routing_mode":"auto"}'
    $conversationId = $conversation.conversation_id
    $messageId = "smoke-$([guid]::NewGuid().ToString('N'))"
    $requestBody = @{
        message_id = $messageId
        content = "系统支持哪些能力？"
    } | ConvertTo-Json
    $created = Invoke-RestMethod `
        -Method Post `
        -Uri "$BaseUrl/api/v2/conversations/$conversationId/runs" `
        -ContentType "application/json" `
        -Body $requestBody
    $runId = $created.run_id

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $run = Invoke-RestMethod `
            -Uri "$BaseUrl/api/v2/conversations/$conversationId/runs/$runId"
        if ($run.status -notin @("succeeded", "failed", "cancelled")) {
            Start-Sleep -Milliseconds 500
        }
    } while (
        $run.status -notin @("succeeded", "failed", "cancelled") -and
        (Get-Date) -lt $deadline
    )
    if ($run.status -ne "succeeded") {
        throw "Durable smoke Run ended as $($run.status)."
    }

    $eventResponse = Invoke-WebRequest `
        -UseBasicParsing `
        -Uri "$BaseUrl/api/v2/conversations/$conversationId/runs/$runId/events"
    $eventCount = ([regex]::Matches(
        $eventResponse.Content,
        "(?m)^id: "
    )).Count
    $messages = Invoke-RestMethod `
        -Uri "$BaseUrl/api/conversations/$conversationId/messages"

    [ordered]@{
        api = $BaseUrl
        ready_status = $ready.status
        worker_check = $ready.checks.worker
        run_status = $run.status
        attempt_count = $run.attempt_count
        persisted_sse_events = $eventCount
        message_count = $messages.items.Count
    } | ConvertTo-Json

    Invoke-RestMethod `
        -Method Delete `
        -Uri "$BaseUrl/api/conversations/$conversationId" | Out-Null
    $conversationId = $null
}
finally {
    if ($null -ne $conversationId -and $null -ne $runId) {
        try {
            Invoke-RestMethod `
                -Method Post `
                -Uri "$BaseUrl/api/v2/conversations/$conversationId/runs/$runId/cancel" |
                Out-Null
        }
        catch {}
        try {
            Invoke-RestMethod `
                -Method Delete `
                -Uri "$BaseUrl/api/conversations/$conversationId" | Out-Null
        }
        catch {}
    }
    Stop-ExactProcess -Process $workerProcess
    Stop-ExactProcess -Process $apiProcess
    if ($null -ne $workerProcess) {
        $cleanupCode = @(
            "import sys",
            "from sqlalchemy import delete",
            "from app.db.engine import get_session_factory",
            "from app.db.models import RuntimeAgentWorker",
            "session = get_session_factory()()",
            "session.execute(delete(RuntimeAgentWorker).where(RuntimeAgentWorker.app_version == sys.argv[1]))",
            "session.commit()",
            "session.close()"
        ) -join ";"
        Push-Location $BackendDir
        try {
            & $PythonPath -c $cleanupCode $SmokeVersion | Out-Null
        }
        finally {
            Pop-Location
        }
    }
}
