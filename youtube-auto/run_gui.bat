@echo off
REM Backward-compatible shortcut. The lightweight launcher handles Python and
REM installs only the dependencies required by the YouTube uploader.
call "%~dp0start_youtube_uploader.bat" --gui %*
exit /b %ERRORLEVEL%
