; Kiwi VPN installer hooks. Setup runs as administrator once, so the person never touches a service, a command line or a separate WireGuard install.
;
; Smart App Control: the setup never starts a program from an EXISTING install. Kiwi 0.x was never code signed, and Smart App
; Control blocks its kiwivpn-service.exe and uninstall.exe, which used to stop an upgrade or uninstall dead. Services are
; stopped and removed with Windows' own sc.exe instead (exactly what `kiwivpn-service.exe uninstall` does: both services and the
; tunnels folder), and windows/installer.nsi skips running a 0.x uninstaller and replaces that install in place.
; (The uninstaller itself is signed by Tauri's own !uninstfinalize via signCommand; ci/sign.ps1 handles its .tmp name.)

!macro KIWI_REMOVE_SERVICE
  ; the tunnel service first (its kill switch / routes go with it), then Kiwi's own service. Other WireGuard tunnels are never touched.
  nsExec::ExecToLog '"$SYSDIR\sc.exe" stop "WireGuardTunnel$$kiwi"'
  Pop $0
  nsExec::ExecToLog '"$SYSDIR\sc.exe" stop KiwiVPN'
  Pop $0
  Sleep 3000
  nsExec::ExecToLog '"$SYSDIR\sc.exe" delete "WireGuardTunnel$$kiwi"'
  Pop $0
  nsExec::ExecToLog '"$SYSDIR\sc.exe" delete KiwiVPN'
  Pop $0
  ; a service that ignored stop still holds its files: end it (its WFP filters are dynamic and vanish with the process)
  nsExec::ExecToLog '"$SYSDIR\taskkill.exe" /F /IM kiwivpn-service.exe'
  Pop $0
  Sleep 2000 ; Windows releases the service's files just after it stops
  RMDir /r "$INSTDIR\tunnels"
!macroend

; Upgrading: the running service holds its own files, so it goes first. The tunnel drops for the moment of the upgrade.
; Then the old programs are deleted (never run), so the new ones are created fresh with Program Files' own permissions rather
; than inheriting whatever an old file had. Settings live elsewhere (%LOCALAPPDATA%\live.pricechat.kiwivpn) and are kept.
!macro NSIS_HOOK_PREINSTALL
  !insertmacro KIWI_REMOVE_SERVICE
  Delete "$INSTDIR\KiwiVPN.exe"
  Delete "$INSTDIR\kiwivpn-service.exe"
  Delete "$INSTDIR\tunnel.dll"
  Delete "$INSTDIR\wireguard.dll"
  Delete "$INSTDIR\uninstall.exe"
!macroend

!macro NSIS_HOOK_POSTINSTALL
  nsExec::ExecToLog '"$INSTDIR\kiwivpn-service.exe" install'
  Pop $0
  ${If} $0 != 0
    MessageBox MB_ICONEXCLAMATION|MB_OK "Kiwi VPN was installed, but its service didn't start (code $0). Restart your PC and run the installer again."
  ${EndIf}
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  !insertmacro KIWI_REMOVE_SERVICE
!macroend

; tunnel.dll keeps its log under Program Files\WireGuard\Data. If the real WireGuard app is installed there, that folder isn't ours to remove.
!macro NSIS_HOOK_POSTUNINSTALL
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "KiwiVPN"
  RMDir /r "$INSTDIR\tunnels"
  RMDir /r "$INSTDIR\logs"
  ${IfNot} ${FileExists} "$PROGRAMFILES64\WireGuard\wireguard.exe"
    RMDir /r "$PROGRAMFILES64\WireGuard\Data"
    RMDir "$PROGRAMFILES64\WireGuard"
  ${EndIf}
!macroend
