; makensis /DVERSION=0.0.1 /DPAYLOAD=C:\stage\Anima /DOUTPUT=C:\out\Anima-0.0.1-windows-x64-setup.exe windows.nsi
Unicode true
RequestExecutionLevel user
ManifestSupportedOS Win10
!include MUI2.nsh
!include LogicLib.nsh
!include FileFunc.nsh
!include WinVer.nsh
!include x64.nsh
!ifndef VERSION
!error "VERSION is required"
!endif
!ifndef PAYLOAD
!error "PAYLOAD is required"
!endif
!ifndef OUTPUT
!error "OUTPUT is required"
!endif
!define ARP "Software\Microsoft\Windows\CurrentVersion\Uninstall\Anima"
!define OWNER "Anima per-user installer v1"
!tempfile FILE_LIST
!system 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "${__FILEDIR__}\test_windows_install.ps1" -GenerateIncludes -Payload "${PAYLOAD}" -IncludePath "${FILE_LIST}"' = 0
!include "${FILE_LIST}"
!delfile "${FILE_LIST}"
Name "Anima"
VIProductVersion "${VERSION}.0"
VIAddVersionKey /LANG=1033 "ProductName" "Anima"
VIAddVersionKey /LANG=1033 "FileDescription" "Anima per-user installer"
VIAddVersionKey /LANG=1033 "CompanyName" "Anima"
VIAddVersionKey /LANG=1033 "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=1033 "ProductVersion" "${VERSION}"
VIAddVersionKey /LANG=1033 "LegalCopyright" "Anima contributors"
OutFile "${OUTPUT}"
InstallDir "$LOCALAPPDATA\Programs\Anima"
ShowInstDetails show
ShowUninstDetails show
SetCompressor /SOLID lzma
!define MUI_ABORTWARNING
!insertmacro MUI_PAGE_WELCOME
; Deliberately no directory page. /D is supported for isolated CI only.
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\Anima.exe"
!define MUI_FINISHPAGE_RUN_NOTCHECKED
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"
!insertmacro MUI_LANGUAGE "SimpChinese"
LangString CloseFirst ${LANG_ENGLISH} "Please exit Anima before installing or upgrading. No processes will be terminated. Continue?"
LangString CloseFirst ${LANG_SIMPCHINESE} "安装或升级前请先退出 Anima。安装器不会终止进程。是否继续？"

; Optional CI diagnostics: create a unique file in an explicitly supplied existing
; directory, never overwrite a caller-selected file. No log in normal installs.
Var DiagnosticLog
Var DiagnosticHandle
Var DiagnosticHadErrors
!macro Trace MESSAGE
  StrCpy $DiagnosticHadErrors 0
  ${If} ${Errors}
    StrCpy $DiagnosticHadErrors 1
  ${EndIf}
  ${If} $DiagnosticLog != ""
    FileOpen $DiagnosticHandle "$DiagnosticLog" a
    FileSeek $DiagnosticHandle 0 END
    FileWrite $DiagnosticHandle "${MESSAGE}$\r$\n"
    FileClose $DiagnosticHandle
  ${EndIf}
  ClearErrors
  ${If} $DiagnosticHadErrors == 1
    SetErrors
  ${EndIf}
!macroend
!macro InitDiagnostics
  ReadEnvStr $DiagnosticLog ANIMA_INSTALL_LOG_DIR
  ${If} $DiagnosticLog != ""
    GetTempFileName $DiagnosticLog "$DiagnosticLog"
  ${EndIf}
  !insertmacro Trace "START instdir=$INSTDIR; temp=$TEMP; profile=$PROFILE"
!macroend

; Reject reparse points in the tree AND every ancestor: even exact file deletion
; must never follow a user-created junction into unrelated data.
!macro SafetyFunctions PREFIX
Function ${PREFIX}CheckTree
  Exch $0
  Push $1
  Push $2
  Push $3
  System::Call 'kernel32::GetFileAttributesW(w r0) i.r1'
  IntCmp $1 -1 tree_done
  IntOp $2 $1 & 0x400
  ${If} $2 != 0
    !insertmacro Trace "ABORT reparse point: $0"
    Abort "Reparse points are not allowed in the installation path."
  ${EndIf}
  IntOp $2 $1 & 0x10
  IntCmp $2 0 tree_done
  FindFirst $1 $2 "$0\*"
  tree_next:
    StrCmp $2 "" tree_close
    StrCmp $2 "." tree_skip
    StrCmp $2 ".." tree_skip
    Push "$0\$2"
    Call ${PREFIX}CheckTree
  tree_skip:
    FindNext $1 $2
    Goto tree_next
  tree_close:
    FindClose $1
  tree_done:
  Pop $3
  Pop $2
  Pop $1
  Pop $0
FunctionEnd

Function ${PREFIX}ValidatePath
  ; NSIS GetFullPathName also resolves long filenames and returns empty when
  ; intermediate directories do not exist. The Windows API is lexical only.
  System::Call 'kernel32::GetFullPathNameW(w "$INSTDIR", i ${NSIS_MAX_STRLEN}, w .r0, p 0) i.r1'
  !insertmacro Trace "PATH canonical=$0; length=$1; requested=$INSTDIR"
  ${If} $1 == 0
  ${OrIf} $1 >= ${NSIS_MAX_STRLEN}
    !insertmacro Trace "ABORT cannot canonicalize path"
    Abort "Cannot canonicalize installation path."
  ${EndIf}
  ${If} $0 != $INSTDIR
    !insertmacro Trace "ABORT path is not canonical"
    Abort "Use a canonical absolute installation path (no trailing slash)."
  ${EndIf}
  ${GetFileName} "$INSTDIR" $0
  ${If} $0 != "Anima"
    !insertmacro Trace "ABORT directory must end in Anima; leaf=$0"
    Abort "Installation directory must end in Anima."
  ${EndIf}
  StrCmp $INSTDIR "$PROFILE" unsafe_path
  StrCmp $INSTDIR "$LOCALAPPDATA" unsafe_path
  StrCmp $INSTDIR "$APPDATA" unsafe_path
  StrCmp $INSTDIR "$TEMP" unsafe_path
  StrCmp $INSTDIR "$LOCALAPPDATA\Anima" unsafe_path
  StrCmp $INSTDIR "$APPDATA\Anima" unsafe_path
  StrCpy $0 $INSTDIR
  ancestor:
    StrCmp $0 "$WINDIR" unsafe_path
    StrCmp $0 "$PROGRAMFILES" unsafe_path
    StrCmp $0 "$PROGRAMFILES64" unsafe_path
    System::Call 'kernel32::GetFileAttributesW(w r0) i.r1'
    !insertmacro Trace "ANCESTOR path=$0; attributes=$1"
    IntCmp $1 -1 ancestor_next
    IntOp $1 $1 & 0x400
    IntCmp $1 0 ancestor_next unsafe_path unsafe_path
  ancestor_next:
    ${GetParent} "$0" $1
    StrCmp $0 $1 ancestors_done
    StrCmp $1 "" ancestors_done
    StrCpy $0 $1
    Goto ancestor
  ancestors_done:
    Push "$INSTDIR"
    Call ${PREFIX}CheckTree
    !insertmacro Trace "PATH validation passed"
    Return
  unsafe_path:
    !insertmacro Trace "ABORT unsafe path; ancestor=$0; attributes=$1"
    Abort "Unsafe installation directory."
FunctionEnd
!macroend
!insertmacro SafetyFunctions ""
!insertmacro SafetyFunctions "un."

Function .onInit
  !insertmacro InitDiagnostics
  !insertmacro Trace "CHECK x64 Windows 10+"
  ${IfNot} ${RunningX64}
    !insertmacro Trace "ABORT RunningX64=false"
    Abort "Anima requires x64 Windows 10 or later."
  ${EndIf}
  ${IfNot} ${AtLeastWin10}
    !insertmacro Trace "ABORT AtLeastWin10=false"
    Abort "Anima requires Windows 10 or later."
  ${EndIf}
  !insertmacro Trace "OS checks passed"
  SetRegView 64
  SetShellVarContext current
  !insertmacro MUI_LANGDLL_DISPLAY
FunctionEnd

Section "Anima"
  Call ValidatePath
  ; FindNext sets the error flag at normal end-of-enumeration.
  ClearErrors
  ReadRegStr $0 HKCU "${ARP}" "InstallLocation"
  !insertmacro Trace "OWNERSHIP registered=$0; target=$INSTDIR"
  StrCmp $0 "" new_install
  StrCmp $0 "$INSTDIR" 0 conflict
  FileOpen $1 "$INSTDIR\.anima-install" r
  IfErrors conflict
  FileRead $1 $2
  FileClose $1
  StrCmp $2 "${OWNER}" 0 conflict
  Goto ready
  new_install:
    IfFileExists "$SMPROGRAMS\Anima.lnk" conflict
    IfFileExists "$DESKTOP\Anima.lnk" conflict
    ; Never overwrite a pre-existing nonempty directory, even with /S.
    FindFirst $1 $2 "$INSTDIR\*"
    check_empty:
      StrCmp $2 "" empty_done
      StrCmp $2 "." empty_next
      StrCmp $2 ".." empty_next conflict
    empty_next:
      FindNext $1 $2
      Goto check_empty
    empty_done:
      FindClose $1
  ready:
    IfSilent copy_files
    MessageBox MB_OKCANCEL|MB_ICONINFORMATION "$(CloseFirst)" IDOK copy_files
    Abort
  conflict:
    !insertmacro Trace "FAIL ownership/nonempty-directory/shortcut conflict; entry=$2"
    Abort "Anima is installed elsewhere, or this directory is not an owned Anima installation."
  copy_files:
    !insertmacro Trace "COPY payload"
    SetOverwrite on
    ClearErrors
    !insertmacro InstallPayload
    IfErrors copy_failed
    FileOpen $1 "$INSTDIR\.anima-install" w
    FileWrite $1 "${OWNER}"
    FileClose $1
    WriteUninstaller "$INSTDIR\Uninstall.exe"
    IfErrors copy_failed
    CreateShortcut "$SMPROGRAMS\Anima.lnk" "$INSTDIR\Anima.exe"
    CreateShortcut "$DESKTOP\Anima.lnk" "$INSTDIR\Anima.exe"
    WriteRegStr HKCU "${ARP}" "DisplayName" "Anima"
    WriteRegStr HKCU "${ARP}" "DisplayVersion" "${VERSION}"
    WriteRegStr HKCU "${ARP}" "Publisher" "Anima"
    WriteRegStr HKCU "${ARP}" "InstallLocation" "$INSTDIR"
    WriteRegStr HKCU "${ARP}" "DisplayIcon" "$INSTDIR\Anima.exe"
    WriteRegStr HKCU "${ARP}" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
    WriteRegStr HKCU "${ARP}" "QuietUninstallString" '$\"$INSTDIR\Uninstall.exe$\" /S'
    WriteRegDWORD HKCU "${ARP}" "NoModify" 1
    WriteRegDWORD HKCU "${ARP}" "NoRepair" 1
    Goto installed
  copy_failed:
    !insertmacro Trace "FAIL copying payload or writing uninstaller"
    Abort "Installation failed. Exit Anima and retry; no process was killed."
  installed:
    !insertmacro Trace "INSTALL complete"
SectionEnd

Function un.onInit
  !insertmacro InitDiagnostics
  !insertmacro Trace "UNINSTALL ownership/path checks"
  SetRegView 64
  SetShellVarContext current
  Call un.ValidatePath
  ClearErrors
  ReadRegStr $0 HKCU "${ARP}" "InstallLocation"
  ${If} $0 != $INSTDIR
    !insertmacro Trace "ABORT registration mismatch; registered=$0"
    Abort "Installation registration does not match."
  ${EndIf}
  FileOpen $1 "$INSTDIR\.anima-install" r
  IfErrors un_bad_owner
  FileRead $1 $2
  FileClose $1
  StrCmp $2 "${OWNER}" un_owner_ok
  un_bad_owner:
    !insertmacro Trace "ABORT ownership marker missing or invalid"
    Abort "Installation ownership marker is missing."
  un_owner_ok:
FunctionEnd

Section "Uninstall"
  ; Generated exact file list; directories are removed ONLY when empty.
  ; No recursive deletion and no operations on application/Godot user data.
  ClearErrors
  !insertmacro RemovePayload
  IfErrors un_locked
  !insertmacro RemoveEmptyDirectories
  Delete "$SMPROGRAMS\Anima.lnk"
  Delete "$DESKTOP\Anima.lnk"
  DeleteRegKey HKCU "${ARP}"
  Delete "$INSTDIR\.anima-install"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  Goto un_done
  un_locked:
    !insertmacro Trace "FAIL removing program files (locked?)"
    Abort "Some program files could not be removed. Exit Anima and retry."
  un_done:
    !insertmacro Trace "UNINSTALL complete"
SectionEnd
