$ErrorActionPreference = 'Stop'
$InstallRoot = Join-Path $env:LOCALAPPDATA 'Programs\RemoteQBT'
$Exe = Join-Path $InstallRoot 'RemoteQBT.exe'
$DesktopShortcut = Join-Path ([Environment]::GetFolderPath('Desktop')) 'RemoteQBT.lnk'
$StartFolder = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\RemoteQBT'

Get-Process RemoteQBT -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
if (Test-Path -LiteralPath $Exe) {
    try { & $Exe --unregister-associations | Out-Host } catch { Write-Warning $_ }
}
Remove-Item $DesktopShortcut -Force -ErrorAction SilentlyContinue
Remove-Item $StartFolder -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $InstallRoot -Recurse -Force -ErrorAction SilentlyContinue

Write-Host 'RemoteQBT application, shortcuts, and handler registration removed.' -ForegroundColor Green
Write-Host 'Your encrypted config in %APPDATA%\RemoteQBT was intentionally kept.'
