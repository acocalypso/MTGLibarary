; Inno Setup script for MTGLibarary
; Requires Inno Setup 6+

#define AppName "MTGLibarary"
#define AppVersion "0.1.0"
#define AppPublisher "acocalypso"
#define AppURL "https://github.com/acocalypso/MTGLibarary"

; We package the PyInstaller onedir output (exe + _internal folder)
#define DistDir "..\\dist\\" + AppName
#define ExeName AppName + ".exe"

[Setup]
AppId={{D3A7A2C1-2BE6-4D7A-9E24-AB8BBE1E7CFB}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
DefaultDirName={autopf}\\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputBaseFilename={#AppName}-Setup
OutputDir=..\\dist
Compression=lzma
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop icon"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
; Copy everything from dist/MTGLibarary/* into the install directory
Source: "{#DistDir}\\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\\{#AppName}"; Filename: "{app}\\{#ExeName}"
Name: "{autodesktop}\\{#AppName}"; Filename: "{app}\\{#ExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\\{#ExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
