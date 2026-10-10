@echo off
rem Double-click to start Quartermaster. The first time it installs what it needs (a few minutes,
rem once) and puts a Quartermaster icon on the desktop. Keep the window open while you use it;
rem close it to stop Quartermaster.
setlocal
title Quartermaster
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto start

where python >nul 2>nul
if errorlevel 1 (
  echo Python is not installed on this computer.
  echo Install Python 3.11 or newer from python.org. In the installer, tick "Add python.exe to PATH".
  echo Then double-click "Start Quartermaster" again.
  start "" "https://www.python.org/downloads/"
  pause
  exit /b 1
)

echo First start: installing Quartermaster. This takes a few minutes, only this once.
python -m venv .venv
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --quiet -e ".[browser]"
if errorlevel 1 goto failed
echo Downloading the test browser...
".venv\Scripts\python.exe" -m playwright install chromium
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m quartermaster.cli shortcut

:start
echo.
echo Starting Quartermaster. Your browser opens by itself.
echo Keep this window open while you use Quartermaster. Close it to stop.
echo.
".venv\Scripts\python.exe" -m quartermaster.cli serve
if errorlevel 1 pause
exit /b 0

:failed
echo.
echo Installing did not work. The messages above say why (often: no internet, or a proxy).
echo Send a photo of this window to the test team.
pause
exit /b 1
