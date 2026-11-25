@echo off
setlocal
set PORT=8765
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo Ejecuta primero 1_PREPARAR_ENTORNO.cmd.
  pause
  exit /b 1
)
"%~dp0.venv\Scripts\python.exe" "%~dp0abrir_museograma.py"
if errorlevel 1 pause
