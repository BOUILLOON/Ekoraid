[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Set-Location $PSScriptRoot
$VenvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $VenvPython)) { throw 'Installation manquante : .venv\Scripts\python.exe.' }
& $VenvPython -m playlist25 stop
if ($LASTEXITCODE -ne 0) { throw 'Arrêt incomplet. Consultez le message ci-dessus.' }
