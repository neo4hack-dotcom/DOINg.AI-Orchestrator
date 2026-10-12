@echo off
setlocal
cd /d "%~dp0"
echo === DOINg.AI Orchestrator - installation ===

where node >nul 2>nul || (echo [ERREUR] Node.js 20+ introuvable : https://nodejs.org/ & pause & exit /b 1)
where python >nul 2>nul || (echo [ERREUR] Python 3.10+ introuvable : https://www.python.org/downloads/ & pause & exit /b 1)

echo [1/4] Environnement Python (.venv)
if not exist .venv python -m venv .venv || goto :fail
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul
pip install -r requirements.txt || goto :fail

echo [2/4] Dependances frontend
pushd frontend
call npm install --no-audit --no-fund || (popd & goto :fail)

echo [3/4] Build du frontend
call npm run build || (popd & goto :fail)
popd

echo [4/4] Termine. Lancez start.bat
pause
exit /b 0

:fail
echo [ERREUR] Installation interrompue.
pause
exit /b 1
