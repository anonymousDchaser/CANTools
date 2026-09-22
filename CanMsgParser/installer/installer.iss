; installer.iss — CanMsgParser 的 Windows 安装包脚本（Inno Setup 6）
;
; 前置条件：已执行 python build.py 生成 dist\CanMsgParser\（自包含产物）。
; 编译命令（项目根目录）：
;     "C:\Users\<你>\AppData\Local\Programs\Inno Setup 6\ISCC.exe" installer\installer.iss
; 产物：
;     installer\output\CanMsgParser_Setup_1.2.1.exe
;
; 设计要点：
;   - dist\CanMsgParser 已是自包含产物（Python 运行时 / PyQt5 / VC 运行库全在内），
;     安装包只负责「搬运 + 快捷方式 + 卸载入口 + 压缩」，不下载任何外部依赖。
;   - PrivilegesRequired=lowest + DefaultDirName={localappdata}：
;     整机无需管理员权限即可安装。
;     这同时规避了 realtime_message_widget._process_dir() 在打包后回落指向
;     {app}\_internal 导致「默认录制目录不可写」的问题（受限目录才会踩）。

#define MyAppName "CanMsgParser"
#define MyAppVersion "1.2.1"
#define MyAppPublisher "lzxDChaser"
#define MyAppExeName "CanMsgParser.exe"
#define MySourceDir "..\dist\CanMsgParser"

[Setup]
; AppId 是卸载/升级识别的唯一标识，一旦发布不要再改，否则会变成「装了两份」。
AppId={{4F8A3C21-9D7E-4B56-A1C8-3E5F90B2D647}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\{#MyAppName}
DefaultGroupName={#MyAppName}
; 不显示「程序组」选择页（开始菜单固定放在 CanMsgParser 组下）
DisableProgramGroupPage=yes
; 显式保留目录选择页，允许用户自行改安装路径
DisableDirPage=no
AllowNoIcons=yes
; 用户级安装，全程不需要管理员权限
PrivilegesRequired=lowest
OutputDir=output
OutputBaseFilename=CanMsgParser_Setup_{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; 产物是 x64 的 Python 3.14 构建，明确声明架构限制
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#MyAppName} {#MyAppVersion}
UninstallDisplayIcon={app}\{#MyAppExeName}
MinVersion=10.0

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; 整目录搬运，含 _internal 下的全部运行时依赖
Source: "{#MySourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

; 说明：卸载时**有意不删除**用户配置 %USERPROFILE%\.canmsgparser_config.json
; 与用户自己保存的分组 JSON，避免误删数据。如需一并清理，可另加 [UninstallDelete]。
