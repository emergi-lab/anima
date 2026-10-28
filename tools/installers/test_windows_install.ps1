[CmdletBinding()]
param(
    [string]$Installer,
    [switch]$GenerateIncludes,
    [string]$Payload,
    [string]$IncludePath
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# Also used by makensis: enumerate the build artifact, not the source checkout.
# Emit exact File/Delete commands so uninstall never recursively deletes a folder.
if ($GenerateIncludes) {
    $root = (Resolve-Path -LiteralPath $Payload).Path.TrimEnd('\')
    $allowed = @('Anima.exe', '_internal', 'client', 'licenses', 'build-info.txt')
    foreach ($name in $allowed) {
        if (-not (Test-Path -LiteralPath (Join-Path $root $name))) { throw "Missing payload entry: $name" }
    }
    foreach ($entry in Get-ChildItem -LiteralPath $root -Force) {
        if ($entry.Name -notin $allowed) { throw "Unexpected payload entry (possible user/test data): $($entry.Name)" }
    }
    function Quote-Nsis([string]$text) {
        if ($text -match '[\r\n"`]') { throw "Unsupported payload path: $text" }
        return $text.Replace('$', '$$')
    }
    # Walk without following junctions/symlinks.
    function Get-SafeEntries([string]$path) {
        foreach ($entry in Get-ChildItem -LiteralPath $path -Force) {
            if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Payload reparse point: $($entry.FullName)" }
            $entry
            if ($entry.PSIsContainer) { Get-SafeEntries $entry.FullName }
        }
    }
    if ((Get-Item -LiteralPath $root).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Payload must not be a reparse point' }
    $entries = @(Get-SafeEntries $root)
    $files = @($entries | Where-Object { -not $_.PSIsContainer } | Sort-Object FullName)
    $dirs = @($entries | Where-Object PSIsContainer | Sort-Object { $_.FullName.Length } -Descending)
    $lines = [Collections.Generic.List[string]]::new()
    $lines.Add('!macro InstallPayload')
    foreach ($dir in $dirs) {
        $lines.Add('CreateDirectory "$INSTDIR\' + (Quote-Nsis $dir.FullName.Substring($root.Length + 1)) + '"')
    }
    foreach ($file in $files) {
        $relative = $file.FullName.Substring($root.Length + 1)
        $parent = Split-Path -Parent $relative
        $lines.Add('SetOutPath "$INSTDIR\' + (Quote-Nsis $parent) + '"')
        $lines.Add('File "' + (Quote-Nsis $file.FullName) + '"')
    }
    $lines.Add('!macroend')
    $lines.Add('!macro RemovePayload')
    foreach ($file in $files) {
        $lines.Add('Delete "$INSTDIR\' + (Quote-Nsis $file.FullName.Substring($root.Length + 1)) + '"')
    }
    $lines.Add('!macroend')
    $lines.Add('!macro RemoveEmptyDirectories')
    foreach ($dir in $dirs) {
        $lines.Add('RMDir "$INSTDIR\' + (Quote-Nsis $dir.FullName.Substring($root.Length + 1)) + '"')
    }
    $lines.Add('!macroend')
    [IO.File]::WriteAllLines($IncludePath, $lines, [Text.UTF8Encoding]::new($true))
    exit 0
}

if (-not $Installer) { throw 'Usage: test_windows_install.ps1 -Installer <setup.exe>' }
if (-not [Environment]::Is64BitProcess) { throw 'Run this test in 64-bit PowerShell' }
# NSIS shell folders use Known Folders, NOT APPDATA environment overrides.
# Therefore only run in a disposable GitHub Actions account. Shortcuts are checked
# in that account's real shell folders; application data is redirected below.
if ($env:GITHUB_ACTIONS -ne 'true' -or -not $env:RUNNER_TEMP) {
    throw 'This test requires a disposable GitHub Actions Windows runner (not a personal account).'
}
$Installer = (Resolve-Path -LiteralPath $Installer).Path
$arp = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Anima'
$links = @(
    (Join-Path ([Environment]::GetFolderPath('Programs')) 'Anima.lnk'),
    (Join-Path ([Environment]::GetFolderPath('DesktopDirectory')) 'Anima.lnk')
)
$defaultInstall = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Programs\Anima'
if ((Test-Path $arp) -or (Test-Path -LiteralPath $defaultInstall)) { throw 'Existing Anima installation; refusing to test' }
foreach ($link in $links) { if (Test-Path -LiteralPath $link) { throw "Existing shortcut: $link" } }
$work = Join-Path $env:RUNNER_TEMP ('anima installer test-' + [Guid]::NewGuid().ToString('N'))
$target = Join-Path $work 'Anima'
$variables = @('LOCALAPPDATA', 'APPDATA', 'ANIMA_DATA_DIR', 'ANIMA_SMOKE_LOG_DIR', 'ANIMA_INSTALL_LOG_DIR')
$saved = @{}
foreach ($name in $variables) { $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
function Assert([bool]$Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
function Run-Wait([string]$Exe, [string]$Arguments) {
    Write-Host "RUN: $Exe $Arguments"
    Write-Host "OS: $([Environment]::OSVersion); 64-bit OS: $([Environment]::Is64BitOperatingSystem)"
    $process = Start-Process -FilePath $Exe -ArgumentList $Arguments -PassThru
    if (-not $process.WaitForExit(240000)) { throw "Timed out (process not killed): $Exe" }
    $process.Refresh()
    Write-Host "EXIT: $($process.ExitCode)"
    if ($env:ANIMA_INSTALL_LOG_DIR -and (Test-Path -LiteralPath $env:ANIMA_INSTALL_LOG_DIR)) {
        foreach ($log in Get-ChildItem -LiteralPath $env:ANIMA_INSTALL_LOG_DIR -File) {
            Write-Host "NSIS DIAGNOSTICS: $($log.FullName)"
            Get-Content -LiteralPath $log.FullName | ForEach-Object { Write-Host $_ }
        }
    }
    Assert ($process.ExitCode -eq 0) "$Exe returned $($process.ExitCode); diagnostics: $env:ANIMA_INSTALL_LOG_DIR"
}
try {
    $env:LOCALAPPDATA = Join-Path $work 'local'
    $env:APPDATA = Join-Path $work 'roaming'
    $env:ANIMA_DATA_DIR = Join-Path $env:LOCALAPPDATA 'Anima'
    $env:ANIMA_SMOKE_LOG_DIR = Join-Path $work 'smoke-logs'
    $env:ANIMA_INSTALL_LOG_DIR = Join-Path $work 'installer-logs'
    New-Item -ItemType Directory -Path $env:ANIMA_INSTALL_LOG_DIR -Force | Out-Null
    Write-Host "Isolated installation target: $target"
    $godotData = Join-Path $env:APPDATA 'Godot\app_userdata\Anima'
    foreach ($dir in @($env:ANIMA_DATA_DIR, $godotData, $env:ANIMA_SMOKE_LOG_DIR)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    $sentinels = @((Join-Path $env:ANIMA_DATA_DIR 'keep.txt'), (Join-Path $godotData 'keep.txt'))
    foreach ($file in $sentinels) { [IO.File]::WriteAllText($file, 'user data must survive') }
    $shell = New-Object -ComObject WScript.Shell
    # Same-version reinstall exercises the upgrade ownership path.
    foreach ($iteration in 1..2) {
        # NSIS /D must be last, and its value MUST NOT be quoted (spaces allowed).
        Run-Wait $Installer "/S /D=$target"
        foreach ($entry in @('Anima.exe', 'client\Anima.exe', '_internal', 'licenses', 'build-info.txt', 'Uninstall.exe')) {
            Assert (Test-Path -LiteralPath (Join-Path $target $entry)) "Missing installed payload: $entry"
        }
        $registration = Get-ItemProperty $arp
        $buildInfo = Get-Content -LiteralPath (Join-Path $target 'build-info.txt') -Raw
        Assert ($buildInfo -match ('(?m)^Anima ' + [regex]::Escape($registration.DisplayVersion) + '\r?$')) 'Incorrect DisplayVersion'
        Assert ($registration.InstallLocation -eq $target) 'Incorrect InstallLocation'
        Assert ($registration.UninstallString -eq ('"' + $target + '\Uninstall.exe"')) 'Incorrect UninstallString'
        Assert ($registration.QuietUninstallString -eq ('"' + $target + '\Uninstall.exe" /S')) 'Incorrect QuietUninstallString'
        foreach ($link in $links) {
            Assert (Test-Path -LiteralPath $link) "Missing shortcut: $link"
            Assert ($shell.CreateShortcut($link).TargetPath -eq (Join-Path $target 'Anima.exe')) "Incorrect shortcut target: $link"
        }
        foreach ($file in $sentinels) { Assert (([IO.File]::ReadAllText($file)) -eq 'user data must survive') 'User data changed' }
        Run-Wait (Join-Path $target 'Anima.exe') '--smoke-test'
        # A foreign file inside an installed program folder must survive too.
        $foreign = Join-Path $target 'client\user-added.txt'
        [IO.File]::WriteAllText($foreign, 'not owned by installer')
    }
    # _?= avoids the uninstaller's self-copy / parent-exits-early behavior.
    Run-Wait (Join-Path $target 'Uninstall.exe') "/S _?=$target"
    Assert (-not (Test-Path -LiteralPath (Join-Path $target 'Anima.exe'))) 'Launcher was not removed'
    Assert (-not (Test-Path -LiteralPath (Join-Path $target 'client\Anima.exe'))) 'Client was not removed'
    Assert (-not (Test-Path $arp)) 'Uninstall registration was not removed'
    foreach ($link in $links) { Assert (-not (Test-Path -LiteralPath $link)) "Shortcut was not removed: $link" }
    foreach ($file in $sentinels) { Assert (([IO.File]::ReadAllText($file)) -eq 'user data must survive') 'User data deleted' }
    Assert (([IO.File]::ReadAllText($foreign)) -eq 'not owned by installer') 'Foreign program-directory file deleted'
    Write-Host "Installer/reinstall/smoke/uninstall checks passed. Artifacts: $work"
} finally {
    foreach ($name in $variables) { [Environment]::SetEnvironmentVariable($name, $saved[$name], 'Process') }
    # Keep disposable artifacts on failure for diagnosis. Never recursively clean
    # an install path here, and never unregister an existing user installation.
}
