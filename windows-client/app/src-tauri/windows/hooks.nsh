; Kiwi VPN installer hooks. Setup runs as administrator once, so the person never touches a service, a command line or a separate WireGuard install.

!macro KIWI_REMOVE_SERVICE
  ${If} ${FileExists} "$INSTDIR\kiwivpn-service.exe"
    nsExec::ExecToLog '"$INSTDIR\kiwivpn-service.exe" uninstall'
    Pop $0
    Sleep 2000 ; Windows releases the service's files just after it stops
  ${EndIf}
!macroend

; Upgrading: the running service holds its own files, so it goes first. The tunnel drops for the moment of the upgrade.
!macro NSIS_HOOK_PREINSTALL
  !insertmacro KIWI_REMOVE_SERVICE
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
  RMDir /r "$INSTDIR\tunnels"
  ${IfNot} ${FileExists} "$PROGRAMFILES64\WireGuard\wireguard.exe"
    RMDir /r "$PROGRAMFILES64\WireGuard\Data"
    RMDir "$PROGRAMFILES64\WireGuard"
  ${EndIf}
!macroend
