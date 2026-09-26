; Inno Setup script for jortle_claude — OPTIONAL. If you're happy with
; create_desktop_shortcut.bat, you don't need this at all.
;
; Compile this with Inno Setup (https://jrsoftware.org/isdl.php) AFTER
; running build_windows.bat, so dist\jortle_claude\jortle_claude.exe exists.
;
; Produces jortle_claude-Setup.exe, which installs the app and offers a
; "Create a desktop shortcut" checkbox (checked by default) plus a
; Start Menu entry and a normal uninstaller.

#define MyAppName "jortle_claude"
#define MyAppVersion "1.0.0"
#define MyAppExeName "jortle_claude.exe"

[Setup]
; A new AppId: jortle_claude is a different application from Jortle, so
; this installer must not upgrade, replace or uninstall a Jortle install.
; An old Jortle installed with the previous AppId stays until you remove it
; yourself (Settings → Apps). Uninstalling never deletes %APPDATA% data.
AppId={{3E4B52A6-E939-439A-A650-29C35C018713}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputBaseFilename=jortle_claude-Setup
Compression=lzma2
SolidCompression=yes
SetupIconFile=resources\icon.ico
DisableProgramGroupPage=yes
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: checkedonce

[Files]
Source: "dist\jortle_claude\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
