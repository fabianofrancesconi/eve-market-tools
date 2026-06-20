@echo off
REM ===  EVE LP Store Scanner  =======================================
REM  Double-click this file to launch the web app. It will:
REM    1. make sure the one dependency (requests) is installed
REM    2. start the local server
REM    3. open the scanner in your default browser automatically
REM  Close this window (or press Ctrl+C) to stop the server.
REM ==================================================================

cd /d "%~dp0"

REM Prefer the Windows "py" launcher; fall back to "python".
where py >nul 2>nul && (set "PY=py") || (set "PY=python")

REM Install requests only if it's missing.
%PY% -c "import requests" 2>nul || %PY% -m pip install requests

title EVE LP Store Scanner
%PY% lp-web.py

echo.
echo Server stopped.
pause
