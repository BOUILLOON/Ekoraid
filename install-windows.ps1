[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Set-Location $PSScriptRoot

$PythonCommand = $null
$PythonPrefix = @()
if (Get-Command py -ErrorAction SilentlyContinue) {
    $PythonCommand = 'py'
    $PythonPrefix = @('-3')
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $PythonCommand = 'python'
} else {
    throw 'Python 3.12 ou plus est requis. Installez-le depuis python.org, ou : winget install --exact --id Python.Python.3.12 --source winget. Ouvrez ensuite un nouveau terminal.'
}
& $PythonCommand @PythonPrefix -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'
if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 ou plus est requis. Vérifiez la version sélectionnée avec py -3 --version.' }

if (-not (Test-Path '.venv\Scripts\python.exe')) {
    & $PythonCommand @PythonPrefix -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Création de .venv impossible. Vérifiez les droits du dossier et Python.' }
}
$VenvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& $VenvPython -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'
if ($LASTEXITCODE -ne 0) { throw 'Le dossier .venv existant utilise une version trop ancienne de Python. Conservez-le ailleurs, puis relancez l’installation.' }
& $VenvPython -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('pip') else 1)"
if ($LASTEXITCODE -ne 0) {
    & $VenvPython -m ensurepip --upgrade
    if ($LASTEXITCODE -ne 0) { throw 'pip est absent et son initialisation a échoué. Vérifiez votre installation Python.' }
}
& $VenvPython -m pip install --disable-pip-version-check -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Installation des dépendances impossible. Vérifiez Internet et le message pip ; la vérification TLS reste activée.' }
& $VenvPython -m playlist25 init
if ($LASTEXITCODE -ne 0) { throw 'Initialisation impossible. Vérifiez les droits du dossier et la configuration .env.local.' }

$MissingTools = @()
foreach ($Tool in @('ffmpeg', 'ffprobe')) {
    if (-not (Get-Command $Tool -ErrorAction SilentlyContinue)) { $MissingTools += $Tool }
}
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    $MissingTools += 'Node.js 20 ou plus'
} else {
    $NodeVersion = & node --version
    if ($LASTEXITCODE -ne 0) {
        $MissingTools += 'Node.js 20 ou plus'
    } elseif ($NodeVersion -match '^v(\d+)\.') {
        if ([int]$Matches[1] -lt 20) { $MissingTools += 'Node.js 20 ou plus' }
    } else {
        $MissingTools += 'Node.js 20 ou plus'
    }
}
& $VenvPython -m playlist25 doctor
if ($LASTEXITCODE -ne 0 -or $MissingTools.Count -gt 0) {
    Write-Warning ('La collecte peut démarrer, mais les outils audio/YouTube doivent être vérifiés : ' + ($MissingTools -join ', '))
    Write-Host 'Installations optionnelles à exécuter vous-même si nécessaires :'
    Write-Host '  winget install --exact --id Gyan.FFmpeg --source winget'
    Write-Host '  winget install --exact --id OpenJS.NodeJS.LTS --source winget'
    Write-Host 'Après installation, ouvrez un nouveau terminal et relancez .venv\Scripts\python.exe -m playlist25 doctor.'
}
Write-Host 'Installation Python terminée. Votre .env.local existant a été conservé.'
Write-Host 'Choisir votre mot de passe : .venv\Scripts\python.exe -m playlist25 configure-admin'
Write-Host 'Lancer : powershell -NoProfile -File .\Start-Windows.ps1'
