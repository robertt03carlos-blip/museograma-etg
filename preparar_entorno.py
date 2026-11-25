"""Prepara el proyecto desde su ubicación real, aunque la terminal esté en otra carpeta."""
import argparse
import subprocess
import sys
import venv
from pathlib import Path

ROOT=Path(__file__).resolve().parent
REQUIRED=('app.py','integracion.py','mejoras.py','verificacion.py','requirements.txt',
          'config/acceso_inicial.json','datos_iniciales/catalogo.json',
          'templates/index.html','templates/cuentas.html','static/app.js',
          'static/cuentas.js','static/portada_coleccion.jpg')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--comprobar',action='store_true',help='Comprueba archivos sin instalar ni modificar nada')
    args=parser.parse_args()
    missing=[name for name in REQUIRED if not (ROOT/name).is_file()]
    if missing:
        print('Faltan archivos del proyecto: '+', '.join(missing))
        print('Extrae TODO el ZIP y abre la carpeta que contiene app.py y requirements.txt.')
        return 1
    print('Archivos completos: '+str(ROOT),flush=True)
    if args.comprobar:return 0
    if sys.version_info<(3,11):
        print('Se requiere Python 3.11 o posterior.');return 1
    folder=ROOT/'.venv'
    executable=folder/('Scripts/python.exe' if sys.platform=='win32' else 'bin/python')
    if not executable.is_file():venv.EnvBuilder(with_pip=True).create(folder)
    subprocess.run([str(executable),'-m','pip','install','-r',str(ROOT/'requirements.txt')],cwd=ROOT,check=True)
    print('Listo. Ejecuta 2_ABRIR_MUSEOGRAMA.cmd o usa F5 en Visual Studio Code.')
    return 0

if __name__=='__main__':
    try:raise SystemExit(main())
    except (OSError,subprocess.CalledProcessError) as exc:
        print('No se pudo preparar el entorno: '+str(exc))
        print('Revisa la conexión a internet y los mensajes de instalación anteriores.')
        raise SystemExit(1)
