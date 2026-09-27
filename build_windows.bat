@echo off
REM ============================================================
REM  jortle_claude - Windows build script
REM
REM  Run this on a Windows machine (with Python 3.11 or newer installed)
REM  from inside the jortle_claude folder. It will:
REM    1. Install dependencies
REM    2. Bundle the app with PyInstaller into dist\jortle_claude\
REM    3. Tell you how to create a desktop shortcut
REM
REM  Uses --onedir (a folder of files) rather than --onefile. A
REM  --onefile exe has to silently unpack itself to a temp folder
REM  EVERY time you launch it, which is a real, noticeable delay for
REM  an app this size (PySide6 is large) - --onedir starts almost
REM  immediately since there's nothing to unpack.
REM
REM  Dependencies (requirements.txt): PySide6, PyInstaller, and the two
REM  encryption libraries - sqlcipher3 (the encrypted journal database)
REM  and pyrage (age, for keys and encrypted backups). Both install from
REM  ready-made Windows wheels; nothing needs compiling. There is no
REM  inference backend, GPU/CPU build choice or model download.
REM ============================================================

echo Installing dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo.
echo Building jortle_claude with PyInstaller (this can take a minute)...
pyinstaller --noconfirm --windowed ^
    --name "jortle_claude" ^
    --icon "resources\icon.ico" ^
    --add-data "resources;resources" ^
    --add-data "docs;docs" ^
    --hidden-import sqlcipher3 ^
    --hidden-import sqlcipher3.dbapi2 ^
    --hidden-import pyrage ^
    --hidden-import pyrage.passphrase ^
    --hidden-import pyrage.x25519 ^
    jortle_claude.py

echo.
echo ============================================================
echo  Build complete.
echo  Your app folder is at: dist\jortle_claude\
echo  The program itself is: dist\jortle_claude\jortle_claude.exe
echo.
echo  Keep the whole dist\jortle_claude\ folder together - the .exe
echo  needs the files alongside it. Don't move just the .exe on its
echo  own.
echo.
echo  For a desktop shortcut, run:
echo    create_desktop_shortcut.bat
echo ============================================================
pause
