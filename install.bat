@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title DOINg.AI SuperCode - install
echo === DOINg.AI SuperCode - installation ===

rem --- Python: prefer the "py" launcher (python.exe may be the Microsoft Store alias)
set "PY="
where py >nul 2>nul && (py -3 -c "import sys; assert sys.version_info >= (3, 10)" >nul 2>nul && set "PY=py -3")
if not defined PY (
  where python >nul 2>nul && (python -c "import sys; assert sys.version_info >= (3, 10)" >nul 2>nul && set "PY=python")
)
if not defined PY (
  echo [ERROR] Python 3.10+ not found. Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
  pause & exit /b 1
)
where node >nul 2>nul || (echo [ERROR] Node.js 20+ not found: https://nodejs.org/ & pause & exit /b 1)
where git >nul 2>nul || echo [WARNING] Git not found: undo/snapshots and Bitbucket pull requests need Git for Windows (https://git-scm.com/download/win).
if not exist "%ProgramFiles%\Git\bin\bash.exe" echo [INFO] Git Bash not found: agents will use PowerShell for shell commands.

echo [1/4] Python environment (.venv)
if not exist .venv\Scripts\python.exe %PY% -m venv .venv || goto :fail
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt || goto :fail

echo [2/4] Frontend dependencies
pushd frontend
call npm install --no-audit --no-fund || (popd & goto :fail)

echo [3/4] Frontend build
call npm run build || (popd & goto :fail)
popd

echo [4/4] Desktop shortcut
set /p SHORTCUT="Create a desktop shortcut? [Y/n] "
if /i not "%SHORTCUT%"=="n" powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\DOINg.AI SuperCode.lnk');" ^
  "$s.TargetPath='%~dp0start.bat'; $s.WorkingDirectory='%~dp0'; $s.Save()"

echo.
echo Done. Run start.bat (or the desktop shortcut).
pause
exit /b 0

:fail
echo [ERROR] Installation stopped.
pause
exit /b 1
