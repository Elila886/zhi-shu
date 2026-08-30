<#
.SYNOPSIS
Verifies the primary React takeover at 8501/8502 without invoking a model.

.DESCRIPTION
Uses the dedicated local PREVIEW_* account from the untracked root .env. It
checks user/admin token-surface isolation and the scoped refresh cookies. With
-RestartServices, it restarts only the primary backend, user frontend, and
admin frontend, then proves that the existing HttpOnly refresh cookies recover
both sessions. It never prints credentials or access tokens.
#>
[CmdletBinding()]
param(
    [string]$UserBaseUrl = "http://localhost:8501",
    [string]$AdminBaseUrl = "http://localhost:8502",
    [switch]$RestartServices
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "Import-PreviewEnvironment.ps1")
Import-PreviewEnvironment -RepoRoot $repoRoot

function Require-Variable([string]$Name) {
    $value = [Environment]::GetEnvironmentVariable($Name, "Process")
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "Formal takeover verification is environment-blocked: $Name is not set."
    }
    return $value
}

function Assert-Status($Response, [int]$Expected, [string]$Label) {
    if ([int]$Response.StatusCode -ne $Expected) {
        throw "$Label returned HTTP $($Response.StatusCode), expected $Expected."
    }
}

function Header-Text($Response, [string]$Name) {
    return (@($Response.Headers[$Name]) -join "`n")
}

function Assert-RefreshCookie($Response, [string]$Name, [string]$Path, [bool]$RequireSecure, [string]$SameSite) {
    $cookies = Header-Text $Response "Set-Cookie"
    $line = @($cookies -split "`n" | Where-Object { $_ -match "^$([regex]::Escape($Name))=" }) | Select-Object -First 1
    if (-not $line) { throw "Expected refresh cookie $Name was not set." }
    if ($line -notmatch "(?i)(?:^|;\s*)HttpOnly(?:;|$)") { throw "Refresh cookie $Name is missing HttpOnly." }
    if ($line -notmatch "(?i)(?:^|;\s*)SameSite=$([regex]::Escape($SameSite))(?:;|$)") { throw "Refresh cookie $Name does not have SameSite=$SameSite." }
    if ($line -notmatch "(?i)(?:^|;\s*)Path=$([regex]::Escape($Path))(?:;|$)") { throw "Refresh cookie $Name does not have Path=$Path." }
    $hasSecure = $line -match "(?i)(?:^|;\s*)Secure(?:;|$)"
    if ($hasSecure -ne $RequireSecure) { throw "Refresh cookie $Name Secure attribute differs from PREVIEW_EXPECT_COOKIE_SECURE." }
}

function Invoke-StatusRequest([string]$Uri, [hashtable]$Headers) {
    $params = @{ Uri = $Uri; Method = "Get"; SkipHttpErrorCheck = $true }
    if ($Headers) { $params.Headers = $Headers }
    return Invoke-WebRequest @params
}

function Wait-ForPrimaryHealth {
    $checks = @(
        @{ Uri = "$UserBaseUrl/healthz"; Label = "primary user frontend" },
        @{ Uri = "$AdminBaseUrl/healthz"; Label = "primary admin frontend" },
        @{ Uri = "$UserBaseUrl/api/v1/config/public"; Label = "primary backend through Nginx" }
    )
    $deadline = (Get-Date).AddSeconds(120)
    do {
        $allHealthy = $true
        foreach ($check in $checks) {
            try {
                if ((Invoke-StatusRequest $check.Uri $null).StatusCode -ne 200) { $allHealthy = $false }
            } catch {
                $allHealthy = $false
            }
        }
        if ($allHealthy) { return }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    throw "Primary services did not become healthy within 120 seconds."
}

$userEmail = Require-Variable "PREVIEW_USER_EMAIL"
$userPassword = Require-Variable "PREVIEW_USER_PASSWORD"
$adminEmail = Require-Variable "PREVIEW_ADMIN_EMAIL"
$adminPassword = Require-Variable "PREVIEW_ADMIN_PASSWORD"
$expectedSecure = ([Environment]::GetEnvironmentVariable("PREVIEW_EXPECT_COOKIE_SECURE", "Process") -eq "true")
$expectedSameSite = [Environment]::GetEnvironmentVariable("PREVIEW_EXPECT_COOKIE_SAMESITE", "Process")
if ([string]::IsNullOrWhiteSpace($expectedSameSite)) { $expectedSameSite = "Lax" }

Wait-ForPrimaryHealth
$userSession = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$userLogin = Invoke-WebRequest -Uri "$UserBaseUrl/api/v1/auth/login" -Method Post -WebSession $userSession -ContentType "application/x-www-form-urlencoded" -Body @{ username = $userEmail; password = $userPassword }
Assert-Status $userLogin 200 "primary user login"
Assert-RefreshCookie $userLogin "zhishu_refresh" "/api/v1/auth" $expectedSecure $expectedSameSite
$userToken = ($userLogin.Content | ConvertFrom-Json).access_token

$adminSession = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$adminLogin = Invoke-WebRequest -Uri "$AdminBaseUrl/api/v1/auth/admin/login" -Method Post -WebSession $adminSession -ContentType "application/x-www-form-urlencoded" -Body @{ username = $adminEmail; password = $adminPassword }
Assert-Status $adminLogin 200 "primary administrator login"
Assert-RefreshCookie $adminLogin "zhishu_admin_refresh" "/api/v1/auth/admin" $expectedSecure $expectedSameSite
$adminToken = ($adminLogin.Content | ConvertFrom-Json).access_token

Assert-Status (Invoke-StatusRequest "$UserBaseUrl/api/v1/users/me" @{ Authorization = "Bearer $userToken" }) 200 "user token on user surface"
Assert-Status (Invoke-StatusRequest "$AdminBaseUrl/api/v1/admin/me" @{ Authorization = "Bearer $adminToken" }) 200 "admin token on admin surface"
Assert-Status (Invoke-StatusRequest "$AdminBaseUrl/api/v1/admin/me" @{ Authorization = "Bearer $userToken" }) 401 "user token rejected by admin surface"
Assert-Status (Invoke-StatusRequest "$UserBaseUrl/api/v1/users/me" @{ Authorization = "Bearer $adminToken" }) 401 "admin token rejected by user surface"

if ($RestartServices) {
    Push-Location $repoRoot
    try {
        docker compose -f docker-compose.yml restart backend frontend admin
        if ($LASTEXITCODE -ne 0) { throw "Primary service restart failed." }
    } finally {
        Pop-Location
    }
    Wait-ForPrimaryHealth
}

$userRefresh = Invoke-WebRequest -Uri "$UserBaseUrl/api/v1/auth/refresh-token" -Method Post -WebSession $userSession
Assert-Status $userRefresh 200 "user refresh-cookie recovery"
Assert-RefreshCookie $userRefresh "zhishu_refresh" "/api/v1/auth" $expectedSecure $expectedSameSite
$adminRefresh = Invoke-WebRequest -Uri "$AdminBaseUrl/api/v1/auth/admin/refresh-token" -Method Post -WebSession $adminSession
Assert-Status $adminRefresh 200 "administrator refresh-cookie recovery"
Assert-RefreshCookie $adminRefresh "zhishu_admin_refresh" "/api/v1/auth/admin" $expectedSecure $expectedSameSite

Write-Output "Formal takeover permissions, scoped refresh cookies, and session recovery passed."
