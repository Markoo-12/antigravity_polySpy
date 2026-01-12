<#
.SYNOPSIS
    Starts the Polygon Insider Sentinel in a persistent loop.
    Restarts automatically if the process crashes.

.DESCRIPTION
    This script activates the virtual environment (if present) or uses the global python,
    then runs the Sentinel main.py script. It includes a 5-second cooldown between restarts
    to prevent CPU thrashing in case of persistent errors.

.EXAMPLE
    .\run_sentinel.ps1
#>

$ScriptPath = $PSScriptRoot
$MainScript = Join-Path $ScriptPath "main.py"

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "   POLYGON INSIDER SENTINEL - RUNNER" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "Starting monitoring..." -ForegroundColor Yellow

while ($true) {
    try {
        # Run the sentinel
        python $MainScript
    }
    catch {
        Write-Host "Error occurred: $_" -ForegroundColor Red
    }

    Write-Host "Sentinel stopped. Restarting in 5 seconds..." -ForegroundColor Magenta
    Start-Sleep -Seconds 5
}
