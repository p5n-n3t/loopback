# Loopback CLI wrapper for Windows PowerShell 5.1+ / PowerShell 7.
# The implementation is cli.py; this file only locates the interpreter.
#   installed:       %LOCALAPPDATA%\Loopback\venv\Scripts\python.exe %LOCALAPPDATA%\Loopback\cli.py <args>
#   source checkout: loopback.ps1 install ...  ->  python runtime\cli.py install ...
$ErrorActionPreference = 'Stop'

if ($env:LOOPBACK_CONFIG_DIR) {
    $Cfg = $env:LOOPBACK_CONFIG_DIR
} else {
    $Base = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $env:USERPROFILE 'AppData\Local' }
    $Cfg = Join-Path $Base 'Loopback'
}

$Py = Join-Path $Cfg 'venv\Scripts\python.exe'
$Override = Join-Path $Cfg 'python-override'
if (-not (Test-Path -LiteralPath $Py) -and (Test-Path -LiteralPath $Override)) {
    $Py = (Get-Content -LiteralPath $Override -Raw).Trim()
}
$Cli = Join-Path $Cfg 'cli.py'

if ((Test-Path -LiteralPath $Py) -and (Test-Path -LiteralPath $Cli)) {
    & $Py $Cli @args
    exit $LASTEXITCODE
}

if ($args.Count -gt 0 -and $args[0] -eq 'install') {
    $Root = Split-Path -Parent $PSScriptRoot
    $SourceCli = Join-Path $Root 'runtime\cli.py'
    if (Test-Path -LiteralPath $SourceCli) {
        $rest = @()
        if ($args.Count -gt 1) { $rest = $args[1..($args.Count - 1)] }
        $launcher = Get-Command py -ErrorAction SilentlyContinue
        if ($launcher) {
            & py -3 $SourceCli install @rest
        } else {
            $python = Get-Command python -ErrorAction SilentlyContinue
            if (-not $python) { Write-Error 'Python 3.11+ is required: winget install Python.Python.3.12'; exit 1 }
            & python $SourceCli install @rest
        }
        exit $LASTEXITCODE
    }
}

Write-Host "loopback: not installed (looked in $Cfg)." -ForegroundColor Yellow
Write-Host '  Install: irm https://github.com/p5n-n3t/loopback/releases/latest/download/install.ps1 | iex'
Write-Host '      or:  git clone https://github.com/p5n-n3t/loopback; cd loopback; .\install.ps1'
exit 1
