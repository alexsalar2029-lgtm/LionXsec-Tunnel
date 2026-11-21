@echo off
rem Reuse the saved channel grant, or connect once using a Google Desktop OAuth client.
call "%~dp0start_youtube_uploader.bat" --setup %*
exit /b %ERRORLEVEL%
