@echo off
cd /d "%~dp0"
if exist "%~dp0SeratoYouTube.exe" (
    start "" "%~dp0SeratoYouTube.exe"
) else (
    start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0app.py"
)
