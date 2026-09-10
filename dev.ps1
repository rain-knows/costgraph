[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("start", "stop", "restart", "status", "logs")]
    [string]$Action = "start",

    [switch]$Open,

    [ValidateRange(1, 500)]
    [int]$Tail = 40
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RootDir = $PSScriptRoot
$BackendDir = Join-Path $RootDir "backend"
$FrontendDir = Join-Path $RootDir "frontend"
$VenvDir = Join-Path $BackendDir ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$RequirementsFile = Join-Path $BackendDir "requirements.txt"
$PackageLockFile = Join-Path $FrontendDir "package-lock.json"
$RuntimeDir = Join-Path $RootDir ".dev"
$LogDir = Join-Path $RuntimeDir "logs"
$StateFile = Join-Path $RuntimeDir "processes.json"
$BackendMarker = Join-Path $RuntimeDir "backend-requirements.sha256"
$FrontendMarker = Join-Path $RuntimeDir "frontend-package-lock.sha256"
$BackendUrl = "http://127.0.0.1:8000/api/health"
$ReadyUrl = "http://127.0.0.1:8000/api/readyz"
$FrontendUrl = "http://127.0.0.1:5173/"

function Write-Step {
    param([string]$Message)
    Write-Host "[dev] $Message" -ForegroundColor Cyan
}

function Get-PortProcessId {
    param([int]$Port)

    $connection = $null
    try {
        $connection = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -First 1
    }
    catch {
        $connection = $null
    }

    if ($null -ne $connection) {
        return [int]$connection.OwningProcess
    }

    $pattern = "^\s*TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+(\d+)\s*$"
    foreach ($line in & netstat.exe -ano -p tcp) {
        if ($line -match $pattern) {
            return [int]$Matches[1]
        }
    }

    return $null
}

function Test-Url {
    param([string]$Url)

    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 2
        return $response.StatusCode -eq 200
    }
    catch {
        return $false
    }
}

function Wait-Url {
    param(
        [string]$Url,
        [int]$TimeoutSeconds = 30,
        [System.Diagnostics.Process]$Process = $null,
        [string]$ProcessLabel = "Process",
        [string]$ErrorLogPath = ""
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        if ($null -ne $Process) {
            try {
                $Process.Refresh()
                if ($Process.HasExited) {
                    $errorTail = if ($ErrorLogPath -and (Test-Path -LiteralPath $ErrorLogPath)) {
                        (Get-Content -LiteralPath $ErrorLogPath -Tail 20 -ErrorAction SilentlyContinue) -join [Environment]::NewLine
                    }
                    else {
                        ""
                    }
                    throw ($ProcessLabel + " exited before " + $Url + " became ready." + [Environment]::NewLine + $errorTail)
                }
            }
            catch [System.InvalidOperationException] {
                throw "$ProcessLabel exited before $Url became ready."
            }
        }

        if (Test-Url -Url $Url) {
            return $true
        }
        Start-Sleep -Milliseconds 350
    } while ((Get-Date) -lt $deadline)

    return $false
}

function Test-Python {
    param([string]$PythonPath)

    if (-not (Test-Path -LiteralPath $PythonPath)) {
        return $false
    }

    try {
        & $PythonPath -c "import sys" 2>&1 | Out-Null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

function Resolve-BootstrapPython {
    $candidates = @()
    $codexPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
    if (Test-Path -LiteralPath $codexPython) {
        $candidates += [pscustomobject]@{
            File = $codexPython
            Args = @()
            Label = "Codex bundled Python 3.12"
        }
    }

    $pyCommand = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($null -ne $pyCommand) {
        $candidates += [pscustomobject]@{
            File = $pyCommand.Source
            Args = @("-3.12")
            Label = "Python 3.12"
        }
    }

    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($null -ne $pythonCommand) {
        $candidates += [pscustomobject]@{
            File = $pythonCommand.Source
            Args = @()
            Label = "Python 3.12"
        }
    }

    foreach ($candidate in $candidates) {
        try {
            $prefixArgs = @($candidate.Args)
            $version = (& $candidate.File @prefixArgs -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')").Trim()
            if ($LASTEXITCODE -eq 0 -and $version -eq "3.12") {
                return $candidate
            }
        }
        catch {
            continue
        }
    }

    return $null
}

function Get-FileHashValue {
    param([string]$Path)
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash
}

function Read-Marker {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return ""
    }
    return (Get-Content -Raw -LiteralPath $Path).Trim()
}

function Ensure-BackendEnvironment {
    $created = $false
    if (-not (Test-Python -PythonPath $VenvPython)) {
        $bootstrap = Resolve-BootstrapPython
        if ($null -eq $bootstrap) {
            throw "No usable Python 3.12 runtime was found. Install Python 3.12 and run .\dev.ps1 again."
        }

        Write-Step "Rebuilding backend virtual environment with $($bootstrap.Label)..."
        $prefixArgs = @($bootstrap.Args)
        & $bootstrap.File @prefixArgs -m venv --clear $VenvDir
        if ($LASTEXITCODE -ne 0 -or -not (Test-Python -PythonPath $VenvPython)) {
            throw "Failed to create backend virtual environment."
        }
        $created = $true
    }

    $requirementsHash = Get-FileHashValue -Path $RequirementsFile
    $installedHash = Read-Marker -Path $BackendMarker
    $importsReady = $false
    if (-not $created) {
        $previousErrorActionPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            & $VenvPython -W ignore -c "import fastapi, uvicorn, langgraph, pydantic, pydantic_settings; import langgraph.checkpoint.postgres" *> $null
            $importsReady = $LASTEXITCODE -eq 0
        }
        finally {
            $ErrorActionPreference = $previousErrorActionPreference
        }
    }

    if ($created -or -not $importsReady -or $installedHash -ne $requirementsHash) {
        Write-Step "Installing backend dependencies..."
        & $VenvPython -m pip install -r $RequirementsFile
        if ($LASTEXITCODE -ne 0) {
            throw "Backend dependency installation failed."
        }
        Set-Content -LiteralPath $BackendMarker -Value $requirementsHash -Encoding ASCII
    }
}

function Ensure-FrontendEnvironment {
    $npmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if ($null -eq $npmCommand) {
        throw "npm was not found. Install Node.js 20+ and run .\dev.ps1 again."
    }

    $packageHash = Get-FileHashValue -Path $PackageLockFile
    $installedHash = Read-Marker -Path $FrontendMarker
    $nodeModules = Join-Path $FrontendDir "node_modules"
    $dependenciesReady = $false

    if (Test-Path -LiteralPath $nodeModules) {
        Push-Location $FrontendDir
        try {
            & $npmCommand.Source ls --depth=0 --silent 2>&1 | Out-Null
            $dependenciesReady = $LASTEXITCODE -eq 0
        }
        finally {
            Pop-Location
        }
    }

    if (-not $dependenciesReady -or $installedHash -ne $packageHash) {
        Write-Step "Installing frontend dependencies..."
        Push-Location $FrontendDir
        try {
            & $npmCommand.Source install | Out-Host
            if ($LASTEXITCODE -ne 0) {
                throw "Frontend dependency installation failed."
            }
        }
        finally {
            Pop-Location
        }
        Set-Content -LiteralPath $FrontendMarker -Value $packageHash -Encoding ASCII
    }

    return $npmCommand.Source
}

function Read-State {
    if (-not (Test-Path -LiteralPath $StateFile)) {
        return $null
    }
    try {
        return Get-Content -Raw -LiteralPath $StateFile | ConvertFrom-Json
    }
    catch {
        return $null
    }
}

function Get-SavedProcessId {
    param(
        [object]$State,
        [string]$Name
    )

    if ($null -eq $State -or $State.PSObject.Properties.Name -notcontains $Name) {
        return $null
    }
    $value = $State.$Name
    if ($null -eq $value) {
        return $null
    }
    return [int]$value
}

function Show-Status {
    $state = Read-State
    $backendPid = Get-PortProcessId -Port 8000
    $frontendPid = Get-PortProcessId -Port 5173
    $workerPid = Get-SavedProcessId -State $state -Name "worker_pid"
    $workerProcess = if ($null -ne $workerPid) { Get-Process -Id $workerPid -ErrorAction SilentlyContinue } else { $null }
    $backendOk = Test-Url -Url $BackendUrl
    $frontendOk = Test-Url -Url $FrontendUrl

    $backendText = if ($backendOk) { "UP (PID $backendPid)" } elseif ($null -ne $backendPid) { "PORT OCCUPIED (PID $backendPid)" } else { "DOWN" }
    $frontendText = if ($frontendOk) { "UP (PID $frontendPid)" } elseif ($null -ne $frontendPid) { "PORT OCCUPIED (PID $frontendPid)" } else { "DOWN" }
    $workerText = if ($null -ne $workerProcess) { "UP (PID $workerPid)" } else { "DOWN" }

    Write-Host "Backend : $backendText"
    Write-Host "Frontend: $frontendText"
    Write-Host "Worker  : $workerText"
    Write-Host "Runtime : $(if (Test-Url -Url $ReadyUrl) { 'READY' } else { 'NOT READY - check /api/readyz and .\dev.ps1 logs' })"
}

function Watch-Logs {
    param([int]$TailLines = 40)

    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
    $logFiles = @(
        [pscustomobject]@{ Name = "backend.out"; Path = (Join-Path $LogDir "backend.out.log") },
        [pscustomobject]@{ Name = "backend.err"; Path = (Join-Path $LogDir "backend.err.log") },
        [pscustomobject]@{ Name = "worker.out"; Path = (Join-Path $LogDir "worker.out.log") },
        [pscustomobject]@{ Name = "worker.err"; Path = (Join-Path $LogDir "worker.err.log") },
        [pscustomobject]@{ Name = "frontend.out"; Path = (Join-Path $LogDir "frontend.out.log") },
        [pscustomobject]@{ Name = "frontend.err"; Path = (Join-Path $LogDir "frontend.err.log") }
    )

    foreach ($logFile in $logFiles) {
        if (-not (Test-Path -LiteralPath $logFile.Path)) {
            Set-Content -LiteralPath $logFile.Path -Value "" -Encoding UTF8
        }
        Write-Host "--- $($logFile.Name) [$($logFile.Path)] ---" -ForegroundColor DarkCyan
        Get-Content -LiteralPath $logFile.Path -Tail $TailLines -ErrorAction SilentlyContinue |
            ForEach-Object { Write-Host "[$($logFile.Name)] $_" }
    }

    $jobs = @()
    try {
        foreach ($logFile in $logFiles) {
            $jobs += Start-Job -Name "dev-log-$($logFile.Name)" -ArgumentList $logFile.Path, $logFile.Name -ScriptBlock {
                param([string]$Path, [string]$Name)
                Get-Content -LiteralPath $Path -Wait | ForEach-Object {
                    "[$Name] $_"
                }
            }
        }

        Write-Host "Watching logs; press Ctrl+C to exit." -ForegroundColor Green
        while ($true) {
            foreach ($job in $jobs) {
                Receive-Job -Job $job -ErrorAction SilentlyContinue | Write-Host
            }
            Start-Sleep -Milliseconds 200
        }
    }
    finally {
        $jobs | Stop-Job -ErrorAction SilentlyContinue
        $jobs | Remove-Job -Force -ErrorAction SilentlyContinue
    }
}

function Stop-Services {
    param([switch]$IncludePortOwners)

    $state = Read-State
    if ($null -eq $state -and -not $IncludePortOwners) {
        Write-Step "No processes started by dev.ps1 were found."
        Show-Status
        return
    }

    $services = @(
        [pscustomobject]@{ Name = "backend_pid"; Port = 8000 },
        [pscustomobject]@{ Name = "frontend_pid"; Port = 5173 }
    )

    $workerPid = Get-SavedProcessId -State $state -Name "worker_pid"
    if ($null -ne $workerPid) {
        $workerProcess = Get-Process -Id $workerPid -ErrorAction SilentlyContinue
        if ($null -ne $workerProcess) {
            Write-Step "Stopping worker_pid (PID $workerPid)..."
            Stop-Process -Id $workerPid -ErrorAction SilentlyContinue
        }
    }

    foreach ($service in $services) {
        $savedProcessId = Get-SavedProcessId -State $state -Name $service.Name
        $listeningProcessId = Get-PortProcessId -Port $service.Port
        $processId = $savedProcessId
        if ($IncludePortOwners -and $null -ne $listeningProcessId) {
            $processId = $listeningProcessId
        }
        elseif ($null -eq $processId) {
            continue
        }
        elseif ($listeningProcessId -ne $processId) {
            Write-Step "Skipping stale $($service.Name) entry (PID $processId)."
            continue
        }

        $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if ($null -ne $process) {
            Write-Step "Stopping $($service.Name) (PID $processId)..."
            Stop-Process -Id $processId -ErrorAction SilentlyContinue
        }
    }

    Remove-Item -LiteralPath $StateFile -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 500
    Show-Status
}

function Start-Services {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null

    $backendPid = Get-PortProcessId -Port 8000
    $frontendPid = Get-PortProcessId -Port 5173
    $backendOk = Test-Url -Url $BackendUrl
    $frontendOk = Test-Url -Url $FrontendUrl

    if ($null -ne $backendPid -and -not $backendOk) {
        throw "Port 8000 is occupied by PID $backendPid, but the backend health check failed."
    }
    if ($null -ne $frontendPid -and -not $frontendOk) {
        throw "Port 5173 is occupied by PID $frontendPid, but the frontend check failed."
    }

    $existingState = Read-State
    $savedBackendPid = Get-SavedProcessId -State $existingState -Name "backend_pid"
    $savedFrontendPid = Get-SavedProcessId -State $existingState -Name "frontend_pid"
    $savedWorkerPid = Get-SavedProcessId -State $existingState -Name "worker_pid"
    $startedBackendPid = $null
    $startedFrontendPid = $null
    $startedWorkerPid = $null

    try {
        if (-not $backendOk) {
            Ensure-BackendEnvironment
            $backendOut = Join-Path $LogDir "backend.out.log"
            $backendErr = Join-Path $LogDir "backend.err.log"
            Set-Content -LiteralPath $backendOut -Value "" -Encoding UTF8
            Set-Content -LiteralPath $backendErr -Value "" -Encoding UTF8

            Write-Step "Starting backend..."
            $backendProcess = Start-Process -FilePath $VenvPython -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000") -WorkingDirectory $BackendDir -WindowStyle Hidden -RedirectStandardOutput $backendOut -RedirectStandardError $backendErr -PassThru
            $startedBackendPid = $backendProcess.Id

            if (-not (Wait-Url -Url $BackendUrl -TimeoutSeconds 30 -Process $backendProcess -ProcessLabel "Backend" -ErrorLogPath $backendErr)) {
                $errorTail = Get-Content -LiteralPath $backendErr -Tail 20 -ErrorAction SilentlyContinue
                throw ("Backend failed to start. Check " + $backendErr + "." + [Environment]::NewLine + $errorTail)
            }
            $startedBackendPid = Get-PortProcessId -Port 8000
        }
        else {
            Write-Step "Backend is already running."
        }

        $workerOut = Join-Path $LogDir "worker.out.log"
        $workerErr = Join-Path $LogDir "worker.err.log"
        $workerProcess = $null
        $savedWorkerProcess = if ($null -ne $savedWorkerPid) { Get-Process -Id $savedWorkerPid -ErrorAction SilentlyContinue } else { $null }
        if ($null -eq $savedWorkerProcess) {
            Set-Content -LiteralPath $workerOut -Value "" -Encoding UTF8
            Set-Content -LiteralPath $workerErr -Value "" -Encoding UTF8
            Write-Step "Starting PostgreSQL Agent worker..."
            $workerProcess = Start-Process -FilePath $VenvPython -ArgumentList @("-m", "app.worker") -WorkingDirectory $BackendDir -WindowStyle Hidden -RedirectStandardOutput $workerOut -RedirectStandardError $workerErr -PassThru
            $startedWorkerPid = $workerProcess.Id
        }
        else {
            $workerProcess = $savedWorkerProcess
            Write-Step "Worker is already running."
        }

        if (-not (Wait-Url -Url $ReadyUrl -TimeoutSeconds 30 -Process $workerProcess -ProcessLabel "Worker" -ErrorLogPath $workerErr)) {
            $workerErrorTail = Get-Content -LiteralPath (Join-Path $LogDir "worker.err.log") -Tail 20 -ErrorAction SilentlyContinue
            throw ("Runtime did not become ready. Check /api/readyz and .\dev.ps1 logs for database schema, Alembic and Worker errors; see docs/operations/runbook.md." + [Environment]::NewLine + $workerErrorTail)
        }

        if (-not $frontendOk) {
            $npmPath = Ensure-FrontendEnvironment
            $frontendOut = Join-Path $LogDir "frontend.out.log"
            $frontendErr = Join-Path $LogDir "frontend.err.log"
            Set-Content -LiteralPath $frontendOut -Value "" -Encoding UTF8
            Set-Content -LiteralPath $frontendErr -Value "" -Encoding UTF8

            Write-Step "Starting frontend..."
            $frontendProcess = Start-Process -FilePath $npmPath -ArgumentList @("run", "dev") -WorkingDirectory $FrontendDir -WindowStyle Hidden -RedirectStandardOutput $frontendOut -RedirectStandardError $frontendErr -PassThru
            $startedFrontendPid = $frontendProcess.Id

            if (-not (Wait-Url -Url $FrontendUrl -TimeoutSeconds 30 -Process $frontendProcess -ProcessLabel "Frontend" -ErrorLogPath $frontendErr)) {
                $errorTail = Get-Content -LiteralPath $frontendErr -Tail 20 -ErrorAction SilentlyContinue
                throw ("Frontend failed to start. Check " + $frontendErr + "." + [Environment]::NewLine + $errorTail)
            }
            $startedFrontendPid = Get-PortProcessId -Port 5173
        }
        else {
            Write-Step "Frontend is already running."
        }
    }
    catch {
        foreach ($processId in @($startedBackendPid, $startedWorkerPid, $startedFrontendPid)) {
            if ($null -ne $processId) {
                Stop-Process -Id $processId -ErrorAction SilentlyContinue
            }
        }
        throw
    }

    $backendPidToSave = if ($null -ne $startedBackendPid) { $startedBackendPid } else { $savedBackendPid }
    $frontendPidToSave = if ($null -ne $startedFrontendPid) { $startedFrontendPid } else { $savedFrontendPid }
    $workerPidToSave = if ($null -ne $startedWorkerPid) { $startedWorkerPid } else { $savedWorkerPid }
    [ordered]@{
        backend_pid = $backendPidToSave
        frontend_pid = $frontendPidToSave
        worker_pid = $workerPidToSave
        started_at = (Get-Date).ToString("o")
    } | ConvertTo-Json | Set-Content -LiteralPath $StateFile -Encoding UTF8

    Write-Host ""
    Write-Host "Services are ready:" -ForegroundColor Green
    Write-Host "  App    $FrontendUrl"
    Write-Host "  API    http://127.0.0.1:8000/docs"
    Write-Host "  Logs   $LogDir"
    Write-Host ""
    Write-Host "Stop with: .\dev.ps1 stop"

    if ($Open) {
        Start-Process $FrontendUrl
    }
}

New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null

switch ($Action) {
    "start" { Start-Services }
    "stop" { Stop-Services }
    "restart" {
        Stop-Services -IncludePortOwners
        Start-Services
    }
    "status" { Show-Status }
    "logs" { Watch-Logs -TailLines $Tail }
}
