@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Ejecuta primero 1_PREPARAR_ENTORNO.cmd.
  pause
  exit /b 1
)
echo Permite consulta desde equipos que tengan acceso a esta red.
echo Configura la contrasena primero desde este equipo.
".venv\Scripts\python.exe" app.py --red
if errorlevel 1 pause
