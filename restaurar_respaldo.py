"""Restauración local de un respaldo íntegro con copia previa de la base actual."""
import os,sqlite3,zipfile,tempfile,shutil
from pathlib import Path
from datetime import datetime

def restore(source,data):
    source=Path(source).expanduser().resolve();data=Path(data).resolve()
    if not source.is_file():raise ValueError('No existe el archivo seleccionado.')
    data.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(source) as z,tempfile.TemporaryDirectory(prefix='museograma_recuperar_') as tmp:
        names=z.namelist()
        if 'coleccion.sqlite3' not in names:raise ValueError('El ZIP no contiene una base de Museograma.')
        if sum(e.file_size for e in z.infolist())>2*1024**3:raise ValueError('El respaldo supera 2 GB descomprimido.')
        candidate=Path(tmp)/'coleccion.sqlite3';candidate.write_bytes(z.read('coleccion.sqlite3'))
        with sqlite3.connect(candidate) as c:
            if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('La base del respaldo está dañada.')
            tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {'pieces','accounts','catalogs'}<=tables:raise ValueError('No es una base válida de Museograma.')
            unknown=[r[0] for r in c.execute('SELECT username FROM accounts') if r[0] not in ('Coleccion_Web_Programador','AdminETG_176463')]
            if unknown:raise ValueError('El respaldo incluye cuentas distintas a las dos autorizadas en esta edición.')
        c.close()
        target=data/'coleccion.sqlite3'
        if target.exists():
            previous=data/'respaldos'/('antes_de_restaurar_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.sqlite3')
            previous.parent.mkdir(exist_ok=True)
            con=sqlite3.connect(target);dest=sqlite3.connect(previous)
            try:con.backup(dest)
            finally:con.close();dest.close()
        staged=data/'restauracion_pendiente.sqlite3';shutil.copyfile(candidate,staged)
        # Whitelist legacy photo paths; never extract arbitrary ZIP paths.
        photos=[]
        for name in names:
            parts=name.split('/')
            if len(parts)==2 and parts[0]=='fotografias' and parts[1] not in ('','.','..') and '\\' not in parts[1] and ':' not in parts[1] and parts[1].lower().endswith(('.jpg','.jpeg','.png','.webp')):
                photos.append((parts[1],z.read(name)))
        os.replace(staged,target)
        for name,content in photos:
            folder=data/'fotografias';folder.mkdir(exist_ok=True);(folder/name).write_bytes(content)
    print('Respaldo recuperado. La base anterior se conserva en datos/respaldos.')

if __name__=='__main__':
    try:
        print('Cierre Museograma y su ventana de servidor antes de continuar.')
        path=input('Ruta del ZIP de respaldo: ').strip().strip('"')
        restore(path,os.environ.get('MUSEOGRAMA_DATA') or Path(__file__).parent/'datos')
    except Exception as exc:print('No se pudo restaurar:',exc);raise SystemExit(1)
