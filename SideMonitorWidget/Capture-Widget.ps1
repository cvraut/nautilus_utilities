# Captures a screenshot of the SideMonitorWidget skin as it appears on screen.
# Position comes from Rainmeter.ini; size from Widget.ini (SkinWidth/SkinHeight).
# Usage: .\Capture-Widget.ps1 [-OutFile path.png] [-Refresh]
param(
    [string]$OutFile = (Join-Path $env:TEMP 'SideMonitorWidget.png'),
    [switch]$Refresh,          # refresh the skin first, then wait for it to redraw
    [int]$WaitSeconds = 3
)

if ($Refresh) {
    & "C:\Program Files\Rainmeter\Rainmeter.exe" '!Refresh' 'SideMonitorWidget'
    Start-Sleep -Seconds $WaitSeconds
}

Add-Type -AssemblyName System.Drawing
Add-Type @'
using System.Runtime.InteropServices;
public static class DpiFix { [DllImport("user32.dll")] public static extern bool SetProcessDPIAware(); }
'@
[DpiFix]::SetProcessDPIAware() | Out-Null

# Read the skin position from Rainmeter.ini
$cfg = Get-Content "$env:APPDATA\Rainmeter\Rainmeter.ini"
$start = [array]::IndexOf($cfg, '[SideMonitorWidget]')
if ($start -lt 0) { throw 'SideMonitorWidget section not found in Rainmeter.ini' }
$x = 0; $y = 0
for ($i = $start + 1; $i -lt $cfg.Count -and $cfg[$i] -notmatch '^\['; $i++) {
    if ($cfg[$i] -match '^WindowX=(-?\d+)') { $x = [int]$Matches[1] }
    if ($cfg[$i] -match '^WindowY=(-?\d+)') { $y = [int]$Matches[1] }
}

# Read the skin size from Widget.ini (UTF-16)
$skin = Get-Content (Join-Path $PSScriptRoot 'Widget.ini') -Encoding Unicode
$w = [int](($skin | Select-String '^SkinWidth=(\d+)').Matches[0].Groups[1].Value)
$h = [int](($skin | Select-String '^SkinHeight=(\d+)').Matches[0].Groups[1].Value)

$bmp = New-Object System.Drawing.Bitmap $w, $h
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($x, $y, 0, 0, (New-Object System.Drawing.Size $w, $h))
$bmp.Save($OutFile, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
Write-Output "Saved $($w)x$($h) capture at ($x,$y) to $OutFile"
