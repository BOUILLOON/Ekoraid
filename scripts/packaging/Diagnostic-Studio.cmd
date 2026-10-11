@echo off
setlocal
cd /d "%~dp0"
start "" /wait "%~dp0Studio-Fanny.exe" --diagnostic --open
if errorlevel 1 start "" notepad.exe "%~dp0Aide\Diagnostic-Studio.txt"
endlocal
