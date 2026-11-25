@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Ejecuta primero 1_PREPARAR_ENTORNO.cmd.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" restaurar_respaldo.py
pause
