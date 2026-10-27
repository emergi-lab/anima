[CmdletBinding()]
param(
    [string]$GodotPath = $env:GODOT_BIN,
    [switch]$ServerOnly
)

$ErrorActionPreference = 'Stop'
$server = $null
$backend = $null  # May be shared; only $server is owned by this launcher.
$pet = $null
$exitCode = 0
$serverDir = Join-Path $PSScriptRoot 'anima-server'
$petDir = Join-Path $PSScriptRoot 'anima-pet'

function Resolve-Godot {
    if ($GodotPath) {
        if (Test-Path -LiteralPath $GodotPath -PathType Container) {
            $found = Get-ChildItem -LiteralPath $GodotPath -Filter 'Godot*.exe' -File |
                Where-Object { $_.Name -notlike 'godot-ai*' } |
                Sort-Object @{ Expression = { $_.Name -like '*console*' } }, Name |
                Select-Object -First 1
            if ($found) { return $found.FullName }
            throw "No Godot executable found inside directory: $GodotPath"
        }
        $command = Get-Command $GodotPath -CommandType Application -ErrorAction SilentlyContinue
        if ($command) { return $command.Source }
        throw "Godot executable not found: $GodotPath"
    }
    foreach ($name in @('godot', 'godot4', 'godot.console')) {
        $command = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue
        if ($command) { return $command.Source }
    }
    foreach ($dir in @($PSScriptRoot, (Join-Path $PSScriptRoot 'tools'))) {
        $found = Get-ChildItem -LiteralPath $dir -Filter 'Godot*.exe' -File |
            Where-Object { $_.Name -notlike 'godot-ai*' } |
            Sort-Object Name | Select-Object -First 1
        if ($found) { return $found.FullName }
    }
    throw 'Godot not found. Use start.bat -GodotPath "C:\path\Godot.exe", set GODOT_BIN, or put Godot in tools\.'
}

$locationPushed = $false
try {
    $uv = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue
    if (-not $uv) { throw 'uv not found. Install uv, then reopen this launcher.' }
    if (-not $ServerOnly) { $godot = Resolve-Godot }
    Push-Location $serverDir
    $locationPushed = $true

    if (-not (Test-Path -LiteralPath '.env')) {
        Copy-Item -LiteralPath '.env.example' -Destination '.env'
    }
    Write-Host '[Anima] Preparing Python dependencies...'
    & $uv.Source sync --locked
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check the output above.' }
    $python = Join-Path $serverDir '.venv\Scripts\python.exe'
    # Read effective configuration without printing model credentials.
    $settingsJson = & $python -c 'import json; from persona_os.config import Settings; s=Settings(); print(json.dumps(dict(host=s.host,port=s.port,tokens=s.token_namespaces())))'
    if ($LASTEXITCODE -ne 0) { throw 'Cannot read backend settings. Check anima-server\.env.' }
    $settings = $settingsJson | ConvertFrom-Json
    if ($settings.host -notin @('127.0.0.1', 'localhost', '::1')) {
        throw 'For desktop use, set PERSONA_HOST=127.0.0.1 in anima-server\.env.'
    }
    $apiHost = $settings.host
    if ($apiHost -eq '::1') { $apiHost = '[::1]' }
    $api = "http://${apiHost}:$($settings.port)"
    if (-not $env:ANIMA_TOKEN) {
        $token = $settings.tokens.PSObject.Properties |
            Where-Object { $_.Value -eq 'anima' } | Select-Object -First 1
        if (-not $token) { throw 'No token for namespace anima. Check PERSONA_SERVICE_TOKENS.' }
        $env:ANIMA_TOKEN = $token.Name
    }
    $env:ANIMA_API = $api

    $listeners = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
    if ($listeners | Where-Object { $_.Port -eq $settings.port }) {
        # Verify process identity before sending the local service token. Windows venv
        # launchers may spawn a base Python process, so inspect its command line.
        $owners = @(Get-NetTCPConnection -LocalPort $settings.port -State Listen |
            Select-Object -ExpandProperty OwningProcess -Unique)
        $existing = if ($owners.Count -eq 1) {
            Get-CimInstance Win32_Process -Filter "ProcessId=$($owners[0])"
        }
        $expectedCommand = '^"?' + [regex]::Escape($python) + '"?\s+-m\s+persona_os\.main(?:\s|$)'
        if (-not $existing -or $existing.CommandLine -notmatch $expectedCommand) {
            throw "Port $($settings.port) is occupied by another or unrecognized process. Stop that service or change PERSONA_PORT in anima-server\.env."
        }
        try {
            $health = Invoke-RestMethod -Uri "$api/health" -TimeoutSec 3
            $runtime = Invoke-RestMethod -Uri "$api/v1/runtime" -TimeoutSec 3 -Headers @{ Authorization = "Bearer $env:ANIMA_TOKEN" }
            if ($health.status -ne 'ok' -or $runtime.namespace -ne 'anima') {
                throw 'Unexpected backend response.'
            }
        } catch {
            throw "The existing project backend on port $($settings.port) failed its health or authentication check. Restart it or check ANIMA_TOKEN and anima-server\.env."
        }
        # A healthy older process can still lack routes required by the current client.
        # Compare the checked-in API contract before reusing it, without sending mutations.
        $runningSchema = Invoke-RestMethod -Uri "$api/openapi.json" -TimeoutSec 3
        $expectedSchema = Get-Content -LiteralPath (Join-Path $serverDir 'docs\openapi.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        $missingRoutes = @()
        foreach ($route in $expectedSchema.paths.PSObject.Properties) {
            $runningRoute = $runningSchema.paths.PSObject.Properties[$route.Name]
            foreach ($method in $route.Value.PSObject.Properties.Name) {
                if ($method -notin @('get', 'post', 'put', 'patch', 'delete', 'head', 'options')) { continue }
                if (-not $runningRoute -or -not $runningRoute.Value.PSObject.Properties[$method]) {
                    $missingRoutes += "$($method.ToUpper()) $($route.Name)"
                }
            }
        }
        if ($missingRoutes.Count) {
            throw "Backend PID $($existing.ProcessId) is outdated; missing API: $($missingRoutes -join ', '). Stop that backend, then run this launcher again. Reopening only the pet will not update the server."
        }
        $backend = Get-Process -Id $existing.ProcessId
        Write-Host "[Anima] Reusing existing project backend (PID $($backend.Id)): $api"
    }

    if (-not $backend) {
        $logDir = Join-Path $serverDir 'logs'
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
        $logPrefix = Join-Path $logDir (Get-Date -Format 'yyyyMMdd-HHmmss-fff')
        $server = Start-Process -FilePath $python -ArgumentList '-m persona_os.main' -WorkingDirectory $serverDir -WindowStyle Hidden -PassThru -RedirectStandardOutput "$logPrefix.out.log" -RedirectStandardError "$logPrefix.err.log"
        $ready = $false
        $deadline = (Get-Date).AddSeconds(60)
        while ((Get-Date) -lt $deadline) {
            if ($server.HasExited) { throw "Backend exited. See $logPrefix.err.log" }
            try {
                $health = Invoke-RestMethod -Uri "$api/health" -TimeoutSec 2
                if ($health.status -eq 'ok') { $ready = $true; break }
            } catch { }
            Start-Sleep -Milliseconds 500
        }
        if (-not $ready) { throw "Backend did not become ready in 60 seconds. See $logPrefix.err.log" }
        Write-Host "[Anima] Backend ready: $api/docs"
        Write-Host "[Anima] Logs: $logDir"
        $backend = $server
    }
    if ($ServerOnly) {
        if (-not $server) {
            Write-Host '[Anima] Existing backend is healthy; leaving it running.'
            exit 0
        }
        Write-Host '[Anima] Press Ctrl+C to stop.'
        while (-not $server.HasExited) { Start-Sleep -Milliseconds 500 }
        throw "Backend exited unexpectedly. See $logPrefix.err.log"
    }

    Write-Host '[Anima] Importing Godot assets (first launch may take a while)...'
    & $godot --headless --path $petDir --editor --import
    if ($LASTEXITCODE -ne 0) { throw 'Godot asset import failed. Check the output above.' }
    if ($server) {
        Write-Host '[Anima] Opening desktop pet. Exit from its menu to stop both services.'
    } else {
        Write-Host '[Anima] Opening desktop pet. The shared backend will remain running when the pet exits.'
    }
    $pet = Start-Process -FilePath $godot -ArgumentList @('--path', ('"{0}"' -f $petDir)) -WorkingDirectory $petDir -PassThru
    while (-not $pet.HasExited) {
        if ($backend.HasExited) { throw 'Backend exited unexpectedly. Restart the launcher.' }
        Start-Sleep -Milliseconds 500
    }
    if ($pet.ExitCode -ne 0) { throw "Godot exited with code $($pet.ExitCode)." }
} catch {
    Write-Host "[Anima] ERROR: $($_.Exception.Message)" -ForegroundColor Red
    $exitCode = 1
} finally {
    if ($pet -and -not $pet.HasExited) { Stop-Process -Id $pet.Id -ErrorAction SilentlyContinue }
    if ($server -and -not $server.HasExited) { Stop-Process -Id $server.Id -ErrorAction SilentlyContinue }
    if ($locationPushed) { Pop-Location }
}
exit $exitCode
