; makensis /DVERSION=0.0.1 /DPAYLOAD=C:\stage\Anima /DOUTPUT=C:\out\Anima-0.0.1-windows-x64-setup.exe windows.nsi
Unicode true
RequestExecutionLevel user
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
  IntCmp $2 0 +2
    Abort "Reparse points are not allowed in the installation path."
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
  GetFullPathName $0 "$INSTDIR"
  StrCmp $0 "$INSTDIR" +2
    Abort "Use a canonical absolute installation path (no trailing slash)."
  ${GetFileName} "$INSTDIR" $0
  StrCmp $0 "Anima" +2
    Abort "Installation directory must end in Anima."
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
    Return
  unsafe_path:
    Abort "Unsafe installation directory."
FunctionEnd
!macroend
!insertmacro SafetyFunctions ""
!insertmacro SafetyFunctions "un."

Function .onInit
  ${IfNot} ${RunningX64}
    Abort "Anima requires x64 Windows 10 or later."
  ${EndIf}
  ${IfNot} ${AtLeastWin10}
    Abort "Anima requires Windows 10 or later."
  ${EndIf}
  SetRegView 64
  SetShellVarContext current
  !insertmacro MUI_LANGDLL_DISPLAY
FunctionEnd

Section "Anima"
  Call ValidatePath
  ReadRegStr $0 HKCU "${ARP}" "InstallLocation"
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
    Abort "Anima is installed elsewhere, or this directory is not an owned Anima installation."
  copy_files:
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
    Abort "Installation failed. Exit Anima and retry; no process was killed."
  installed:
SectionEnd

Function un.onInit
  SetRegView 64
  SetShellVarContext current
  Call un.ValidatePath
  ReadRegStr $0 HKCU "${ARP}" "InstallLocation"
  StrCmp $0 "$INSTDIR" +2
    Abort "Installation registration does not match."
  FileOpen $1 "$INSTDIR\.anima-install" r
  IfErrors un_bad_owner
  FileRead $1 $2
  FileClose $1
  StrCmp $2 "${OWNER}" un_owner_ok
  un_bad_owner:
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
    Abort "Some program files could not be removed. Exit Anima and retry."
  un_done:
SectionEnd
