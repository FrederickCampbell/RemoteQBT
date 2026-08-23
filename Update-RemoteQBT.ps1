param(
    [Parameter(Mandatory)][string]$PackageRoot,
    [int]$ParentPid = 0,
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'Programs\RemoteQBT'),
    [string]$ReleaseId = '',
    [string]$StateDir = (Join-Path $env:LOCALAPPDATA 'RemoteQBT'),
    [string]$ConfigDir = (Join-Path $env:APPDATA 'RemoteQBT'),
    [switch]$Relaunch,
    [switch]$SkipAssociations,
    [switch]$SkipProcessShutdown,
    [switch]$Quiet
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Set-StrictMode -Version Latest

$LogFile = Join-Path $StateDir 'Update-RemoteQBT.log'
$ResultFile = Join-Path $ConfigDir 'update-result.json'
New-Item -ItemType Directory -Path $StateDir, $ConfigDir -Force | Out-Null

function Write-Log([string]$Message) {
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff') $Message"
    Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
}

function Write-Result([string]$Status, [string]$Message) {
    [ordered]@{
        status = $Status
        release_id = $ReleaseId
        message = $Message
        time = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    } | ConvertTo-Json | Set-Content -LiteralPath $ResultFile -Encoding UTF8
}

$UpdateForm = $null
$UpdateLabel = $null

function Start-UpdateUi([string]$Text) {
    if ($Quiet -or $env:CI) { return }
    try {
        Add-Type -AssemblyName System.Windows.Forms
        Add-Type -AssemblyName System.Drawing
        $script:UpdateForm = New-Object System.Windows.Forms.Form
        $script:UpdateForm.Text = 'RemoteQBT Update'
        $script:UpdateForm.Width = 470
        $script:UpdateForm.Height = 150
        $script:UpdateForm.StartPosition = 'CenterScreen'
        $script:UpdateForm.FormBorderStyle = 'FixedDialog'
        $script:UpdateForm.MaximizeBox = $false
        $script:UpdateForm.MinimizeBox = $false
        $script:UpdateForm.TopMost = $true

        $script:UpdateLabel = New-Object System.Windows.Forms.Label
        $script:UpdateLabel.AutoSize = $false
        $script:UpdateLabel.Left = 20
        $script:UpdateLabel.Top = 18
        $script:UpdateLabel.Width = 415
        $script:UpdateLabel.Height = 42
        $script:UpdateLabel.Text = $Text
        $script:UpdateForm.Controls.Add($script:UpdateLabel)

        $bar = New-Object System.Windows.Forms.ProgressBar
        $bar.Left = 20
        $bar.Top = 68
        $bar.Width = 415
        $bar.Height = 22
        $bar.Style = 'Marquee'
        $bar.MarqueeAnimationSpeed = 25
        $script:UpdateForm.Controls.Add($bar)

        $script:UpdateForm.Show()
        [System.Windows.Forms.Application]::DoEvents()
    }
    catch {
        Write-Log "Updater progress UI unavailable: $($_.Exception.Message)"
        $script:UpdateForm = $null
        $script:UpdateLabel = $null
    }
}

function Set-UpdateUi([string]$Text) {
    if ($null -eq $script:UpdateForm) { return }
    try {
        $script:UpdateLabel.Text = $Text
        [System.Windows.Forms.Application]::DoEvents()
    } catch { }
}

function Stop-UpdateUi {
    if ($null -eq $script:UpdateForm) { return }
    try {
        $script:UpdateForm.Close()
        $script:UpdateForm.Dispose()
    } catch { }
    $script:UpdateForm = $null
    $script:UpdateLabel = $null
}

function Wait-ForRemoteQbtExit([int]$PidToWait) {
    if ($SkipProcessShutdown) {
        Write-Log 'Process shutdown skipped for updater smoke test.'
        return
    }

    if ($PidToWait -gt 0) {
        Write-Log "Waiting for parent RemoteQBT PID $PidToWait."
        for ($i = 0; $i -lt 240; $i++) {
            if (-not (Get-Process -Id $PidToWait -ErrorAction SilentlyContinue)) { break }
            Start-Sleep -Milliseconds 250
            if ($i -eq 239) {
                Write-Log "Parent PID $PidToWait did not exit in 60 seconds; forcing it closed."
                Get-Process -Id $PidToWait -ErrorAction SilentlyContinue |
                    Stop-Process -Force -ErrorAction SilentlyContinue
            }
        }
    }

    Get-Process RemoteQBT -ErrorAction SilentlyContinue |
        Stop-Process -Force -ErrorAction SilentlyContinue

    for ($i = 0; $i -lt 80; $i++) {
        if (-not (Get-Process RemoteQBT -ErrorAction SilentlyContinue)) { return }
        Start-Sleep -Milliseconds 250
    }
    throw 'RemoteQBT processes did not fully exit before installation.'
}

function Move-WithRetry([string]$Source, [string]$Destination) {
    $last = $null
    for ($i = 1; $i -le 40; $i++) {
        try {
            Move-Item -LiteralPath $Source -Destination $Destination -Force
            return
        }
        catch {
            $last = $_
            Start-Sleep -Milliseconds 250
        }
    }
    throw "Could not move '$Source' to '$Destination' after 10 seconds: $($last.Exception.Message)"
}

$SourceDir = Join-Path $PackageRoot 'RemoteQBT'
$SourceExe = Join-Path $SourceDir 'RemoteQBT.exe'
$SourceReleaseFile = Join-Path $SourceDir 'RELEASE-ID.txt'
$Parent = Split-Path -Parent $InstallRoot
$Stage = "$InstallRoot.__new"
$Backup = "$InstallRoot.__old"
$InstalledExe = Join-Path $InstallRoot 'RemoteQBT.exe'
$InstalledReleaseFile = Join-Path $InstallRoot 'RELEASE-ID.txt'

try {
    Write-Log '------------------------------------------------------------'
    Write-Log "Updater started. PackageRoot=$PackageRoot InstallRoot=$InstallRoot ParentPid=$ParentPid"

    if (-not (Test-Path -LiteralPath $SourceExe)) {
        throw "Update package is missing: $SourceExe"
    }

    if (-not (Test-Path -LiteralPath $SourceReleaseFile)) {
        throw "Update package is missing release identity marker: $SourceReleaseFile"
    }

    $PackageReleaseId = (Get-Content -LiteralPath $SourceReleaseFile -Raw).Trim()
    if ([string]::IsNullOrWhiteSpace($PackageReleaseId)) {
        throw 'Update package contains an empty release identity marker.'
    }

    # Old RemoteQBT builds do not pass -ReleaseId. The new package carries its
    # own SHA-256-protected identity marker, so old callers can still install it.
    if ([string]::IsNullOrWhiteSpace($ReleaseId)) {
        $ReleaseId = $PackageReleaseId
    }
    elseif ($ReleaseId -ne $PackageReleaseId) {
        throw "Updater release identity '$ReleaseId' does not match package identity '$PackageReleaseId'."
    }

    Write-Result 'installing' "Installing RemoteQBT $ReleaseId."
    Start-UpdateUi "Preparing RemoteQBT $ReleaseId…"

    New-Item -ItemType Directory -Path $Parent -Force | Out-Null
    Remove-Item $Stage -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item $Backup -Recurse -Force -ErrorAction SilentlyContinue

    Set-UpdateUi "Staging RemoteQBT $ReleaseId…"
    Write-Log "Copying complete package tree to staging folder: $Stage"
    Copy-Item $SourceDir $Stage -Recurse -Force

    Set-UpdateUi 'Waiting for RemoteQBT to close…'
    Wait-ForRemoteQbtExit $ParentPid

    Set-UpdateUi "Installing RemoteQBT $ReleaseId…"
    if (Test-Path -LiteralPath $InstallRoot) {
        Write-Log "Moving current installation to rollback folder: $Backup"
        Move-WithRetry $InstallRoot $Backup
    }

    try {
        Write-Log 'Activating staged installation.'
        Move-WithRetry $Stage $InstallRoot
    }
    catch {
        if (Test-Path -LiteralPath $Backup) {
            Write-Log 'Activation failed; restoring rollback folder.'
            Move-WithRetry $Backup $InstallRoot
        }
        throw
    }

    Set-UpdateUi 'Verifying the installed build…'
    if (-not (Test-Path -LiteralPath $InstalledExe)) {
        throw "Installed executable is missing: $InstalledExe"
    }

    if (-not (Test-Path -LiteralPath $InstalledReleaseFile)) {
        throw "Installed release identity marker is missing: $InstalledReleaseFile"
    }
    $Reported = (Get-Content -LiteralPath $InstalledReleaseFile -Raw).Trim()
    Write-Log "Installed application tree reports release identity: $Reported"
    if ($Reported -ne $ReleaseId) {
        throw "Installed build verification failed. Expected '$ReleaseId', got '$Reported'."
    }

    if (-not $SkipAssociations) {
        Set-UpdateUi 'Repairing Windows file associations…'
        try {
            & $InstalledExe --register-associations | Out-Null
            Write-Log 'Windows associations repaired.'
        }
        catch {
            Write-Log "Association repair warning: $($_.Exception.Message)"
        }
    }

    Remove-Item $Backup -Recurse -Force -ErrorAction SilentlyContinue
    Write-Result 'success' "RemoteQBT $ReleaseId was installed and verified successfully."
    Write-Log "SUCCESS: RemoteQBT $ReleaseId installed and verified."

    Set-UpdateUi "RemoteQBT $ReleaseId is installed. Restarting…"
    Start-Sleep -Milliseconds 500
    Stop-UpdateUi

    if ($Relaunch) {
        Start-Process $InstalledExe
    }
    exit 0
}
catch {
    $message = $_.Exception.Message
    Write-Log "FAILURE: $message"

    try {
        Remove-Item $Stage -Recurse -Force -ErrorAction SilentlyContinue
        if (Test-Path -LiteralPath $Backup) {
            Write-Log 'Restoring previous installation after failure.'
            Remove-Item $InstallRoot -Recurse -Force -ErrorAction SilentlyContinue
            Move-WithRetry $Backup $InstallRoot
        }
    }
    catch {
        Write-Log "ROLLBACK FAILURE: $($_.Exception.Message)"
        $message += " Rollback also reported: $($_.Exception.Message)"
    }

    Write-Result 'failed' $message
    Stop-UpdateUi

    if (-not $Quiet -and -not $env:CI) {
        try {
            Add-Type -AssemblyName System.Windows.Forms
            [System.Windows.Forms.MessageBox]::Show(
                "RemoteQBT could not complete the update.`r`n`r`n$message`r`n`r`nThe previous installation was restored when possible.`r`n`r`nUpdate log:`r`n$LogFile",
                'RemoteQBT Update Failed',
                [System.Windows.Forms.MessageBoxButtons]::OK,
                [System.Windows.Forms.MessageBoxIcon]::Error
            ) | Out-Null
        } catch { }
    }

    $RollbackExe = Join-Path $InstallRoot 'RemoteQBT.exe'
    if ($Relaunch -and (Test-Path -LiteralPath $RollbackExe)) {
        Start-Process $RollbackExe
    }
    exit 1
}
