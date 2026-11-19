@echo off
setlocal
cd /d "%~dp0"
set "TRAY=%~dp0runtime\launcher\XunqiTray.exe"
if not exist "%TRAY%" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_tray_launcher.ps1" -SkipDesktopExe
  if errorlevel 1 (
    echo Launcher build failed. Check scripts\install_tray_launcher.ps1
    pause
    exit /b 1
  )
)
start "" "%TRAY%"
exit /b 0
