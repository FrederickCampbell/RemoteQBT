$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $Root '.venv\Scripts\python.exe'
$Python = if (Test-Path $VenvPython) { $VenvPython } elseif (Test-Path 'C:\_Hub\Dev\Python314\python.exe') { 'C:\_Hub\Dev\Python314\python.exe' } else { (Get-Command python.exe -ErrorAction Stop).Source }
& $Python (Join-Path $Root 'remoteqbt.py') @args
