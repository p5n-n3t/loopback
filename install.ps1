<#
.SYNOPSIS
  Loopback installer for Windows (PowerShell 5.1+ and PowerShell 7).

.DESCRIPTION
  Ensures Python 3.11+ is available (offers 'winget install Python.Python.3.12'
  with your consent), then runs the single cross-platform installer:
  python runtime\cli.py install <your arguments>.

  From a checkout:   .\install.ps1 [-Yes] [--non-interactive --ingress local ...]
  One-line install:  irm https://github.com/p5n-n3t/loopback/releases/latest/download/install.ps1 | iex
                     (set $env:LOOPBACK_VERSION to pin a release)

  Windows limitations:
    * There is no OS sandbox for shell commands on Windows; Loopback's path and
      command policy still apply, but commands run with your account's rights.
    * Autostart uses Task Scheduler (task "Loopback", at logon, limited rights).
    * Persistent PTY terminals are not available.
#>
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
$Repo = 'p5n-n3t/loopback'
$PassArgs = @($args)
$Yes = ($PassArgs -contains '--yes') -or ($PassArgs -contains '-y') -or ($PassArgs -contains '-Yes')
$PassArgs = @($PassArgs | Where-Object { $_ -ne '-Yes' })
if ($Yes -and -not ($PassArgs -contains '--yes')) { $PassArgs += '--yes' }
$NonInteractive = $PassArgs -contains '--non-interactive'

function Write-Ok($msg) { Write-Host "[ok] $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "[!] $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "[x] $msg" -ForegroundColor Red; exit 1 }
function Ask($question) {
    if ($Yes) { return $true }
    if ($NonInteractive) { return $false }
    $answer = Read-Host "$question [y/N]"
    return ($answer -match '^(y|yes)$')
}

Write-Host ''
Write-Host 'LOOPBACK - MCP control plane installer (Windows)' -ForegroundColor Yellow
Write-Host ''

# Refuse an elevated (Administrator) session: Loopback must run as the normal account.
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator) -and -not ($PassArgs -contains '--allow-root')) {
    Write-Warn 'This PowerShell session is elevated (Run as Administrator).'
    Write-Warn 'Loopback runs as your normal account; open a normal PowerShell window and rerun,'
    Write-Warn 'or pass --allow-root if you really intend to install for the Administrator account.'
    exit 2
}

function Find-Python {
    $candidates = @()
    if (Get-Command py -ErrorAction SilentlyContinue) { $candidates += , @('py', '-3') }
    if (Get-Command python -ErrorAction SilentlyContinue) { $candidates += , @('python') }
    if (Get-Command python3 -ErrorAction SilentlyContinue) { $candidates += , @('python3') }
    foreach ($c in $candidates) {
        $exe = $c[0]
        $pre = @()
        if ($c.Count -gt 1) { $pre = $c[1..($c.Count - 1)] }
        try {
            $out = & $exe @pre -c "import sys, venv, ensurepip; print(sys.executable if sys.version_info >= (3, 11) else '')" 2>$null
            if ($LASTEXITCODE -eq 0 -and $out -and ($out.Trim() -ne '') -and ($out -notmatch 'WindowsApps')) {
                return $out.Trim()
            }
        } catch { }
    }
    return $null
}

$Python = Find-Python
if (-not $Python) {
    Write-Warn 'Python 3.11+ was not found.'
    if ((Get-Command winget -ErrorAction SilentlyContinue) -and (Ask 'Install Python 3.12 with: winget install Python.Python.3.12 ?')) {
        winget install --id Python.Python.3.12 -e --scope user --accept-package-agreements --accept-source-agreements
        $env:Path = [Environment]::GetEnvironmentVariable('Path', 'User') + ';' + [Environment]::GetEnvironmentVariable('Path', 'Machine')
        $Python = Find-Python
    }
    if (-not $Python) {
        Fail 'Python 3.11+ is required. Install it with "winget install Python.Python.3.12" (or from python.org), open a new PowerShell window and rerun.'
    }
}
Write-Ok "python: $Python"

# Locate the source: this script's directory, or download a verified release.
$Root = $null
if ($PSScriptRoot -and (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'runtime\cli.py'))) {
    $Root = $PSScriptRoot
}
$Temp = $null
if (-not $Root) {
    if ($env:LOOPBACK_SOURCE_DIR -and (Test-Path -LiteralPath (Join-Path $env:LOOPBACK_SOURCE_DIR 'runtime\cli.py'))) {
        $Root = $env:LOOPBACK_SOURCE_DIR
    } else {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $Tag = $env:LOOPBACK_VERSION
        if (-not $Tag) {
            $rel = Invoke-RestMethod -UseBasicParsing -Headers @{ 'User-Agent' = 'loopback-installer' } "https://api.github.com/repos/$Repo/releases/latest"
            $Tag = $rel.tag_name
        }
        if (-not $Tag.StartsWith('v')) { $Tag = "v$Tag" }
        $Temp = Join-Path ([IO.Path]::GetTempPath()) ("loopback-" + [Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $Temp | Out-Null
        $base = "https://github.com/$Repo/releases/download/$Tag"
        $archive = Join-Path $Temp "loopback-$Tag.tar.gz"
        Write-Host "Downloading Loopback $Tag..."
        Invoke-WebRequest -UseBasicParsing -Uri "$base/loopback-$Tag.tar.gz" -OutFile $archive
        Invoke-WebRequest -UseBasicParsing -Uri "$base/SHA256SUMS" -OutFile (Join-Path $Temp 'SHA256SUMS')
        $line = Get-Content (Join-Path $Temp 'SHA256SUMS') | Where-Object { $_ -match "loopback-$([regex]::Escape($Tag))\.tar\.gz$" } | Select-Object -First 1
        if (-not $line) { Fail 'SHA256SUMS has no entry for the release archive' }
        $expected = ($line -split '\s+')[0].ToLower()
        $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLower()
        if ($expected -ne $actual) { Fail "checksum verification FAILED for loopback-$Tag.tar.gz" }
        Write-Ok "verified loopback-$Tag.tar.gz (sha256)"
        tar -xzf $archive -C $Temp
        if ($LASTEXITCODE -ne 0) { Fail 'could not extract the release (tar.exe needs Windows 10 1803+)' }
        $Root = Join-Path $Temp "loopback-$Tag"
    }
}

$hasSource = ($PassArgs | Where-Object { $_ -like '--source*' }).Count -gt 0
$cliArgs = @((Join-Path $Root 'runtime\cli.py'), 'install') + $PassArgs
if (-not $hasSource) { $cliArgs += @('--source', $Root) }

& $Python @cliArgs
$code = $LASTEXITCODE

if ($Temp) { Remove-Item -Recurse -Force -LiteralPath $Temp -ErrorAction SilentlyContinue }

Write-Host ''
Write-Host 'Windows notes:' -ForegroundColor Yellow
Write-Host '  * No OS sandbox for shell commands on Windows: keep the policy profile restrictive.'
Write-Host '  * Autostart runs through Task Scheduler (task "Loopback" at logon).'
Write-Host '  * The one-time dashboard enrollment link is printed above (loopback auth enroll makes a new one).'
Write-Host "  * CLI: $env:LOCALAPPDATA\Loopback\bin\loopback.cmd (add that folder to your PATH)."
exit $code
