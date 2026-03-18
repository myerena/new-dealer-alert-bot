@echo off
REM ============================================================
REM  Dealer Alert Bot — Daily Pipeline Run
REM  Schedule this with Windows Task Scheduler to run daily.
REM ============================================================

REM Change to the project directory
cd /d "%~dp0.."

REM Activate virtual environment if it exists
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
)

REM Run the full pipeline
echo [%date% %time%] Starting dealer alert pipeline...
dealer-alert run-all --crawl-limit 200 --email-hours 24

REM Log completion
echo [%date% %time%] Pipeline complete.
