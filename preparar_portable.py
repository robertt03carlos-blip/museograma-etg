"""Copia consistente de datos e imágenes integradas al portable recién compilado."""
import shutil,sqlite3
from pathlib import Path

base=Path(__file__).parent
source=base/'datos'/'coleccion.sqlite3'
target=base/'dist'/'Museograma'/'datos'
if not source.exists():raise SystemExit('No existe la colección local. Integre primero el inventario.')
if not target.parent.is_dir():raise SystemExit('No existe el directorio del programa compilado.')
target.mkdir(exist_ok=True)
with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as original:
    if original.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise SystemExit('La base de datos requiere revisión.')
    with sqlite3.connect(target/'coleccion.sqlite3') as dest:original.backup(dest)
legacy=base/'datos'/'fotografias'
if legacy.exists():shutil.copytree(legacy,target/'fotografias',dirs_exist_ok=True)
print('Colección y fotografías incorporadas al programa portable.')
