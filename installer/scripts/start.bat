@echo off
@REM cd /d "%~dp0..\."

set PATH=%CD%\python;%CD%\ffmpeg;%PATH%
set PYTHONHOME=%CD%\python
@REM set PYTHONDONTWRITEBYTECODE=1

.\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000

pause
