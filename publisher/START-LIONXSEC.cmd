@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
title LIONXSEC Publisher
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import sys; sys.exit(not ((3,10) <= sys.version_info[:2] < (3,14) and sys.maxsize > 2**32))" >nul 2>&1
  if not errorlevel 1 (
    ".venv\Scripts\python.exe" launcher.py
    goto finished
  )
)
for %%V in (3.13 3.12 3.11 3.10) do (
  py -%%V -c "import sys; sys.exit(not ((3,10) <= sys.version_info[:2] < (3,14) and sys.maxsize > 2**32))" >nul 2>&1
  if not errorlevel 1 (
    py -%%V launcher.py
    goto finished
  )
)
python -c "import sys; sys.exit(not ((3,10) <= sys.version_info[:2] < (3,14) and sys.maxsize > 2**32))" >nul 2>&1
if errorlevel 1 goto no_python
python launcher.py
:finished
if errorlevel 1 goto failed
exit /b 0
:no_python
echo Python 3.10-3.13 64-bit was not found. Install Python and enable Add Python to PATH.
echo Python 3.10-3.13 64-bit was not found.>>startup-error.log
echo Download: https://www.python.org/downloads/windows/
:failed
echo Setup or startup failed. Read startup-error.log and WINDOWS-SERVER-2016_FA.md.
pause
exit /b 1
