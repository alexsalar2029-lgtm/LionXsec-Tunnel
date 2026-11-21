@echo off
rem Drop a downloaded Google Desktop client JSON onto this launcher, or double-click it.
if /i "%~x1"==".json" goto :client_file
call "%~dp0start_youtube_uploader.bat" --setup %*
exit /b %ERRORLEVEL%

:client_file
call "%~dp0start_youtube_uploader.bat" --setup --client-json "%~f1"
exit /b %ERRORLEVEL%
