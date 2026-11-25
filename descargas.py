"""Descargas privadas en segundo plano con progreso y reutilización de resultados."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from threading import Lock
import hashlib
import json
import secrets
import sqlite3
import time
import zipfile
from flask import abort, jsonify, request, send_file, session
from integracion import export_inventory


def register_downloads(app,data,admin):
    folder=Path(data)/'descargas';folder.mkdir(exist_ok=True)
    database=Path(data)/'coleccion.sqlite3'
    jobs={};lock=Lock();pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='museograma-descarga')

    def fingerprint(kind,name):
        h=hashlib.sha256();h.update((kind+'\0'+name).encode())
        if kind=='backup':
            stat=database.stat();h.update(str((stat.st_mtime_ns,stat.st_size)).encode())
            for path in sorted((Path(data)/'fotografias').glob('*.jpg')):
                stat=path.stat();h.update(str((path.name,stat.st_size,stat.st_mtime_ns)).encode())
        else:
            with closing(sqlite3.connect(database)) as c:
                for table,query,args in [
                    ('pieces','SELECT id,payload FROM pieces ORDER BY id',()),
                    ('photographs','SELECT piece,digest FROM photographs ORDER BY piece',()),
                    ('units','SELECT * FROM units ORDER BY id',()),
                    ('movements','SELECT * FROM movements ORDER BY id',()),
                    ('conservation','SELECT * FROM conservation ORDER BY id',()),
                    ('exhibitions','SELECT * FROM exhibitions ORDER BY id',())]:
                    h.update(table.encode())
                    for row in c.execute(query,args):h.update(json.dumps(row,ensure_ascii=False).encode())
        return h.hexdigest()

    def update(job,stage,done=None,total=None):
        with lock:job.update(stage=stage,done=done,total=total)

    def work(job):
        snapshot=folder/(job['id']+'.sqlite3')
        try:
            update(job,'Creando una copia consistente del inventario')
            with closing(sqlite3.connect(database)) as source,closing(sqlite3.connect(snapshot)) as target:source.backup(target,pages=512,progress=lambda status,remaining,total:update(job,'Copiando datos y fotografías',total-remaining,total))
            if job['kind'] in ('excel','word'):
                with closing(sqlite3.connect(snapshot)) as c:
                    records=[json.loads(row[0]) for row in c.execute('SELECT payload FROM pieces ORDER BY id')]
                    records=[p for p in records if not job['catalog'] or p.get('catalog')==job['catalog']]
                    callback=lambda stage,done=None,total=None:update(job,stage,done,total)
                    if job['kind']=='word':
                        from catalogo_word import build_catalog
                        photos=dict(c.execute('SELECT piece,content FROM photographs'))
                        events=[json.loads(row[0]) for row in c.execute('SELECT payload FROM exhibitions')] if c.execute("SELECT 1 FROM sqlite_master WHERE name='exhibitions'").fetchone() else None
                        build_catalog(Path(app.root_path)/'plantillas/catalogo_base.docx',records,photos,job['path'],on_progress=callback,exhibitions=events)
                    else:
                        export_inventory(records,c,job['path'],on_progress=callback)
            else:
                files=[(snapshot,'coleccion.sqlite3')]+[(p,'fotografias/'+p.name) for p in sorted((Path(data)/'fotografias').glob('*.jpg'))]
                with zipfile.ZipFile(job['path'],'w',zipfile.ZIP_DEFLATED,compresslevel=1) as z:
                    for index,(path,name) in enumerate(files,1):
                        update(job,'Empaquetando el respaldo sin eliminar fotografías',index-1,len(files));z.write(path,name)
                    update(job,'Comprobando el archivo de respaldo',len(files),len(files))
            with lock:job.update(state='ready',stage='Archivo listo para descargar',size=job['path'].stat().st_size)
        except ValueError as error:
            with lock:job.update(state='error',stage=str(error))
        except Exception:
            app.logger.exception('No se pudo preparar una descarga de Museograma')
            with lock:job.update(state='error',stage='No se pudo preparar el archivo. Inténtalo de nuevo; tu colección se conserva.')
        finally:snapshot.unlink(missing_ok=True)

    @app.post('/api/downloads',endpoint='downloads_start')
    @admin
    def start():
        d=request.get_json(silent=True) or {};kind=d.get('kind');name=str(d.get('catalog','')).strip()
        if kind not in ('excel','backup','word'):abort(400,description='Seleccione inventario Excel, catálogo Word o respaldo.')
        if kind=='excel' and name:
            with closing(sqlite3.connect(database)) as c:
                if not c.execute('SELECT 1 FROM catalogs WHERE name=?',(name,)).fetchone():abort(404)
        else:name=''
        signature=fingerprint(kind,name);owner=session['username']
        with lock:
            for job in jobs.values():
                if job['owner']==owner and job['kind']==kind and job['catalog']==name and (job['state']=='working' or (job['signature']==signature and job['state']=='ready' and job['path'].exists())):
                    return jsonify(id=job['id'],reused=True)
            if sum(j['state']=='working' for j in jobs.values())>=4:abort(429,description='Ya se están preparando varios archivos. Espera a que termine uno.')
            ident=secrets.token_urlsafe(24);suffix='.xlsx' if kind=='excel' else '.docx' if kind=='word' else '.zip'
            filename=('Inventario_'+''.join(c if c.isalnum() or c in '-_' else '_' for c in (name or 'Coleccion_completa'))[:60]+suffix) if kind=='excel' else 'Museograma_respaldo.zip'
            if kind=='word':filename='Catalogo_Tecnico_Coleccion_Actualizado.docx'
            job=dict(id=ident,owner=owner,kind=kind,catalog=name,signature=signature,state='working',stage='Preparando la descarga',done=None,total=None,path=folder/(ident+suffix),filename=filename,created=time.time())
            jobs[ident]=job
        pool.submit(work,job)
        return jsonify(id=ident,reused=False)

    def get_job(ident):
        with lock:
            job=jobs.get(ident)
            if not job or job['owner']!=session['username']:abort(404)
            return dict(job)

    @app.get('/api/downloads/<ident>',endpoint='downloads_status')
    @admin
    def status(ident):
        job=get_job(ident)
        return jsonify(state=job['state'],stage=job['stage'],done=job['done'],total=job['total'],size=job.get('size'),filename=job['filename'])

    @app.get('/api/downloads/<ident>/file',endpoint='downloads_file')
    @admin
    def file(ident):
        job=get_job(ident)
        if job['state']!='ready' or not job['path'].is_file():abort(409,description='El archivo todavía se está preparando.')
        mime={'backup':'application/zip','excel':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','word':'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}[job['kind']]
        return send_file(job['path'],as_attachment=True,download_name=job['filename'],mimetype=mime)
