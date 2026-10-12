@echo off
cd /d "%~dp0"
if exist .venv\Scripts\activate.bat call .venv\Scripts\activate.bat
echo DOINg.AI Orchestrator sur http://localhost:4001
python server.py --open %*
