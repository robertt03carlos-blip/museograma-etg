"""Abre el servidor existente o inicia Museograma sin duplicar el puerto."""
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.request import urlopen
import webbrowser

def main():
    root=Path(__file__).resolve().parent
    url='http://127.0.0.1:'+str(os.environ.get('PORT','8765'))
    try:
        with urlopen(url+'/api/session',timeout=2) as response:
            status=json.loads(response.read())
        if isinstance(status,dict) and {'owner','csrf','configured'}<=status.keys():
            webbrowser.open(url)
            print('Museograma ya estaba abierto. Se abrio la pagina del programa.')
            return 0
    except (OSError,ValueError):
        pass
    return subprocess.call([sys.executable,str(root/'app.py')],cwd=root)

if __name__=='__main__':
    raise SystemExit(main())
