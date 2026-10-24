@echo off
rem Loopback CLI wrapper for cmd.exe. The implementation is cli.py.
setlocal
if defined LOOPBACK_CONFIG_DIR (
  set "LB_CFG=%LOOPBACK_CONFIG_DIR%"
) else (
  set "LB_CFG=%LOCALAPPDATA%\Loopback"
)
set "LB_PY=%LB_CFG%\venv\Scripts\python.exe"
if not exist "%LB_PY%" if exist "%LB_CFG%\python-override" set /p LB_PY=<"%LB_CFG%\python-override"
if exist "%LB_PY%" if exist "%LB_CFG%\cli.py" (
  "%LB_PY%" "%LB_CFG%\cli.py" %*
  exit /b %ERRORLEVEL%
)
if /i "%~1"=="install" if exist "%~dp0..\runtime\cli.py" (
  where py >nul 2>nul
  if not errorlevel 1 (
    py -3 "%~dp0..\runtime\cli.py" %*
  ) else (
    python "%~dp0..\runtime\cli.py" %*
  )
  exit /b %ERRORLEVEL%
)
echo loopback: not installed (looked in %LB_CFG%). 1>&2
echo   Install: powershell -ExecutionPolicy Bypass -c "irm https://github.com/p5n-n3t/loopback/releases/latest/download/install.ps1 ^| iex" 1>&2
exit /b 1
