; Script do Inno Setup — empacota o JusControlAgente.exe (já compilado
; pelo PyInstaller, ver agente_local.spec) num instalador de verdade
; (assistente "Avançar/Avançar/Concluir", atalho no Menu Iniciar,
; desinstalador que aparece em "Adicionar ou remover programas").
;
; Rodado automaticamente pelo GitHub Actions (ver
; .github/workflows/build-agente-local.yml) com o compilador ISCC.exe,
; que já vem instalado nas imagens padrão do runner "windows-latest".
;
; Pra compilar manualmente (Windows, com Inno Setup instalado):
;   iscc build\instalador.iss
;
; #AppVersion é passado por linha de comando no workflow
; (/DAppVersion=1.2.3, vindo da tag do Git) — sem isso, usa "0.0.0-dev".
#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif

[Setup]
AppId={{8070DE43-0791-4238-A88E-994B13CCC498}
AppName=Agente Local JusControl
AppVersion={#AppVersion}
AppPublisher=JusControl
DefaultDirName={autopf}\JusControlAgente
DefaultGroupName=JusControl Agente Local
DisableProgramGroupPage=yes
; Instala só pro usuário atual, sem pedir permissão de administrador —
; o agente não precisa mexer em nada do sistema, só na conta do próprio
; advogado (registro de início automático é em HKEY_CURRENT_USER, ver
; autostart_windows.py).
PrivilegesRequired=lowest
OutputDir=dist_instalador
OutputBaseFilename=JusControlAgente-Setup
Compression=lzma2
SolidCompression=yes
SetupIconFile=icone.ico
UninstallDisplayIcon={app}\JusControlAgente.exe

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar um atalho na Área de Trabalho"; GroupDescription: "Atalhos adicionais:"; Flags: unchecked

[Files]
Source: "dist\JusControlAgente.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Agente Local JusControl"; Filename: "{app}\JusControlAgente.exe"
Name: "{group}\Desinstalar Agente Local JusControl"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Agente Local JusControl"; Filename: "{app}\JusControlAgente.exe"; Tasks: desktopicon

[Run]
; Abre o agente (e a janela de configuração inicial, se ainda não
; pareado) assim que o instalador termina — assim o advogado já cola o
; token na hora, sem precisar procurar o programa depois.
Filename: "{app}\JusControlAgente.exe"; Description: "Abrir o Agente Local agora"; Flags: nowait postinstall skipifsilent
