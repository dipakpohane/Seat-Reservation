@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Python environment is missing. Run setup_local.py first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" book_ticket.py
pause