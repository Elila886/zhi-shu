param(
    [int[]]$Ports = @(8511, 8512)
)

$occupied = @()
foreach ($port in $Ports) {
    $listeners = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
    foreach ($listener in $listeners) {
        $occupied += [pscustomobject]@{
            Port = $port
            ProcessId = $listener.OwningProcess
            Address = $listener.LocalAddress
        }
    }
}

if ($occupied.Count -gt 0) {
    $occupied | Format-Table -AutoSize | Out-String | Write-Error
    Write-Error "Preview ports are occupied. Stop only the explicitly approved process or choose alternate PREVIEW_*_PORT values."
    exit 1
}

Write-Output ("Preview ports available: " + ($Ports -join ", "))
