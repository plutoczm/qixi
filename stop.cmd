@echo off
setlocal
set "TRAY=%~dp0runtime\launcher\XunqiTray.exe"
if exist "%TRAY%" start "" "%TRAY%" --quit
exit /b 0
