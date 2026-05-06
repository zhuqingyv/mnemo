<#
.SYNOPSIS
mnemo installer for Windows.

.DESCRIPTION
Downloads the prebuilt mnemo binary for Windows x86_64, verifies its SHA256,
installs it under %LOCALAPPDATA%\mnemo\bin\, ensures that directory is on
the user's PATH, and runs `mnemo setup --auto` to configure detected AI
clients (Claude Code, Cursor, Codex CLI, Claude Desktop).

.PARAMETER Version
Pin a specific version tag (e.g. v0.2.1). Default: latest.

.PARAMETER Repo
Override repo slug. Default: zhuqingyv/mnemo.

.PARAMETER InstallDir
Where the binary lands. Default: $env:LOCALAPPDATA\mnemo\bin.

.PARAMETER NoSetup
Skip the post-install `mnemo setup --auto` step.

.PARAMETER NoPath
Skip writing to the user PATH environment variable.

.EXAMPLE
irm https://github.com/zhuqingyv/mnemo/releases/latest/download/install.ps1 | iex
#>

[CmdletBinding()]
param(
    [string]$Version = $(if ($env:MNEMO_VERSION) { $env:MNEMO_VERSION } else { "latest" }),
    [string]$Repo    = $(if ($env:MNEMO_REPO)    { $env:MNEMO_REPO }    else { "zhuqingyv/mnemo" }),
    [string]$InstallDir = $(if ($env:MNEMO_INSTALL_DIR) { $env:MNEMO_INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA "mnemo\bin" }),
    [switch]$NoSetup = [bool]$env:MNEMO_NO_SETUP,
    [switch]$NoPath  = [bool]$env:MNEMO_NO_PATH
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Write-Info($msg)  { Write-Host "==> $msg" -ForegroundColor Green }
function Write-Warn2($msg) { Write-Host "warn: $msg" -ForegroundColor Yellow }
function Fail($msg)        { Write-Host "error: $msg" -ForegroundColor Red; exit 1 }

# -- detect ARCH (Windows is x86_64-only for now) ----------------------------
$arch = "x86_64"
if ([Environment]::Is64BitOperatingSystem -eq $false) {
    Fail "32-bit Windows is not supported. mnemo requires x86_64."
}
# ARM64 Windows runs x86_64 binaries through emulation; we ship x86_64 only.

$asset = "mnemo-windows-$arch.exe"

if ($Version -eq "latest") {
    $base = "https://github.com/$Repo/releases/latest/download"
} else {
    $base = "https://github.com/$Repo/releases/download/$Version"
}

$binaryUrl = "$base/$asset"
$sumsUrl   = "$base/SHA256SUMS"

# -- download ---------------------------------------------------------------
$tmp = New-Item -ItemType Directory -Path (Join-Path $env:TEMP ("mnemo-install-" + [Guid]::NewGuid().ToString("N"))) -Force
try {
    Write-Info "Downloading $asset ($Version) from $Repo"
    $tmpBinary = Join-Path $tmp $asset
    try {
        Invoke-WebRequest -Uri $binaryUrl -OutFile $tmpBinary -UseBasicParsing
    } catch {
        Fail "download failed: $binaryUrl ($($_.Exception.Message))"
    }

    # -- verify sha256 ------------------------------------------------------
    $tmpSums = Join-Path $tmp "SHA256SUMS"
    $sumsAvailable = $false
    try {
        Invoke-WebRequest -Uri $sumsUrl -OutFile $tmpSums -UseBasicParsing
        $sumsAvailable = $true
    } catch {
        Write-Warn2 "SHA256SUMS not found at $sumsUrl, skipping verification"
    }

    if ($sumsAvailable) {
        $expected = $null
        foreach ($line in Get-Content $tmpSums) {
            if ($line -match "^([0-9a-fA-F]{64})\s+\.?/?$([Regex]::Escape($asset))\s*$") {
                $expected = $matches[1].ToLower()
                break
            }
        }
        if ($expected) {
            $actual = (Get-FileHash $tmpBinary -Algorithm SHA256).Hash.ToLower()
            if ($actual -ne $expected) {
                Fail "sha256 mismatch for $asset (expected $expected, got $actual)"
            }
            Write-Info "sha256 verified"
        } else {
            Write-Warn2 "no SHA256 entry for $asset, skipping verification"
        }
    }

    # -- install -----------------------------------------------------------
    if (-not (Test-Path $InstallDir)) {
        New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    }
    $dest = Join-Path $InstallDir "mnemo.exe"

    # Best-effort: stop any running mnemo so we can overwrite the file.
    Get-Process -Name "mnemo" -ErrorAction SilentlyContinue | ForEach-Object {
        try { $_ | Stop-Process -Force -ErrorAction SilentlyContinue } catch {}
    }

    Copy-Item -Path $tmpBinary -Destination $dest -Force
    Write-Info "Installed: $dest"

    # -- ensure PATH (user scope) ------------------------------------------
    if (-not $NoPath) {
        $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
        if (-not $userPath) { $userPath = "" }
        $entries = $userPath.Split(";") | Where-Object { $_ -ne "" }
        if (-not ($entries -contains $InstallDir)) {
            $newPath = ($entries + $InstallDir) -join ";"
            [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
            Write-Info "Added $InstallDir to user PATH"
            # Make it visible in *this* shell too
            $env:Path = "$env:Path;$InstallDir"
        }
    }

    # -- run setup ---------------------------------------------------------
    if ($NoSetup) {
        Write-Info "Skipping setup (NoSetup set)"
    } else {
        Write-Info "Running 'mnemo setup --auto' to configure detected AI clients"
        try {
            & $dest setup --auto
            if ($LASTEXITCODE -ne 0) {
                Write-Warn2 "mnemo setup exited with code $LASTEXITCODE — re-run manually:"
                Write-Warn2 "    $dest setup --auto"
            }
        } catch {
            Write-Warn2 "mnemo setup failed: $($_.Exception.Message)"
            Write-Warn2 "Re-run manually: $dest setup --auto"
        }
    }

    Write-Info "All done."
    Write-Info "Verify:    & '$dest' --version"
    Write-Info "Open a new terminal so 'mnemo' is on PATH."
    Write-Info "Restart your AI client (Claude Code / Cursor / Codex / Claude Desktop) to activate mnemo."
}
finally {
    if (Test-Path $tmp) {
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    }
}
