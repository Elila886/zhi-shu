param(
    [string]$UserBaseUrl = "http://localhost:8511",
    [string]$AdminBaseUrl = "http://localhost:8512"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "Import-PreviewEnvironment.ps1")
Import-PreviewEnvironment -RepoRoot $repoRoot

function Require-PreviewVariable([string]$Name) {
    $value = [Environment]::GetEnvironmentVariable($Name, "Process")
    if ([string]::IsNullOrWhiteSpace($value)) {
        [Console]::Error.WriteLine("Preview verification is environment-blocked: $Name is not set.")
        exit 2
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

$userEmail = Require-PreviewVariable "PREVIEW_USER_EMAIL"
$userPassword = Require-PreviewVariable "PREVIEW_USER_PASSWORD"
$adminEmail = Require-PreviewVariable "PREVIEW_ADMIN_EMAIL"
$adminPassword = Require-PreviewVariable "PREVIEW_ADMIN_PASSWORD"
$streamThreadId = Require-PreviewVariable "PREVIEW_REAL_AI_THREAD_ID"
$streamModel = Require-PreviewVariable "PREVIEW_REAL_AI_MODEL_NAME"
$expectedSecure = ([Environment]::GetEnvironmentVariable("PREVIEW_EXPECT_COOKIE_SECURE", "Process") -eq "true")
$expectedSameSite = [Environment]::GetEnvironmentVariable("PREVIEW_EXPECT_COOKIE_SAMESITE", "Process")
if ([string]::IsNullOrWhiteSpace($expectedSameSite)) { $expectedSameSite = "Lax" }

foreach ($check in @(
    @{ Url = "$UserBaseUrl/healthz"; Label = "user preview health" },
    @{ Url = "$AdminBaseUrl/healthz"; Label = "admin preview health" },
    @{ Url = "$UserBaseUrl/api/v1/docs"; Label = "API docs" },
    @{ Url = "$UserBaseUrl/api/v1/config/public"; Label = "public configuration" }
)) {
    $response = Invoke-WebRequest -Uri $check.Url -Method Get
    Assert-Status $response 200 $check.Label
}

$userSession = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$userLogin = Invoke-WebRequest -Uri "$UserBaseUrl/api/v1/auth/login" -Method Post -WebSession $userSession -ContentType "application/x-www-form-urlencoded" -Body @{ username = $userEmail; password = $userPassword }
Assert-Status $userLogin 200 "user login"
Assert-RefreshCookie $userLogin "zhishu_refresh" "/api/v1/auth" $expectedSecure $expectedSameSite

# This is the API equivalent of a hard page reload: a new access token must be restored only from the HttpOnly refresh cookie.
$userRefresh = Invoke-WebRequest -Uri "$UserBaseUrl/api/v1/auth/refresh-token" -Method Post -WebSession $userSession
Assert-Status $userRefresh 200 "user refresh recovery"
Assert-RefreshCookie $userRefresh "zhishu_refresh" "/api/v1/auth" $expectedSecure $expectedSameSite

$streamBody = @{
    prompt = "Confirm the preview streaming channel in one sentence."
    model_name = $streamModel
}
$streamPayload = $streamBody | ConvertTo-Json -Compress
$stream = Invoke-WebRequest -Uri "$UserBaseUrl/api/v1/chat/$streamThreadId" -Method Post -WebSession $userSession -ContentType "application/json" -Body $streamPayload
Assert-Status $stream 200 "real AI stream"
if ((Header-Text $stream "Content-Type") -notmatch "(?i)application/x-ndjson") { throw "Real AI stream is missing application/x-ndjson Content-Type." }
if ((Header-Text $stream "Cache-Control") -notmatch "(?i)no-cache") { throw "Real AI stream is missing Cache-Control: no-cache." }
if ((Header-Text $stream "X-Accel-Buffering") -notmatch "(?i)no") { throw "Real AI stream is missing X-Accel-Buffering: no." }

foreach ($index in 1..2) {
    $logout = Invoke-WebRequest -Uri "$UserBaseUrl/api/v1/auth/logout" -Method Post -WebSession $userSession
    Assert-Status $logout 200 "user logout attempt $index"
}

$adminSession = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$adminLogin = Invoke-WebRequest -Uri "$AdminBaseUrl/api/v1/auth/admin/login" -Method Post -WebSession $adminSession -ContentType "application/x-www-form-urlencoded" -Body @{ username = $adminEmail; password = $adminPassword }
Assert-Status $adminLogin 200 "admin login"
Assert-RefreshCookie $adminLogin "zhishu_admin_refresh" "/api/v1/auth/admin" $expectedSecure $expectedSameSite
$adminRefresh = Invoke-WebRequest -Uri "$AdminBaseUrl/api/v1/auth/admin/refresh-token" -Method Post -WebSession $adminSession
Assert-Status $adminRefresh 200 "admin refresh recovery"
Assert-RefreshCookie $adminRefresh "zhishu_admin_refresh" "/api/v1/auth/admin" $expectedSecure $expectedSameSite

foreach ($index in 1..2) {
    $logout = Invoke-WebRequest -Uri "$AdminBaseUrl/api/v1/auth/admin/logout" -Method Post -WebSession $adminSession
    Assert-Status $logout 200 "admin logout attempt $index"
}

$env:PREVIEW_MODE = "1"
$env:PREVIEW_USER_BASE_URL = $UserBaseUrl
$env:PREVIEW_ADMIN_BASE_URL = $AdminBaseUrl
Push-Location (Join-Path $repoRoot "web")
try {
    npm run test:e2e -- e2e/preview-gate.spec.ts --project non-ai
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
    Pop-Location
}

Write-Output "Preview API, cookie, refresh-recovery, real NDJSON header, and idempotent logout checks passed."
