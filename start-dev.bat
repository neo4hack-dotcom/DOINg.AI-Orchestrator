@echo off
cd /d "%~dp0"
REM API FastAPI sur :4001 + Vite (hot reload) sur :4000
start "Orchestrator API" cmd /k "if exist .venv\Scripts\activate.bat call .venv\Scripts\activate.bat & python server.py"
start "Orchestrator UI" cmd /k "cd frontend & npm run dev"
timeout /t 3 >nul
start http://localhost:4000
