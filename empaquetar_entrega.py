"""Crea una copia compartible sin cachés ni configuración privada de correo."""
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3,zipfile
from contextlib import closing
root=Path(__file__).resolve().parent.parent
out=root.parent/(root.name+'.zip')
excluded={'.venv','__pycache__','.git','.vscode','Verificacion','Catalogo_Word','respaldos','descargas','excel','grabacion'}
with TemporaryDirectory() as folder:
    snapshot=Path(folder)/'coleccion.sqlite3'
    with closing(sqlite3.connect(root/'Museograma_VSCode/datos/coleccion.sqlite3')) as a,closing(sqlite3.connect(snapshot)) as b:a.backup(b)
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for p in root.rglob('*'):
            rel=p.relative_to(root)
            if not p.is_file() or any(x in excluded for x in rel.parts):continue
            if p.name in {'.env','clave_sesion.txt'} or p.suffix in {'.log','.pyc'}:continue
            if p.name=='coleccion.sqlite3':continue
            z.write(p,root.name+'/'+rel.as_posix())
        z.write(snapshot,root.name+'/Museograma_VSCode/datos/coleccion.sqlite3')
print('Copia lista: '+str(out))
