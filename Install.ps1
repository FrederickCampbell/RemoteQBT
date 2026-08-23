param(
    [switch]$NoLaunch,
    [switch]$SkipAssociations
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$SourceDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$PreferredProjectRoot = 'C:\_Hub\Projects\RemoteQBT\current'
$ProjectRoot = if (Test-Path -LiteralPath 'C:\_Hub\Projects') { $PreferredProjectRoot } else { Join-Path $env:LOCALAPPDATA 'RemoteQBT\Source' }
$InstallRoot = Join-Path $env:LOCALAPPDATA 'Programs\RemoteQBT'
$VenvRoot    = Join-Path $ProjectRoot '.venv'
$VenvPython  = Join-Path $VenvRoot 'Scripts\python.exe'

$IdentitySource = Get-Content -LiteralPath (Join-Path $SourceDir 'rqbt\version.py') -Raw
$QbtVersion = ([regex]::Match($IdentitySource, 'QBITTORRENT_VERSION\s*=\s*"([^"]+)"')).Groups[1].Value
$RemoteRevision = ([regex]::Match($IdentitySource, 'REMOTEQBT_REVISION\s*=\s*(\d+)')).Groups[1].Value
if (-not $QbtVersion -or -not $RemoteRevision) {
    throw 'Could not read qBittorrent-aligned RemoteQBT release identity.'
}

function Find-Python314 {
    $candidates = @(
        'C:\_Hub\Dev\Python314\python.exe',
        'C:\_Hub\Dev\Python\python.exe',
        'C:\Program Files\Python314\python.exe'
    )
    foreach ($p in $candidates) {
        if (Test-Path -LiteralPath $p) { return $p }
    }
    $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    throw "Python was not found. Checked:`n$($candidates -join "`n")"
}

function Make-Shortcut {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Target,
        [string]$WorkingDirectory = '',
        [string]$Description = 'Remote qBittorrent client'
    )
    New-Item -ItemType Directory -Path (Split-Path -Parent $Path) -Force | Out-Null
    $ws = New-Object -ComObject WScript.Shell
    $sc = $ws.CreateShortcut($Path)
    $sc.TargetPath = $Target
    if ($WorkingDirectory) { $sc.WorkingDirectory = $WorkingDirectory }
    $sc.Description = $Description
    $sc.IconLocation = "$Target,0"
    $sc.Save()
}

Write-Host ''
Write-Host " RemoteQBT for qBittorrent $QbtVersion (r$RemoteRevision)" -ForegroundColor Cyan
Write-Host ' Remote qBittorrent, controlled locally.' -ForegroundColor DarkCyan
Write-Host ''

$Python = Find-Python314
Write-Host "Python: $Python" -ForegroundColor DarkGray

# Keep editable source in a stable, generation-free project directory.
New-Item -ItemType Directory -Path $ProjectRoot -Force | Out-Null
Get-ChildItem -LiteralPath $ProjectRoot -Force -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -notin @('.venv') } |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

Copy-Item (Join-Path $SourceDir 'remoteqbt.py') $ProjectRoot -Force
Copy-Item (Join-Path $SourceDir 'rqbt') $ProjectRoot -Recurse -Force
Copy-Item (Join-Path $SourceDir 'assets') $ProjectRoot -Recurse -Force
Copy-Item (Join-Path $SourceDir 'README.md') $ProjectRoot -Force
Copy-Item (Join-Path $SourceDir 'THIRD_PARTY_NOTICES.md') $ProjectRoot -Force
Copy-Item (Join-Path $SourceDir 'LICENSE-GPLv3.txt') $ProjectRoot -Force
Copy-Item (Join-Path $SourceDir 'requirements.txt') $ProjectRoot -Force
Copy-Item (Join-Path $SourceDir 'Uninstall.ps1') $ProjectRoot -Force
Copy-Item (Join-Path $SourceDir 'Run-Dev.ps1') $ProjectRoot -Force
if (Test-Path (Join-Path $SourceDir 'Update-RemoteQBT.ps1')) { Copy-Item (Join-Path $SourceDir 'Update-RemoteQBT.ps1') $ProjectRoot -Force }

if (-not (Test-Path -LiteralPath $VenvPython)) {
    Write-Host 'Creating isolated build environment...' -ForegroundColor Cyan
    & $Python -m venv $VenvRoot
}

Write-Host 'Installing/updating lightweight Qt Essentials + PyInstaller...' -ForegroundColor Cyan
& $VenvPython -m pip install --disable-pip-version-check --upgrade pip wheel
& $VenvPython -m pip install --disable-pip-version-check --upgrade PySide6-Essentials pyinstaller
if ($LASTEXITCODE -ne 0) {
    Write-Warning 'PySide6-Essentials install failed; falling back to full PySide6.'
    & $VenvPython -m pip install --disable-pip-version-check --upgrade PySide6 pyinstaller
    if ($LASTEXITCODE -ne 0) { throw 'Could not install PySide6/PyInstaller build dependencies.' }
}

Get-Process RemoteQBT -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 250

$Dist  = Join-Path $ProjectRoot 'dist'
$Build = Join-Path $ProjectRoot 'build'
Remove-Item $Dist -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $Build -Recurse -Force -ErrorAction SilentlyContinue

Write-Host 'Building native-launch Windows app...' -ForegroundColor Cyan
& $VenvPython -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --onedir `
    --name RemoteQBT `
    --icon (Join-Path $ProjectRoot 'assets\qbt\qbittorrent.ico') `
    --add-data "$(Join-Path $ProjectRoot 'assets');assets" `
    --distpath $Dist `
    --workpath $Build `
    --specpath $ProjectRoot `
    (Join-Path $ProjectRoot 'remoteqbt.py')
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

$BuiltDir = Join-Path $Dist 'RemoteQBT'
$BuiltExe = Join-Path $BuiltDir 'RemoteQBT.exe'
if (-not (Test-Path -LiteralPath $BuiltExe)) {
    throw "Build completed without producing $BuiltExe"
}

Write-Host 'Installing to AppData...' -ForegroundColor Cyan
if (Test-Path -LiteralPath $InstallRoot) {
    Remove-Item $InstallRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
Copy-Item (Join-Path $BuiltDir '*') $InstallRoot -Recurse -Force
Copy-Item (Join-Path $ProjectRoot 'THIRD_PARTY_NOTICES.md') $InstallRoot -Force
Copy-Item (Join-Path $ProjectRoot 'LICENSE-GPLv3.txt') $InstallRoot -Force

$InstalledExe = Join-Path $InstallRoot 'RemoteQBT.exe'
$Desktop = [Environment]::GetFolderPath('Desktop')
$StartPrograms = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\RemoteQBT'

Make-Shortcut -Path (Join-Path $Desktop 'RemoteQBT.lnk') -Target $InstalledExe -WorkingDirectory $InstallRoot -Description 'Remote control qBittorrent'
Make-Shortcut -Path (Join-Path $StartPrograms 'RemoteQBT.lnk') -Target $InstalledExe -WorkingDirectory $InstallRoot -Description 'Remote control qBittorrent'

if (-not $SkipAssociations) {
    Write-Host 'Registering magnet and .torrent handlers...' -ForegroundColor Cyan
    & $InstalledExe --register-associations | Out-Host
}

Write-Host ''
Write-Host 'Installed successfully.' -ForegroundColor Green
Write-Host "App:      $InstalledExe"
Write-Host "Source:   $ProjectRoot"
Write-Host "Desktop:  $(Join-Path $Desktop 'RemoteQBT.lnk')"
Write-Host "Start:    $(Join-Path $StartPrograms 'RemoteQBT.lnk')"
Write-Host ''
Write-Host 'Existing encrypted RemoteQBT API settings are preserved.' -ForegroundColor DarkGray
Write-Host 'Existing encrypted RemoteQBT configuration remains outside the application directory.' -ForegroundColor DarkGray
Write-Host ''

if (-not $NoLaunch) {
    Write-Host 'Launching RemoteQBT...' -ForegroundColor Cyan
    Start-Process $InstalledExe
}
