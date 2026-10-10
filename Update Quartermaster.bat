@echo off
rem Double-click to get the latest version of Quartermaster. Stop Quartermaster first (close its window).
setlocal
title Update Quartermaster
cd /d "%~dp0"
echo Getting the latest version...
git pull
if errorlevel 1 goto failed
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --quiet -e ".[browser]"
  if errorlevel 1 goto failed
)
echo.
echo Up to date. Start Quartermaster again from the desktop icon.
pause
exit /b 0

:failed
echo.
echo The update did not work. The messages above say why. Send a photo of this window to the test team.
pause
exit /b 1
