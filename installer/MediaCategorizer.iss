#ifndef AppVersion
#define AppVersion "6.5.0"
#endif
#define AppKey "Software\MediaCategorizer"
#define UninstallKey "Software\Microsoft\Windows\CurrentVersion\Uninstall\MediaCategorizer_is1"
[Setup]
AppId=MediaCategorizer
AppName=Media Categorizer
AppVersion={#AppVersion}
AppPublisher=Media Categorizer
DefaultDirName={localappdata}\Programs\MediaCategorizer
DefaultGroupName=Media Categorizer
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
UsePreviousAppDir=yes
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\MediaCategorizer.exe
SetupIconFile=..\media_categorizer\assets\app.ico
OutputDir=..\dist
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=no
RestartApplications=no
AppMutex=MediaCategorizer.Running
#ifdef UpdateOnly
OutputBaseFilename=MediaCategorizer-Update-{#AppVersion}
DisableDirPage=yes
DisableWelcomePage=yes
#else
OutputBaseFilename=MediaCategorizer-Setup-{#AppVersion}
DisableDirPage=auto
#endif
[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"
[Files]
Source: "..\dist\MediaCategorizer.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\media_categorizer\assets\LUCIDE-LICENSE"; DestDir: "{app}\licenses"; Flags: ignoreversion
Source: "..\dist\ffmpeg\*"; DestDir: "{app}\ffmpeg"; Flags: ignoreversion
[Registry]
Root: HKCU; Subkey: "{#AppKey}"; ValueType: string; ValueName: "InstallDir"; ValueData: "{app}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "{#AppKey}"; ValueType: string; ValueName: "Version"; ValueData: "{#AppVersion}"
#ifndef UpdateOnly
[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; Flags: unchecked
[Icons]
Name: "{group}\Media Categorizer"; Filename: "{app}\MediaCategorizer.exe"
Name: "{autodesktop}\Media Categorizer"; Filename: "{app}\MediaCategorizer.exe"; Tasks: desktopicon
#endif
[Run]
Filename: "{app}\MediaCategorizer.exe"; Description: "Запустить Media Categorizer"; Flags: nowait postinstall skipifsilent; BeforeInstall: PrepareAppEnvironment
Filename: "{app}\MediaCategorizer.exe"; Flags: nowait; Check: RestartAfterUpdate; BeforeInstall: PrepareAppEnvironment
[Code]
var ExistingDir: String;
function SetEnvironmentVariable(lpName, lpValue: String): Boolean;
external 'SetEnvironmentVariableW@kernel32.dll stdcall';
procedure PrepareAppEnvironment();
begin
  { Also handles updates started by older clients that inherit _PYI_* variables. }
  if not SetEnvironmentVariable('PYINSTALLER_RESET_ENVIRONMENT', '1') then
    RaiseException('Не удалось подготовить окружение для запуска приложения.');
end;
function RestartAfterUpdate(): Boolean;
begin
  Result := WizardSilent and (ExpandConstant('{param:RESTARTAPP|0}') = '1');
end;
function InitializeSetup(): Boolean;
var ExistingVersion: String; OldVersion, NewVersion: Int64;
begin
  Result := False;
  RegQueryStringValue(HKCU, '{#AppKey}', 'InstallDir', ExistingDir);
#ifdef UpdateOnly
  if (ExistingDir = '') or not FileExists(ExistingDir + '\MediaCategorizer.exe') or
     not RegKeyExists(HKCU, '{#UninstallKey}') then begin
    SuppressibleMsgBox('Установленная программа не найдена. Сначала запустите MediaCategorizer-Setup.', mbError, MB_OK, IDOK);
    exit;
  end;
#endif
  if RegQueryStringValue(HKCU, '{#AppKey}', 'Version', ExistingVersion) then begin
    if not StrToVersion(ExistingVersion, OldVersion) then OldVersion := 0;
    StrToVersion('{#AppVersion}', NewVersion);
    if ComparePackedVersion(OldVersion, NewVersion) > 0 then begin
      SuppressibleMsgBox('Уже установлена более новая версия. Понижение версии отменено.', mbError, MB_OK, IDOK);
      exit;
    end;
  end;
  Result := True;
end;
#ifdef UpdateOnly
procedure InitializeWizard();
begin
  WizardForm.DirEdit.Text := ExistingDir;
  WizardForm.WelcomeLabel1.Caption := 'Обновление Media Categorizer';
end;
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if CompareText(ExpandConstant('{app}'), ExistingDir) <> 0 then
    Result := 'Обновление должно выполняться в папку установленной программы.';
end;
#endif
