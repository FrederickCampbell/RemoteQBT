param(
    [string]$ReleaseTag = ''
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$MetaJson = (& python -c "import json, rqbt.version as v; print(json.dumps({'release_id':v.RELEASE_ID,'release_tag':v.RELEASE_TAG,'qbittorrent_version':v.QBITTORRENT_VERSION,'revision':v.REMOTEQBT_REVISION,'display':v.DISPLAY_VERSION}))").Trim()
if (-not $MetaJson) { throw 'Could not determine RemoteQBT release identity.' }
$Meta = $MetaJson | ConvertFrom-Json

if (-not $ReleaseTag) { $ReleaseTag = $Meta.release_tag }
if ($ReleaseTag -ne $Meta.release_tag) {
    throw "Requested tag $ReleaseTag does not match source identity $($Meta.release_tag)."
}

python scripts/release_identity.py check
if ($LASTEXITCODE -ne 0) { throw 'Release identity consistency check failed.' }

python -m pip install --disable-pip-version-check --upgrade pip wheel
python -m pip install --disable-pip-version-check -r requirements.txt

Remove-Item build, dist, release-stage, artifacts -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path artifacts -Force | Out-Null

python -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --onedir `
    --name RemoteQBT `
    --icon (Join-Path $Root 'assets\qbt\qbittorrent.ico') `
    --add-data "$(Join-Path $Root 'assets');assets" `
    remoteqbt.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

$Stage = Join-Path $Root 'release-stage'
$AppStage = Join-Path $Stage 'RemoteQBT'
New-Item -ItemType Directory -Path $Stage -Force | Out-Null
Copy-Item (Join-Path $Root 'dist\RemoteQBT') $AppStage -Recurse -Force
Copy-Item LICENSE-GPLv3.txt, THIRD_PARTY_NOTICES.md -Destination $AppStage -Force
$Meta.release_id | Set-Content -LiteralPath (Join-Path $AppStage 'RELEASE-ID.txt') -Encoding ascii
Copy-Item Update-RemoteQBT.ps1 -Destination $Stage -Force
@"
RemoteQBT for qBittorrent $($Meta.qbittorrent_version)
Release identity: $($Meta.release_id)
Git tag: $($Meta.release_tag)

Run RemoteQBT\RemoteQBT.exe directly, or install through the normal RemoteQBT installer.
Existing user configuration is stored outside the application directory and is preserved by updates.
"@ | Set-Content -LiteralPath (Join-Path $Stage 'README.txt') -Encoding UTF8

$Zip = Join-Path $Root "artifacts\RemoteQBT-$($Meta.release_tag)-Windows.zip"
Compress-Archive -LiteralPath (Join-Path $Stage 'RemoteQBT'), (Join-Path $Stage 'Update-RemoteQBT.ps1'), (Join-Path $Stage 'README.txt') -DestinationPath $Zip -CompressionLevel Optimal
$Hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Zip).Hash.ToLowerInvariant()
"$Hash  $(Split-Path -Leaf $Zip)" | Set-Content -LiteralPath "$Zip.sha256" -Encoding ascii

Write-Host "Built: $Zip"
Write-Host "SHA256: $Hash"
