@echo off
REM Double-click this file to open the Pennybot dashboard in your browser.
cd /d "%~dp0"
start "" /min cmd /c "timeout /t 6 >nul & start http://localhost:8501"
".venv\Scripts\python.exe" -m streamlit run dashboard.py
