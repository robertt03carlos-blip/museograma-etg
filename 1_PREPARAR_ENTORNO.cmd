.@echo off
setlocal
if not exist "%~dp0preparar_entorno.py" (
  echo Falta preparar_entorno.py. Extrae TODO el ZIP antes de continuar.
  pause
  exit /b 1
)
where py >nul 2>nul
if errorlevel 1 (
  python "%~dp0preparar_entorno.py"
) else (
  py -3 "%~dp0preparar_entorno.py"
)
if errorlevel 1 (
  echo No se completo la preparacion. Revisa los mensajes anteriores.
  pause
  exit /b 1
)
pause
