@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Primero ejecuta 1_PREPARAR_ENTORNO.cmd.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" "configurar_correo.py"
pause
