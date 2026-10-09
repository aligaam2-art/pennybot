@echo off
REM Double-click to run the screener in a terminal window (no browser).
cd /d "%~dp0"
".venv\Scripts\python.exe" -m pennybot
pause
