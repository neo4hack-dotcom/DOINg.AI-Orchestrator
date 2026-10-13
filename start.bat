@echo off
cd /d "%~dp0"
title DOINg.AI SuperCode
if exist .venv\Scripts\activate.bat call .venv\Scripts\activate.bat
if not exist frontend\dist\index.html echo [WARNING] The UI is not built yet: run install.bat first.
echo DOINg.AI SuperCode on http://localhost:4001  (Ctrl+C to stop)
python server.py --open %*
