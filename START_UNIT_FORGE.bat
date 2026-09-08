@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 unit_forge.py
) else (
    python unit_forge.py
)
if errorlevel 1 pause
endlocal
