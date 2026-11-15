@echo off
title Plasma Exchange
cd /d "%~dp0app"
set "PY="

rem --- find Python ---
where py >nul 2>nul && set "PY=py"
if not defined PY python -c "import sys" >nul 2>nul && set "PY=python"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"

rem --- first time only: install Python ---
if not defined PY (
  echo First-time setup - installing Python. This happens once and takes about a minute...
  winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
  if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
)
if not defined PY (
  echo.
  echo Python could not be installed automatically.
  echo Your browser will open the Python download page - install it, then double-click START HERE again.
  start https://www.python.org/downloads/
  pause
  exit /b
)

echo.
echo  Plasma Exchange is opening in your web browser...
echo  Keep this window open while you use it. Close it to stop.
echo.
"%PY%" market.py
pause
