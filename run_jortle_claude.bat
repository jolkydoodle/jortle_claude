@echo off
REM  Runs jortle_claude straight from the source in this folder, without building
REM  an .exe first. Handy for trying a change immediately — for the real
REM  installed app (with a Desktop shortcut and no console window), use
REM  build_windows.bat and then create_desktop_shortcut.bat instead.
REM
REM  Needs Python 3.10+ with the packages in requirements.txt:
REM      python -m pip install -r requirements.txt

setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found on your PATH.
    echo Install Python 3.10 or newer from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH" during setup.
    pause
    exit /b 1
)

if not exist "jortle_claude.py" (
    echo Couldn't find jortle_claude.py next to this script.
    echo Run this from inside the jortle_claude folder.
    pause
    exit /b 1
)

echo Starting jortle_claude...
python jortle_claude.py
if errorlevel 1 (
    echo.
    echo jortle_claude exited with an error. The message above says why.
    pause
)
endlocal
