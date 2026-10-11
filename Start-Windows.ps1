[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Set-Location $PSScriptRoot
$VenvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $VenvPython)) { throw 'Installation manquante. Lancez install-windows.ps1, ou suivez docs/INSTALLATION.md.' }
& $VenvPython -m playlist25 start
if ($LASTEXITCODE -ne 0) { throw 'Le lancement a échoué. Consultez le message ci-dessus et docs/INSTALLATION.md.' }
