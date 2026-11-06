@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
set "QTWEBENGINE_CHROMIUM_FLAGS=%QTWEBENGINE_CHROMIUM_FLAGS% --disable-gpu"
set "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=%WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS% --disable-gpu"
call START-LIONXSEC.cmd
