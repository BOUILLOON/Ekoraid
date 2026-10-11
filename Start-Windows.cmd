@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Installation Python manquante dans ce dossier.
    echo Lancez install-windows.ps1, puis relancez ce fichier.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m playlist25 start
set "PLAYLIST25_START_EXIT_CODE=%ERRORLEVEL%"
if not "%PLAYLIST25_START_EXIT_CODE%"=="0" (
    echo Le lancement a echoue. Le message utile est affiche ci-dessus.
    pause
)
exit /b %PLAYLIST25_START_EXIT_CODE%
