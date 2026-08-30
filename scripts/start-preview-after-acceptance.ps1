<#!
.SYNOPSIS
Starts only the two React preview services after the automated acceptance gate.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "Import-PreviewEnvironment.ps1")
Import-PreviewEnvironment -RepoRoot $repoRoot
$required = @("PREVIEW_USER_EMAIL", "PREVIEW_USER_PASSWORD", "PREVIEW_ADMIN_EMAIL", "PREVIEW_ADMIN_PASSWORD")
$missing = @($required | Where-Object { [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($_, "Process")) })
foreach ($name in $required) {
    Write-Output ("environment {0}: {1}" -f $name, $(if ($missing -contains $name) { "missing" } else { "present" }))
}
if ($missing.Count -gt 0) {
    Write-Error ("Preview is environment-blocked; missing variables: " + ($missing -join ", "))
    exit 2
}

& (Join-Path $PSScriptRoot "check-preview-ports.ps1")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Push-Location $repoRoot
try {
    # --no-deps prevents PostgreSQL/backend startup; an existing backend is required.
    docker compose -f docker-compose.yml -f docker-compose.preview.yml --profile preview up --detach --no-build --no-deps frontend-preview admin-preview
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
    Pop-Location
}

Write-Output "React previews started. Run scripts\\verify-preview.ps1, then complete the required manual review."
