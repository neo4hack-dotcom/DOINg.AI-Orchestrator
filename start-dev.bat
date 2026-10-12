@echo off
cd /d "%~dp0"
rem FastAPI on :4001 + Vite (hot reload) on :4000
start "SuperCode API" cmd /k "if exist .venv\Scripts\activate.bat call .venv\Scripts\activate.bat & python server.py"
start "SuperCode UI" cmd /k "cd frontend & npm run dev"
timeout /t 3 >nul
start http://localhost:4000
