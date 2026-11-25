@echo off
where code >nul 2>nul
if errorlevel 1 (
  echo Abre Visual Studio Code y elige Archivo - Abrir carpeta.
  echo Selecciona esta carpeta: %~dp0
  pause
  exit /b 1
)
code "%~dp0Museograma.code-workspace"
