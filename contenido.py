"""Contenido público y exposiciones editables, conservando el inventario."""
import json,re,secrets
from datetime import date
from pathlib import Path
from flask import request,session,render_template,jsonify,abort,send_file
import io

def register_content(app,db,items,admin,csrf,audit):
    with db() as c:
        c.execute('CREATE TABLE IF NOT EXISTS exhibitions(id TEXT PRIMARY KEY,payload TEXT NOT NULL)')
        c.execute('CREATE TABLE IF NOT EXISTS deleted_exhibitions(id TEXT PRIMARY KEY)')
        c.execute('CREATE TABLE IF NOT EXISTS deleted_exhibition_photos(event TEXT NOT NULL,photo TEXT NOT NULL,PRIMARY KEY(event,photo))')
        c.execute('CREATE TABLE IF NOT EXISTS exhibition_photos(id INTEGER PRIMARY KEY AUTOINCREMENT,event TEXT NOT NULL,content BLOB NOT NULL)')
        # Fuente editable por el desarrollador: config/exposiciones_iniciales.json.
        # En cada inicio sincroniza los campos de contenido y orden. Conserva fotos
        # agregadas y piezas vinculadas que ya existan en la base local.
        source_events=json.loads((Path(app.root_path)/'config/exposiciones_iniciales.json').read_text(encoding='utf-8'))
        for e in source_events:
            if c.execute('SELECT 1 FROM deleted_exhibitions WHERE id=?',(e['id'],)).fetchone():continue
            row=c.execute('SELECT payload FROM exhibitions WHERE id=?',(e['id'],)).fetchone()
            previous=json.loads(row[0]) if row else {}
            deleted={r[0] for r in c.execute('SELECT photo FROM deleted_exhibition_photos WHERE event=?',(e['id'],))}
            defaults=[x for x in e.get('photos',[]) if x not in deleted]
            previous_photos=[x for x in previous.get('photos',[]) if x not in deleted]
            synced=dict(e)
            # Preserva orden editable anterior; agrega fotos de configuración que aún no estaban.
            synced['photos']=list(dict.fromkeys(previous_photos+defaults))
            synced['pieceIds']=previous.get('pieceIds',e.get('pieceIds',[]))
            c.execute('INSERT INTO exhibitions VALUES(?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',(e['id'],json.dumps(synced,ensure_ascii=False)))
    def events(private=False):
        with db() as c:result=[json.loads(x[0]) for x in c.execute('SELECT payload FROM exhibitions')]
        return sorted([e for e in result if private or e.get('published')],key=lambda e:(e.get('sort_order') if isinstance(e.get('sort_order'),int) else 9999, e.get('date','')))
    app.collection_events=events
    def public_items():return [p for p in items() if p.get('visibility')=='visible']
    @app.context_processor
    def collection_context():
        ps=public_items();pid=request.view_args.get('pid') if request.view_args else None
        navigation=None
        if pid and request.endpoint=='piece_page':
            ordered=sorted(ps,key=lambda p:p['id']);back='/'+'#coleccion';label='Volver a la colección'
            slug=request.args.get('tema','')
            if slug:
                from mejoras import ROUTES
                route=next((r for r in ROUTES if r['slug']==slug),None)
                if route:
                    ordered=[p for p in ps if p.get('pre')=='pre'] if slug=='antes' else [p for p in ps if p.get('narrative','').strip() in route['themes']]
                    ordered.sort(key=lambda p:(p.get('top100Rank') or 9999,p['id']));back='/recorrido/'+slug+'#parada-1';label='Volver al tema'
            found=next((i for i,p in enumerate(ordered) if p['id']==pid),None)
            if found is not None:
                suffix='?tema='+slug if slug else ''
                navigation=dict(back=back,label=label,previous=('/pieza/'+str(ordered[found-1]['id'])+suffix if found else ''),next=('/pieza/'+str(ordered[found+1]['id'])+suffix if found+1<len(ordered) else ''))
        hero=[]
        for word in ['Bomba de','Autotanque','Lata','Sello','Casco']:
            p=next((p for p in ps if p.get('image') and word.casefold() in p.get('name','').casefold() and p not in hero),None)
            if p:hero.append(p)
        hero+=( [p for p in ps if p.get('image') and p not in hero][:5-len(hero)] )
        acquisitions=sorted([p for p in ps if p.get('acquisitionDate')],key=lambda p:(p['acquisitionDate'],p['id']),reverse=True)[:10]
        highlights=[]
        for pid,title,href in [(145,'Latas y lubricantes','/buscar?q=lata'),(180,'Fotografías históricas','/buscar?q=fotograf%C3%ADa'),(50,'Publicidad y propaganda','/recorrido/imagen'),(next((p['id'] for p in ps if 'cascos de seguridad' in p['name'].casefold()),0),'Cultura laboral petrolera','/recorrido/trabajo')]:
            selected=next((p for p in ps if p['id']==pid and p.get('image')),None)
            if selected:highlights.append(dict(title=title,href=href,image=selected['image']))
        return dict(home_highlights=highlights,home_photos=hero,new_acquisitions=acquisitions,public_events=events(),piece_navigation=navigation,piece_events=[e for e in events() if pid in e.get('pieceIds',[])],content_csrf=csrf() if session.get('owner') else '')
    @app.get('/buscar')
    def search_collection():
        import unicodedata
        def norm(v):return ''.join(c for c in unicodedata.normalize('NFD',str(v).casefold()) if unicodedata.category(c)!='Mn')
        q=request.args.get('q','').strip()[:200];words=norm(q).split()
        results=[p for p in public_items() if all(word in norm(' '.join(str(p.get(k,'')) for k in ('name','maker','period','narrative','category','subcategory','description','curatorial','observations'))) for word in words)] if words else []
        return render_template('buscar.html',results=results,query=q)
    @app.get('/exposiciones')
    def exhibitions():
        return render_template('exposiciones.html',events=events(),available={p['id']:p for p in public_items()},editing=False)
    @app.get('/exposiciones/gestionar')
    @admin
    def edit_exhibitions():
        return render_template('exposiciones.html',events=events(True),available={p['id']:p for p in public_items()},editing=True)
    @app.post('/api/exhibitions')
    @admin
    def save_exhibition():
        d=request.get_json() or {};eid=str(d.get('id') or secrets.token_hex(6))
        if not re.fullmatch(r'[a-zA-Z0-9-]{1,80}',eid):raise ValueError('Identificador no válido.')
        e={k:str(d.get(k,'') or '').strip() for k in ('title','date','endDate','place','description','source','youtube_url','external_url')}
        if not e['title'] or not e['description']:raise ValueError('Completa título y descripción.')
        if any(len(v)>16000 for v in e.values()):raise ValueError('Texto demasiado largo.')
        if e.get('external_url'):
            from urllib.parse import urlparse
            ext=e['external_url'].strip()
            if not re.match(r'^https?://',ext,flags=re.I): ext='https://'+ext
            parsed_ext=urlparse(ext)
            if parsed_ext.scheme not in ('http','https') or not parsed_ext.netloc or '.' not in parsed_ext.netloc:
                raise ValueError('La URL externa no parece válida.')
            e['external_url']=ext
        if e.get('youtube_url'):
            from urllib.parse import urlparse,parse_qs
            u=urlparse(e['youtube_url'])
            if u.scheme not in ('https','http') or u.netloc not in ('youtube.com','www.youtube.com','youtu.be','m.youtube.com'):
                raise ValueError('El enlace de video debe ser de YouTube; déjalo vacío si no hay video.')
            vid=(u.path.strip('/').split('/')[-1] if u.netloc=='youtu.be' else parse_qs(u.query).get('v',[''])[0])
            if u.path.startswith('/embed/'):
                vid=u.path.split('/embed/')[-1]
            if not re.fullmatch(r'[A-Za-z0-9_-]{11}',vid):raise ValueError('El enlace de YouTube no parece válido.')
            e['youtube_url']='https://www.youtube.com/watch?v='+vid
        for k in ('date','endDate'):
            if e[k]:
                try:date.fromisoformat(e[k])
                except ValueError:raise ValueError('Fecha no válida.')
        if e['date'] and e['endDate'] and e['endDate']<e['date']:raise ValueError('La fecha final es anterior al inicio.')
        ids=d.get('pieceIds',[])
        if not isinstance(ids,list) or any(type(i)!=int for i in ids):raise ValueError('Indica consecutivos numéricos.')
        known={p['id'] for p in items()}
        if set(ids)-known:raise ValueError('Hay consecutivos que no existen en el inventario.')
        with db() as c:
            old=c.execute('SELECT payload FROM exhibitions WHERE id=?',(eid,)).fetchone();old=json.loads(old[0]) if old else {}
            
            try:order=int(d.get('sort_order') or old.get('sort_order') or 9999)
            except (TypeError,ValueError):raise ValueError('Orden de aparición no válido.')
            if order<1:raise ValueError('El orden debe ser un número positivo.')
            e.update(id=eid,pieceIds=sorted(set(ids)),photos=old.get('photos',[]),published=bool(d.get('published')),sort_order=order,youtube_url=e.get('youtube_url',''))
            c.execute('INSERT INTO exhibitions VALUES(?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',(eid,json.dumps(e,ensure_ascii=False)));audit(c,'Exposición actualizada',e['title'])
        return jsonify(ok=True)

    @app.post('/api/exhibitions/<eid>/photos')
    @admin
    def event_photo_upload(eid):
        from integracion import photograph
        f=request.files.get('photo')
        if not f:raise ValueError('Elige una fotografía.')
        raw=f.read(8*1024*1024+1)
        if len(raw)>8*1024*1024:raise ValueError('La fotografía supera 8 MB.')
        try:content,thumb,digest=photograph(raw)
        except Exception:raise ValueError('El archivo no es una fotografía válida.')
        with db() as c:
            row=c.execute('SELECT payload FROM exhibitions WHERE id=?',(eid,)).fetchone()
            if not row:abort(404)
            e=json.loads(row[0])
            if len(e.get('photos',[]))>=30:raise ValueError('Máximo 30 fotografías por exposición.')
            cur=c.execute('INSERT INTO exhibition_photos(event,content) VALUES(?,?)',(eid,content))
            e.setdefault('photos',[]).append('/exposiciones/foto/'+str(cur.lastrowid))
            c.execute('UPDATE exhibitions SET payload=? WHERE id=?',(json.dumps(e,ensure_ascii=False),eid))
            audit(c,'Fotografía de exposición',eid)
        return jsonify(ok=True)
    @app.post('/api/exhibitions/<eid>/photos/order')
    @admin
    def event_photo_order(eid):
        d=request.get_json() or {};ordered=d.get('photos')
        if not isinstance(ordered,list) or any(not isinstance(x,str) for x in ordered):raise ValueError('Orden de fotografías inválido.')
        with db() as c:
            row=c.execute('SELECT payload FROM exhibitions WHERE id=?',(eid,)).fetchone()
            if not row:abort(404)
            e=json.loads(row[0]);existing=e.get('photos',[])
            if len(ordered)!=len(existing) or set(ordered)!=set(existing):raise ValueError('La galería cambió. Recarga la página e inténtalo de nuevo.')
            e['photos']=ordered;c.execute('UPDATE exhibitions SET payload=? WHERE id=?',(json.dumps(e,ensure_ascii=False),eid));audit(c,'Orden de fotografías actualizado',eid)
        return jsonify(ok=True)
    @app.post('/api/exhibitions/<eid>/photos/delete')
    @admin
    def event_photo_delete(eid):
        d=request.get_json() or {};url=d.get('photo')
        if not isinstance(url,str):raise ValueError('Fotografía no válida.')
        with db() as c:
            row=c.execute('SELECT payload FROM exhibitions WHERE id=?',(eid,)).fetchone()
            if not row:abort(404)
            e=json.loads(row[0]);photos=e.get('photos',[])
            if url not in photos:abort(404)
            e['photos']=[p for p in photos if p!=url]
            c.execute('INSERT OR IGNORE INTO deleted_exhibition_photos(event,photo) VALUES(?,?)',(eid,url))
            c.execute('UPDATE exhibitions SET payload=? WHERE id=?',(json.dumps(e,ensure_ascii=False),eid))
            # Elimina el blob únicamente si se trata de una foto subida a la base;
            # las fotos originales del directorio static solo se retiran de la galería.
            m=re.fullmatch(r'/exposiciones/foto/(\d+)',url)
            if m:c.execute('DELETE FROM exhibition_photos WHERE id=? AND event=?',(int(m.group(1)),eid))
            audit(c,'Fotografía eliminada',eid)
        return jsonify(ok=True)
    @app.delete('/api/exhibitions/<eid>')
    @admin
    def delete_exhibition(eid):
        with db() as c:
            row=c.execute('SELECT payload FROM exhibitions WHERE id=?',(eid,)).fetchone()
            if not row:abort(404)
            e=json.loads(row[0]);c.execute('DELETE FROM exhibition_photos WHERE event=?',(eid,));c.execute('DELETE FROM exhibitions WHERE id=?',(eid,));c.execute('INSERT OR IGNORE INTO deleted_exhibitions(id) VALUES(?)',(eid,));audit(c,'Exposición eliminada',e.get('title',eid))
        return jsonify(ok=True)
    @app.post('/api/exhibitions/<eid>/duplicate')
    @admin
    def duplicate_exhibition(eid):
        with db() as c:
            row=c.execute('SELECT payload FROM exhibitions WHERE id=?',(eid,)).fetchone()
            if not row:abort(404)
            e=json.loads(row[0]);newid=secrets.token_hex(6)
            e['id']=newid;e['title']=(e.get('title','Exposición')+' (copia)')[:500];e['published']=False;e['photos']=[];e['pieceIds']=[];e['sort_order']=9999
            c.execute('INSERT INTO exhibitions VALUES(?,?)',(newid,json.dumps(e,ensure_ascii=False)));audit(c,'Exposición duplicada',e['title'])
        return jsonify(ok=True,id=newid)
    @app.get('/exposiciones/foto/<int:photo>')
    def event_photo(photo):
        with db() as c:row=c.execute('SELECT p.content,e.payload FROM exhibition_photos p JOIN exhibitions e ON p.event=e.id WHERE p.id=?',(photo,)).fetchone()
        if not row or (not json.loads(row[1]).get('published') and not session.get('owner')):abort(404)
        return send_file(io.BytesIO(row[0]),mimetype='image/jpeg')
