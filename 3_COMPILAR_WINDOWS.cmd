@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Ejecuta primero 1_PREPARAR_ENTORNO.cmd.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install -r requirements_compilar.txt
if errorlevel 1 goto error
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onedir --name Museograma --add-data "templates;templates" --add-data "static;static" --add-data "datos_iniciales;datos_iniciales" --add-data "config;config" app.py
if errorlevel 1 goto error
".venv\Scripts\python.exe" preparar_portable.py
if errorlevel 1 goto error
echo Listo: dist\Museograma\Museograma.exe
echo Copia TODA la carpeta dist\Museograma al equipo de destino.
echo La primera ejecucion crea datos junto al ejecutable. Conserva esa carpeta.
pause
exit /b 0
:error
echo No se pudo compilar. Revisa los mensajes anteriores.
pause
exit /b 1
