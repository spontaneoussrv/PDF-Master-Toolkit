; =============================================================================
;  PDF Master Toolkit - Inno Setup script
;
;  Produces:  dist_installer\PDF_Master_Toolkit_Setup.exe
;
;  Wizard pages the user sees, in order:
;     1. Welcome
;     2. Licence agreement
;     3. Install for everyone / just me      (elevation choice)
;     4. Destination folder                  (with a free-space check)
;     5. Components                          (OCR engine, extras)
;     6. Start Menu folder
;     7. Additional tasks                    (desktop, taskbar, startup,
;                                             file association, context menu)
;     8. Ready to install (summary)
;     9. Installing
;    10. Finished  (with "launch now")
;
;  Product details come from branding.iss, regenerated on every build from
;  app\branding.py. Never edit branding.iss by hand.
;
;  Requires Inno Setup 6:  https://jrsoftware.org/isdl.php
; =============================================================================

#include "branding.iss"

#define SourceDir "..\dist\" + AppName
#define AssetsDir "..\assets"
#define VendorTess "..\dist\" + AppName + "\_internal\vendor\tesseract"

[Setup]
AppId={{{#AppId}}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
VersionInfoVersion={#AppVersion}.{#AppBuild}
VersionInfoCompany={#Publisher}
VersionInfoDescription={#AppName} Setup
VersionInfoCopyright={#Copyright}
AppPublisher={#Publisher}
AppPublisherURL={#AppURL}
AppSupportURL={#SupportURL}
AppUpdatesURL={#UpdatesURL}
AppContact={#SupportEmail}
AppCopyright={#Copyright}

; --- elevation --------------------------------------------------------------
; "lowest" + "dialog" makes Setup ASK on launch: install for all users (which
; elevates via UAC) or just for me (no admin password needed at all).
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

; --- pages the user gets to answer -----------------------------------------
DefaultDirName={autopf}\{#PublisherShort}\{#AppName}
DefaultGroupName={#AppName}
DisableDirPage=no
DisableProgramGroupPage=no
DisableReadyPage=no
DisableReadyMemo=no
DisableWelcomePage=no
AllowNoIcons=yes
AlwaysShowDirOnReadyPage=yes
AlwaysShowGroupOnReadyPage=yes
DirExistsWarning=auto
UsePreviousAppDir=yes
UsePreviousGroup=yes
UsePreviousTasks=yes
UsePreviousSetupType=yes

; --- output -----------------------------------------------------------------
OutputDir=..\dist_installer
OutputBaseFilename={#OutputName}
SetupIconFile={#AssetsDir}\{#IconFile}
UninstallDisplayIcon={app}\{#ExeName}
UninstallDisplayName={#AppName} {#AppVersion}

; --- compression ------------------------------------------------------------
Compression=lzma2/max
SolidCompression=yes
LZMAUseSeparateProcess=yes
LZMANumBlockThreads=4

; --- requirements -----------------------------------------------------------
MinVersion=10.0.17763
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; --- appearance -------------------------------------------------------------
WizardStyle=modern
WizardSizePercent=115
WizardImageFile=wizard.bmp
WizardSmallImageFile=wizard_small.bmp
WizardImageStretch=yes
ShowLanguageDialog=no
SetupLogging=yes

; --- behaviour --------------------------------------------------------------
CloseApplications=yes
CloseApplicationsFilter=*.exe
RestartApplications=no
ChangesAssociations=yes

LicenseFile=LICENSE.txt


[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"


; ===========================================================================
;  COMPONENTS  - what to install
; ===========================================================================
[Types]
Name: "full";    Description: "Full installation (recommended)"
Name: "compact"; Description: "Compact installation - no OCR engine"
Name: "custom";  Description: "Custom installation"; Flags: iscustom

[Components]
Name: "core"; Description: "{#AppName} (required)"; \
    Types: full compact custom; Flags: fixed
Name: "ocr";  Description: "Offline OCR engine and language packs"; \
    Types: full custom; ExtraDiskSpaceRequired: 125829120


; ===========================================================================
;  TASKS  - the "Additional tasks" wizard page
; ===========================================================================
[Tasks]
Name: "desktopicon";   Description: "Create a &desktop shortcut"; \
    GroupDescription: "Shortcuts:"
Name: "quicklaunch";   Description: "Pin a &taskbar shortcut"; \
    GroupDescription: "Shortcuts:"; Flags: unchecked
Name: "startupicon";   Description: "Start {#AppShortName} when I sign in to Windows"; \
    GroupDescription: "Shortcuts:"; Flags: unchecked

Name: "assocpdf";      Description: "Make {#AppShortName} the default app for &PDF files"; \
    GroupDescription: "File associations:"; Flags: unchecked
Name: "contextmenu";   Description: "Add ""Open with {#AppShortName}"" to the &right-click menu"; \
    GroupDescription: "File associations:"


[Files]
; Everything except the optional OCR engine.
Source: "{#SourceDir}\*"; DestDir: "{app}"; Components: core; \
    Excludes: "\_internal\vendor\tesseract\*"; \
    Flags: ignoreversion recursesubdirs createallsubdirs

; The OCR engine, only when the user ticked it AND the build staged it.
Source: "{#VendorTess}\*"; DestDir: "{app}\_internal\vendor\tesseract"; \
    Components: ocr; \
    Flags: ignoreversion recursesubdirs createallsubdirs skipifsourcedoesntexist

Source: "LICENSE.txt"; DestDir: "{app}"; Components: core; Flags: ignoreversion


[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#ExeName}"; \
    Comment: "{#AppName} - all-in-one PDF productivity suite"; \
    WorkingDir: "{app}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"

Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#ExeName}"; \
    Tasks: desktopicon; WorkingDir: "{app}"; \
    Comment: "{#AppName} - all-in-one PDF productivity suite"

Name: "{userappdata}\Microsoft\Internet Explorer\Quick Launch\{#AppName}"; \
    Filename: "{app}\{#ExeName}"; Tasks: quicklaunch; WorkingDir: "{app}"

Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#ExeName}"; \
    Tasks: startupicon; WorkingDir: "{app}"


[Registry]
Root: HKA; Subkey: "Software\{#PublisherShort}\{#AppName}"; \
    ValueType: string; ValueName: "InstallPath"; ValueData: "{app}"; \
    Flags: uninsdeletekey
Root: HKA; Subkey: "Software\{#PublisherShort}\{#AppName}"; \
    ValueType: string; ValueName: "Version"; ValueData: "{#AppVersion}"

; --- always registered so the app appears under "Open with" -----------------
Root: HKA; Subkey: "Software\Classes\Applications\{#ExeName}"; \
    ValueType: string; ValueName: "FriendlyAppName"; ValueData: "{#AppName}"; \
    Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Applications\{#ExeName}\DefaultIcon"; \
    ValueType: string; ValueData: "{app}\{#ExeName},0"
Root: HKA; Subkey: "Software\Classes\Applications\{#ExeName}\shell\open\command"; \
    ValueType: string; ValueData: """{app}\{#ExeName}"" ""%1"""
Root: HKA; Subkey: "Software\Classes\Applications\{#ExeName}\SupportedTypes"; \
    ValueType: string; ValueName: ".pdf"; ValueData: ""

; --- optional: become the default PDF handler ------------------------------
Root: HKA; Subkey: "Software\Classes\.pdf\OpenWithProgids"; \
    ValueType: string; ValueName: "{#AppId}.pdf"; ValueData: ""; \
    Flags: uninsdeletevalue; Tasks: assocpdf
Root: HKA; Subkey: "Software\Classes\{#AppId}.pdf"; \
    ValueType: string; ValueData: "PDF Document"; \
    Flags: uninsdeletekey; Tasks: assocpdf
Root: HKA; Subkey: "Software\Classes\{#AppId}.pdf\DefaultIcon"; \
    ValueType: string; ValueData: "{app}\{#ExeName},0"; Tasks: assocpdf
Root: HKA; Subkey: "Software\Classes\{#AppId}.pdf\shell\open\command"; \
    ValueType: string; ValueData: """{app}\{#ExeName}"" ""%1"""; Tasks: assocpdf

; --- right-click menu -------------------------------------------------------
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.pdf\shell\{#AppId}"; \
    ValueType: string; ValueName: ""; ValueData: "Open with {#AppShortName}"; \
    Flags: uninsdeletekey; Tasks: contextmenu
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.pdf\shell\{#AppId}"; \
    ValueType: string; ValueName: "Icon"; ValueData: "{app}\{#ExeName},0"; \
    Tasks: contextmenu
Root: HKA; Subkey: "Software\Classes\SystemFileAssociations\.pdf\shell\{#AppId}\command"; \
    ValueType: string; ValueData: """{app}\{#ExeName}"" ""%1"""; Tasks: contextmenu


[Run]
Filename: "{app}\{#ExeName}"; \
    Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; \
    WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent

Filename: "{#SupportURL}"; Description: "Open the getting-started guide"; \
    Flags: postinstall shellexec skipifsilent unchecked


[UninstallDelete]
Type: filesandordirs; Name: "{app}\_internal\__pycache__"
Type: dirifempty;     Name: "{app}"


[Messages]
BeveledLabel={#PublisherShort}  ·  {#AppName} {#AppVersion}
SetupAppTitle={#AppName} Setup
SetupWindowTitle={#AppName} {#AppVersion} Setup
WelcomeLabel1=Welcome to [name]
WelcomeLabel2=This will install [name/ver] on your computer.%n%nAll PDF processing happens locally on this machine — your documents are never uploaded.%n%nIt is recommended that you close all other applications before continuing.
FinishedHeadingLabel=[name] is ready
FinishedLabel=Setup has installed [name] on your computer.%n%nYour PDF files are processed locally on your computer unless a feature explicitly states otherwise.
SelectDirDesc=Where should [name] be installed?
SelectDirLabel3=Setup will install [name] into the following folder. To continue, click Next. To choose a different folder, click Browse.
SelectComponentsDesc=Which parts of [name] should be installed?
SelectComponentsLabel2=Select the components you want to install; clear the components you do not want. Click Next when you are ready to continue.
SelectTasksDesc=Which additional shortcuts and options would you like?
SelectTasksLabel2=Select the additional tasks you would like Setup to perform, then click Next.


[CustomMessages]
CreateDesktopIcon=Create a &desktop shortcut
LaunchProgram=Launch %1 now


[Code]
{ ------------------------------------------------------------------------- }
{  Shell notification so new file associations take effect immediately       }
{ ------------------------------------------------------------------------- }
const
  SHCNE_ASSOCCHANGED = $08000000;
  SHCNF_IDLIST = $0000;

procedure SHChangeNotify(wEventId, uFlags: Integer; dwItem1, dwItem2: Integer);
  external 'SHChangeNotify@shell32.dll stdcall';

var
  OcrAvailable: Boolean;

function GetUserDataDir(): String;
begin
  Result := ExpandConstant('{localappdata}\{#PublisherShort}\{#AppName}');
end;

{ ------------------------------------------------------------------------- }
{  Startup: note whether this build actually bundled the OCR engine, and     }
{  tell the user if an older version is present.                             }
{ ------------------------------------------------------------------------- }
function InitializeSetup(): Boolean;
var
  Installed: String;
begin
  Result := True;

  OcrAvailable := FileExists(ExpandConstant('{src}\..\dist\{#AppName}\_internal\vendor\tesseract\tesseract.exe'));

  if RegQueryStringValue(HKEY_CURRENT_USER,
       'Software\{#PublisherShort}\{#AppName}', 'Version', Installed) or
     RegQueryStringValue(HKEY_LOCAL_MACHINE,
       'Software\{#PublisherShort}\{#AppName}', 'Version', Installed) then
  begin
    if Installed <> '{#AppVersion}' then
      MsgBox('{#AppName} ' + Installed + ' is already installed and will be '
           + 'upgraded to version {#AppVersion}.' + #13#10 + #13#10
           + 'Your settings, history and saved signatures are kept.',
           mbInformation, MB_OK)
    else
      if MsgBox('{#AppName} {#AppVersion} is already installed.' + #13#10 + #13#10
              + 'Do you want to repair or reinstall it?',
              mbConfirmation, MB_YESNO) = IDNO then
        Result := False;
  end;
end;

{ ------------------------------------------------------------------------- }
{  Warn if the user cleared the OCR component                               }
{ ------------------------------------------------------------------------- }
function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = wpSelectComponents then
  begin
    if not IsComponentSelected('ocr') then
      MsgBox('You have chosen not to install the OCR engine.' + #13#10 + #13#10
           + 'The OCR PDF screen will report that recognition is unavailable. '
           + 'Every other tool works normally, and you can re-run this installer '
           + 'later to add OCR.', mbInformation, MB_OK);
  end;
end;

{ ------------------------------------------------------------------------- }
{  Show the chosen options on the "Ready to install" summary                 }
{ ------------------------------------------------------------------------- }
function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo,
  MemoTypeInfo, MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
var
  S: String;
begin
  S := '';
  if MemoDirInfo <> '' then S := S + MemoDirInfo + NewLine + NewLine;
  if MemoTypeInfo <> '' then S := S + MemoTypeInfo + NewLine + NewLine;
  if MemoComponentsInfo <> '' then S := S + MemoComponentsInfo + NewLine + NewLine;
  if MemoGroupInfo <> '' then S := S + MemoGroupInfo + NewLine + NewLine;
  if MemoTasksInfo <> '' then S := S + MemoTasksInfo + NewLine + NewLine;

  S := S + 'Privacy:' + NewLine + Space +
       'All PDF processing runs locally on this computer.' + NewLine;
  Result := S;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, 0, 0);
end;

{ ------------------------------------------------------------------------- }
{  Uninstall: offer to keep or remove personal data                          }
{ ------------------------------------------------------------------------- }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := GetUserDataDir();
    if DirExists(DataDir) then
    begin
      if MsgBox('Also remove your {#AppShortName} settings, history and saved '
              + 'signatures?' + #13#10 + #13#10
              + 'Your PDF files are never touched — this only clears the '
              + 'application''s own data in:' + #13#10 + DataDir,
              mbConfirmation, MB_YESNO) = IDYES then
        DelTree(DataDir, True, True, True);
    end;
    SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, 0, 0);
  end;
end;
