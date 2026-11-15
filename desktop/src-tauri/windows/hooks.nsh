; Calco installer hooks (Tauri NSIS). The installer runs as administrator, so it can let the
; bundled engine receive the phone's uploads on private networks: one firewall rule, removed again
; on uninstall. Public networks stay closed; the app's "Fix It" button handles that case.

!macro NSIS_HOOK_POSTINSTALL
  nsExec::Exec 'netsh advfirewall firewall delete rule name="Calco engine"'
  nsExec::Exec 'netsh advfirewall firewall add rule name="Calco engine" dir=in action=allow program="$INSTDIR\runtime\python\python.exe" enable=yes profile=private,domain'
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  nsExec::Exec 'netsh advfirewall firewall delete rule name="Calco engine"'
!macroend
