@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 unit_forge.py --self-test
) else (
    python unit_forge.py --self-test
)
pause
endlocal
