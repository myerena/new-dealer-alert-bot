@echo off
REM ============================================================
REM  Creates a Windows Task Scheduler task for the Dealer Alert Bot.
REM  Run this script once AS ADMINISTRATOR to set up daily runs.
REM
REM  Default: runs every day at 3:00 AM.
REM  Edit the /ST time below to change the schedule.
REM ============================================================

set TASK_NAME=DealerAlertBot
set SCRIPT_PATH=%~dp0run_daily.bat

echo Creating scheduled task: %TASK_NAME%
echo Script: %SCRIPT_PATH%
echo Schedule: Daily at 3:00 AM
echo.

schtasks /create ^
    /tn "%TASK_NAME%" ^
    /tr "\"%SCRIPT_PATH%\"" ^
    /sc daily ^
    /st 03:00 ^
    /rl HIGHEST ^
    /f

if %errorlevel% equ 0 (
    echo.
    echo Task created successfully!
    echo.
    echo To verify:  schtasks /query /tn "%TASK_NAME%" /v
    echo To run now: schtasks /run /tn "%TASK_NAME%"
    echo To delete:  schtasks /delete /tn "%TASK_NAME%" /f
) else (
    echo.
    echo Failed to create task. Make sure you're running as Administrator.
)

pause
