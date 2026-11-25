"""Limpia una sola vez la espera de solicitudes; conserva cuentas, códigos e inventario."""
import os,sqlite3
from pathlib import Path
folder=Path(os.environ.get('MUSEOGRAMA_DATA') or Path(__file__).resolve().parent/'datos')
p=folder/'coleccion.sqlite3'
if not p.is_file():raise SystemExit('Coloca este archivo junto a app.py de tu instalación. No se encontró la base existente.')
with sqlite3.connect(p.as_uri()+'?mode=rw',uri=True) as c:
    c.execute('DELETE FROM code_limits')
print('Espera reiniciada. Se conservaron cuentas, códigos de verificación e inventario.')
