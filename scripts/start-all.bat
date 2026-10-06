@echo off
chcp 65001 >nul
"%~dp0..\backend\.venv\Scripts\python.exe" "%~dp0start-local.py"
if errorlevel 1 pause
