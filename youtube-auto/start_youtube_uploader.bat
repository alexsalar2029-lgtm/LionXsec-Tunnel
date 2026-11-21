@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0" || exit /b 1
if /i "%~1"=="--help" goto :help
if /i "%~1"=="/?" goto :help

set "RUNTIME_ROOT=%LOCALAPPDATA%\YouTubeUploader"
if not defined LOCALAPPDATA set "RUNTIME_ROOT=%~dp0.runtime"
set "VENV=%RUNTIME_ROOT%\venv"
set "PYTHON=%VENV%\Scripts\python.exe"
if not exist "%PYTHON%" (
    call :create_venv
    if errorlevel 1 goto :failed
)

rem Also upgrades v4 installations which only had Selenium and the GUI.
"%PYTHON%" -c "import tkinter, customtkinter, PIL, telethon, socks, googleapiclient.discovery, google_auth_oauthlib.flow" >nul 2>&1
if errorlevel 1 (
    call :install requirements-uploader.txt
    if errorlevel 1 goto :failed
)

if /i "%~1"=="--install-telegram" goto :install_telegram
if /i "%~1"=="--install-vision" goto :install_vision
if /i "%~1"=="--install-all" goto :install_vision
if /i "%~1"=="--vision" goto :vision_gui
if /i "%~1"=="--all" goto :vision_gui
if /i "%~1"=="--telegram" goto :gui
if "%~1"=="" goto :gui

"%PYTHON%" "%~dp0yt_uploader.py" %*
if errorlevel 1 goto :failed
exit /b 0

:gui
"%PYTHON%" "%~dp0yt_uploader.py" --gui
if errorlevel 1 goto :failed
exit /b 0

:vision_gui
call :install requirements-uploader-vision.txt
if errorlevel 1 goto :failed
goto :gui

:install_telegram
call :install requirements-uploader-telegram.txt
if errorlevel 1 goto :failed
echo Telegram support is ready.
exit /b 0

:install_vision
call :install requirements-uploader-vision.txt
if errorlevel 1 goto :failed
echo Optional components are ready.
exit /b 0

:install
"%PYTHON%" -m pip install --disable-pip-version-check --no-input -r "%~dp0%~1"
exit /b %ERRORLEVEL%

:create_venv
for %%V in (3.12 3.11 3.10 3.13 3.14) do (
    py -%%V -c "import tkinter" >nul 2>&1
    if not errorlevel 1 (
        py -%%V -m venv "%VENV%"
        if errorlevel 1 exit /b 1
        exit /b 0
    )
)
python -c "import tkinter, sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1
if not errorlevel 1 (
    python -m venv "%VENV%"
    if errorlevel 1 exit /b 1
    exit /b 0
)
where winget >nul 2>&1
if errorlevel 1 goto :no_python
winget install --id Python.Python.3.12 --exact --scope user --silent --accept-package-agreements --accept-source-agreements
if errorlevel 1 goto :no_python
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" -m venv "%VENV%"
    if errorlevel 1 exit /b 1
    exit /b 0
)
:no_python
echo Install Python 3.10 or newer with Tcl/Tk from python.org, then run this file again.
exit /b 1

:help
echo start_youtube_uploader.bat              Open the desktop interface
echo run_gui.bat                            Open the desktop interface
echo login_youtube.bat                      Connect YouTube once
echo install_telegram.bat                   Install Telegram support in the app environment
echo auto_run.bat                           Open and start the daily batch
echo start_youtube_uploader.bat --dry-run   Preview metadata
echo start_youtube_uploader.bat --vision    Add frame analysis and Windows OCR
echo start_youtube_uploader.bat --install-all   Install optional components and exit
exit /b 0

:failed
echo YouTube Uploader could not complete this operation. See the message above.
if not defined CI pause
exit /b 1
