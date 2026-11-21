@echo off
rem Install Telegram support into the exact same environment used by the GUI.
call "%~dp0start_youtube_uploader.bat" --install-telegram
exit /b %ERRORLEVEL%
