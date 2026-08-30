<#!
.SYNOPSIS
Runs the React migration acceptance gates in their required order.

All writes happen in uniquely named, tmpfs-backed Compose projects.  A missing
credential or approved-baseline flag is an environment block (exit 2), never a
skip or a substitute credential.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$e2eCompose = Join-Path $repoRoot "docker-compose.e2e.yml"
$testCompose = Join-Path $repoRoot "docker-compose.test.yml"
$runId = "zhishu-e2e-$([Guid]::NewGuid().ToString('N').Substring(0, 12))"
$testRunId = "$runId-backend"
$reportPath = Join-Path ([System.IO.Path]::GetTempPath()) "$runId-acceptance.jsonl"
$e2eStarted = $false

function Write-Report([string]$Stage, [string]$Status, [int]$ExitCode, [string]$Detail) {
    $record = [ordered]@{
        at = (Get-Date).ToUniversalTime().ToString("o")
        stage = $Stage
        status = $Status
        exitCode = $ExitCode
        detail = $Detail
    } | ConvertTo-Json -Compress
    Add-Content -LiteralPath $reportPath -Value $record -Encoding utf8
    Write-Output "[$Status] $Stage (exit $ExitCode): $Detail"
}

function Require-Variables([string]$Stage, [string[]]$Names) {
    $missing = @($Names | Where-Object { [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($_, "Process")) })
    foreach ($name in $Names) {
        $isPresent = -not [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name, "Process"))
        Write-Output ("environment {0}: {1}" -f $name, $(if ($isPresent) { "present" } else { "missing" }))
    }
    if ($missing.Count -gt 0) {
        Write-Report $Stage "environment-blocked" 2 ("missing variables: " + ($missing -join ", "))
        throw [System.InvalidOperationException]::new("environment blocked at $Stage")
    }
}

function Invoke-Gate([string]$Stage, [scriptblock]$Command) {
    & $Command
    $code = if ($null -eq $LASTEXITCODE) { 0 } else { [int]$LASTEXITCODE }
    if ($code -ne 0) {
        Write-Report $Stage "failed" $code "command failed"
        throw [System.InvalidOperationException]::new("$Stage failed")
    }
    Write-Report $Stage "passed" 0 "command completed"
}

function Stop-ComposeProject([string]$Project, [string]$ComposeFile) {
    & docker compose -p $Project -f $ComposeFile down
    $downCode = if ($null -eq $LASTEXITCODE) { 0 } else { [int]$LASTEXITCODE }
    $containers = @(& docker ps -aq --filter "label=com.docker.compose.project=$Project")
    $networks = @(& docker network ls -q --filter "label=com.docker.compose.project=$Project")
    if ($downCode -ne 0 -or $containers.Count -gt 0 -or $networks.Count -gt 0) {
        Write-Report "cleanup:$Project" "failed" 1 "isolated Compose resources remain or docker compose down failed"
        return
    }
    Write-Report "cleanup:$Project" "passed" 0 "containers and networks are absent"
}

try {
    # Compose configuration is the first gate.  Only presence is reported.
    Require-Variables "compose configuration" @(
        "E2E_USER_EMAIL", "E2E_USER_PASSWORD", "E2E_ADMIN_EMAIL", "E2E_ADMIN_PASSWORD",
        "E2E_SUPER_ADMIN_EMAIL", "E2E_SUPER_ADMIN_PASSWORD"
    )
    Invoke-Gate "compose configuration" { docker compose -p $runId -f $e2eCompose config --quiet }
    Invoke-Gate "test compose configuration" { docker compose -p $testRunId -f $testCompose config --quiet }

    try {
        Invoke-Gate "backend isolated tests" {
            docker compose -p $testRunId -f $testCompose up --build --abort-on-container-exit --exit-code-from backend-test backend-test
        }
    } finally {
        Stop-ComposeProject $testRunId $testCompose
    }

    Push-Location (Join-Path $repoRoot "web")
    try {
        Invoke-Gate "React unit tests" { npm test }
        Invoke-Gate "React type check" { npm run typecheck }
        Invoke-Gate "React production build" { npm run build }
    } finally {
        Pop-Location
    }

    Invoke-Gate "isolated E2E startup" { docker compose -p $runId -f $e2eCompose up --build --detach }
    $e2eStarted = $true
    $env:E2E_BASE_URL = "http://localhost:18511"
    $env:E2E_ADMIN_BASE_URL = "http://localhost:18512"
    $env:E2E_COMPOSE_PROJECT = $runId
    $env:E2E_COMPOSE_FILE = $e2eCompose
    $env:E2E_RESTART_SERVICES = "1"

    Push-Location (Join-Path $repoRoot "web")
    try {
        Invoke-Gate "non-AI browser E2E" { npm run test:e2e:non-ai }
        Invoke-Gate "administrator browser E2E" { npm run test:e2e:admin }

        if ([Environment]::GetEnvironmentVariable("E2E_REAL_AI", "Process") -ne "1") {
            Write-Report "real-AI browser E2E" "environment-blocked" 2 "E2E_REAL_AI must equal 1"
            throw [System.InvalidOperationException]::new("environment blocked at real-AI browser E2E")
        }
        Require-Variables "real-AI browser E2E" @("E2E_OPENAI_API_KEY", "E2E_MODEL_PROVIDER", "E2E_MODEL_NAMES", "E2E_EMBEDDINGS_MODEL_NAME")
        Invoke-Gate "real-AI browser E2E" { npm run test:e2e:real-ai }
        Invoke-Gate "mobile browser E2E" { npm run test:e2e:mobile }

    } finally {
        Pop-Location
    }
} catch {
    $isEnvironmentBlocked = Select-String -LiteralPath $reportPath -SimpleMatch '"environment-blocked"' -Quiet -ErrorAction SilentlyContinue
    if (-not $isEnvironmentBlocked) {
        Write-Report "acceptance" "failed" 1 $_.Exception.Message
    }
    Write-Output "Acceptance report (no secrets): $reportPath"
    if ($isEnvironmentBlocked) { exit 2 }
    exit 1
} finally {
    if ($e2eStarted) {
        Stop-ComposeProject $runId $e2eCompose
    }
}

Write-Output "Acceptance report (no secrets): $reportPath"
exit 0
