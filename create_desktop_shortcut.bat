@echo off
REM Drops a shortcut to jortle_claude.exe on your Desktop. Run this AFTER
REM build_windows.bat has produced dist\jortle_claude\jortle_claude.exe.
REM
REM Re-run this any time you rebuild in place (same jortle_claude folder) —
REM it's harmless to run again and will just recreate the same shortcut.

set SCRIPT_DIR=%~dp0
set EXE_PATH=%SCRIPT_DIR%dist\jortle_claude\jortle_claude.exe
set ICON_PATH=%SCRIPT_DIR%resources\icon.ico
set WORK_DIR=%SCRIPT_DIR%dist\jortle_claude

if not exist "%EXE_PATH%" (
    echo Couldn't find %EXE_PATH%
    echo Run build_windows.bat first.
    pause
    exit /b 1
)

REM Desktop is routed through OneDrive on this machine (OneDrive syncs the
REM Desktop folder to %USERPROFILE%\OneDrive\Desktop instead of the plain
REM %USERPROFILE%\Desktop) — the shortcut has to land there or it won't
REM show up on the actual visible desktop.
powershell -NoProfile -Command ^
  "$s=(New-Object -COM WScript.Shell).CreateShortcut(\"$env:USERPROFILE\OneDrive\Desktop\jortle_claude.lnk\"); $s.TargetPath='%EXE_PATH%'; $s.IconLocation='%ICON_PATH%'; $s.WorkingDirectory='%WORK_DIR%'; $s.Save()"

REM The app used to build as DailyJournal.exe, so a shortcut from before
REM that rename would now point at a file that no longer exists. Remove it
REM rather than leaving a dead icon on the Desktop next to the new one.
REM
REM A "Jortle.lnk" from the builds before the jortle_claude rename is
REM deliberately NOT removed: a separately developed application is also
REM called Jortle, and its shortcut must never be deleted by this script.
REM Delete the old Jortle shortcut yourself if it pointed at this project's
REM dist\Jortle folder.
powershell -NoProfile -Command ^
  "$old=\"$env:USERPROFILE\OneDrive\Desktop\Daily Journal.lnk\"; if (Test-Path $old) { Remove-Item $old }"

echo Desktop shortcut created: "jortle_claude" on your Desktop.
pause
