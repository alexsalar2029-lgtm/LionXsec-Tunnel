@echo off
REM Open the app and immediately auto-upload the daily batch (videos_per_day).
REM This is what the Windows daily task runs.
call "%~dp0start_youtube_uploader.bat" --autorun %*
exit /b %ERRORLEVEL%
