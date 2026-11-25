"""Recorridos, documentación, galerías y seguimiento de la colección."""
import io,json,sqlite3
from datetime import datetime,timezone,date
from pathlib import Path
from flask import request,session,jsonify,render_template,abort,send_file

ROUTES=[{'slug': 'expropiacion', 'title': 'La expropiación petrolera', 'intro': 'Fotografías, documentos y objetos relacionados con la expropiación petrolera de 1938 y la creación de PEMEX.', 'themes': ['Expropiación petrolera', 'Fundación y consolidación de PEMEX'], 'question': '¿Qué información aporta cada pieza sobre la expropiación petrolera?'}, {'slug': 'hogar', 'title': 'Productos del petróleo en la vida diaria', 'intro': 'Envases, lubricantes y otros productos que muestran los usos del petróleo en el hogar, el transporte y las actividades diarias.', 'themes': ['Productos y vida cotidiana'], 'question': '¿Para qué se utilizaba esta pieza y quién la usaba?'}, {'slug': 'trabajo', 'title': 'Cultura laboral petrolera', 'intro': 'Credenciales, uniformes, distintivos y documentos relacionados con el trabajo y la vida de los trabajadores de PEMEX.', 'themes': ['Cultura laboral petrolera', 'Sindicalismo y relaciones laborales'], 'question': '¿Qué nos cuenta esta pieza sobre el trabajo en PEMEX?'}, {'slug': 'imagen', 'title': 'Marcas y publicidad petrolera', 'intro': 'Anuncios, logotipos y artículos promocionales de PEMEX y de otras compañías petroleras presentes en la colección.', 'themes': ['Publicidad y propaganda', 'Identidad visual y promocional'], 'question': '¿Qué marca aparece y qué producto o servicio anuncia?'}, {'slug': 'antes', 'title': 'Pre-PEMEX', 'intro': 'Objetos y documentos clasificados como Pre-PEMEX en el inventario. Esta clasificación debe revisarse con la fecha y la información de cada pieza.', 'themes': [], 'question': '¿A qué compañía o etapa anterior a PEMEX pertenece esta pieza?'}]

ROUTES.extend([{'slug':'industria','title':'Industria e infraestructura petrolera','intro':'Instalaciones, equipos, autotanques y objetos de la operación petrolera.','themes':['Industria y operación petrolera','Transporte y distribución','Infraestructura y refinación'],'question':'¿Cómo se extraía, transformaba y transportaba el petróleo?'},{'slug':'memoria','title':'Memoria e identidad de PEMEX','intro':'Objetos conmemorativos, aniversarios, publicaciones y reconocimientos.','themes':['Memoria e identidad de PEMEX','Memoria institucional y conmemoraciones','Filatelia y documentación postal','Identidad visual y promocional'],'question':'¿Qué memoria institucional conserva esta pieza?'}])

def register(app,db,items,item,put,admin,audit,photograph):
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS pending_originals(token TEXT NOT NULL,piece INTEGER NOT NULL,content BLOB NOT NULL,mime TEXT NOT NULL,PRIMARY KEY(token,piece));
        CREATE TABLE IF NOT EXISTS gallery(id INTEGER PRIMARY KEY AUTOINCREMENT,piece INTEGER NOT NULL,caption TEXT NOT NULL,role TEXT NOT NULL,content BLOB NOT NULL,thumbnail BLOB NOT NULL,original BLOB NOT NULL,mime TEXT NOT NULL,created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS original_photographs(piece INTEGER PRIMARY KEY,content BLOB NOT NULL,mime TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS units(id INTEGER PRIMARY KEY AUTOINCREMENT,piece INTEGER NOT NULL,code TEXT NOT NULL,location TEXT NOT NULL,condition TEXT NOT NULL,notes TEXT NOT NULL,UNIQUE(piece,code));
        CREATE TABLE IF NOT EXISTS movements(id INTEGER PRIMARY KEY AUTOINCREMENT,piece INTEGER NOT NULL,unit INTEGER,origin TEXT NOT NULL,destination TEXT NOT NULL,note TEXT NOT NULL,occurred TEXT NOT NULL,username TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS conservation(id INTEGER PRIMARY KEY AUTOINCREMENT,piece INTEGER NOT NULL,reviewed TEXT NOT NULL,condition TEXT NOT NULL,damage TEXT NOT NULL,action TEXT NOT NULL,username TEXT NOT NULL);
        ''')
    def now():return datetime.now(timezone.utc).isoformat()
    def visible(pid):
        p=item(pid)
        if not p or (p.get('visibility')!='visible' and not session.get('owner')):abort(404)
        return p
    def required(pid):
        p=item(pid)
        if not p:abort(404)
        return p
    def text(d,k,maxlen=16000):
        v=str(d.get(k,'') or '').strip()
        if len(v)>maxlen:raise ValueError('El campo '+k+' es demasiado largo.')
        return v
    def gallery(pid):
        with db() as c:return [dict(id=r[0],caption=r[1],role=r[2]) for r in c.execute('SELECT id,caption,role FROM gallery WHERE piece=? ORDER BY id',(pid,))]
    def related(p):
        candidates=[q for q in items() if q['id']!=p['id'] and q.get('visibility')=='visible']
        candidates.sort(key=lambda q:(-(4*(q.get('narrative','').strip()==p.get('narrative','').strip())+2*(q.get('maker')==p.get('maker') and p.get('maker') not in ('','No identificado'))+(q.get('category')==p.get('category'))),q['id']))
        return candidates[:4]
    @app.context_processor
    def context():
        return dict(commercial=request.args.get('modo')=='comercial',gallery_for=gallery,related_for=related,owner=bool(session.get('owner')))
    def route_pieces(route):
        ps=[p for p in items() if p.get('visibility')=='visible' and (p.get('pre')=='pre' if route['slug']=='antes' else p.get('narrative','').strip() in route['themes'])]
        ps.sort(key=lambda p:(p.get('top100Rank') or 9999,p['id']))
        return ps
    @app.get('/recorridos')
    def tours():return render_template('recorridos.html',routes=ROUTES)
    @app.get('/recorrido/<slug>')
    def tour(slug):
        r=next((r for r in ROUTES if r['slug']==slug),None)
        if not r:abort(404)
        return render_template('recorrido.html',route=r,pieces=route_pieces(r))
    @app.get('/cronologia')
    def chronology():
        ps=[p for p in items() if p.get('visibility')=='visible']
        dated=sorted([p for p in ps if p.get('yearStart')],key=lambda p:(p['yearStart'],p['id']))
        return render_template('cronologia.html',pieces=dated,pending=len(ps)-len(dated))
    @app.get('/api/items/<int:pid>/gallery')
    def list_gallery(pid):visible(pid);return jsonify(photos=gallery(pid))
    @app.post('/api/items/<int:pid>/gallery')
    @admin
    def add_gallery(pid):
        required(pid);f=request.files.get('photo')
        if not f:raise ValueError('Seleccione una fotografía.')
        raw=f.read(8*1024*1024+1)
        if len(raw)>8*1024*1024:raise ValueError('La fotografía supera 8 MB.')
        try:content,thumb,digest=photograph(raw)
        except Exception as exc:raise ValueError('La fotografía no es válida.') from exc
        from PIL import Image
        mime=Image.MIME.get(Image.open(io.BytesIO(raw)).format,'application/octet-stream')
        role=text(request.form,'role',100) or 'Detalle';caption=text(request.form,'caption',1000)
        if role not in ('Frente','Reverso','Firma o sello','Etiqueta','Deterioro','Detalle','Otra'):raise ValueError('Tipo de fotografía no válido.')
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute('SELECT COUNT(*) FROM gallery WHERE piece=?',(pid,)).fetchone()[0]>=40:raise ValueError('Máximo 40 fotografías adicionales por registro.')
            cur=c.execute('INSERT INTO gallery(piece,caption,role,content,thumbnail,original,mime,created) VALUES(?,?,?,?,?,?,?,?)',(pid,caption,role,content,thumb,raw,mime,now()))
            audit(c,'Fotografía adicional',str(pid))
        return jsonify(ok=True,id=cur.lastrowid)
    @app.get('/galeria/<int:photo>')
    def gallery_image(photo):
        with db() as c:r=c.execute('SELECT piece,content,thumbnail,original,mime FROM gallery WHERE id=?',(photo,)).fetchone()
        if not r:abort(404)
        visible(r[0]);original=request.args.get('original')=='1'
        if original and not session.get('owner'):abort(401)
        return send_file(io.BytesIO(r[3] if original else r[2] if request.args.get('miniatura')=='1' else r[1]),mimetype=r[4] if original else 'image/jpeg',as_attachment=original,download_name='original_'+str(photo))
    @app.delete('/api/gallery/<int:photo>')
    @admin
    def delete_gallery(photo):
        app.backup()
        with db() as c:
            r=c.execute('SELECT piece FROM gallery WHERE id=?',(photo,)).fetchone()
            if not r:abort(404)
            c.execute('DELETE FROM gallery WHERE id=?',(photo,));audit(c,'Fotografía adicional eliminada',str(r[0]))
        return jsonify(ok=True)
    @app.get('/api/items/<int:pid>/original')
    @admin
    def original_photo(pid):
        required(pid)
        with db() as c:r=c.execute('SELECT content,mime FROM original_photographs WHERE piece=?',(pid,)).fetchone()
        if not r:abort(404)
        return send_file(io.BytesIO(r[0]),mimetype=r[1],as_attachment=True,download_name='original_pieza_'+str(pid)+{'image/jpeg':'.jpg','image/png':'.png','image/webp':'.webp'}.get(r[1],'.jpg'))
    def records(table,pid):
        with db() as c:
            c.row_factory=sqlite3.Row
            return [dict(r) for r in c.execute('SELECT * FROM '+table+' WHERE piece=? ORDER BY id DESC',(pid,)) if table=='units' or r['username']!='Coleccion_Web_Programador' or session.get('username')=='Coleccion_Web_Programador']
    @app.get('/gestion/<int:pid>')
    @admin
    def management(pid):
        piece=required(pid);units=records('units',pid)
        locations={u['location'] or 'Sin registrar' for u in units}
        if len(units)<piece['quantity']:locations.add(piece.get('location') or 'Sin registrar')
        current_location=next(iter(locations)) if len(locations)==1 else 'Las piezas están en lugares diferentes; revisa sus etiquetas.' if locations else piece.get('location') or 'Sin registrar'
        with db() as c:has_original=bool(c.execute('SELECT 1 FROM original_photographs WHERE piece=?',(pid,)).fetchone())
        return render_template('gestion.html',piece=piece,units=units,current_location=current_location,has_original=has_original,today=date.today().isoformat(),movements=records('movements',pid),reviews=records('conservation',pid),photos=gallery(pid))
    @app.post('/api/items/<int:pid>/units')
    @admin
    def add_unit(pid):
        p=required(pid);d=request.get_json() or {};code=text(d,'code',100)
        if not code:raise ValueError('Escribe una etiqueta para distinguir esta pieza.')
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            p=json.loads(c.execute('SELECT payload FROM pieces WHERE id=?',(pid,)).fetchone()[0])
            if c.execute('SELECT COUNT(*) FROM units WHERE piece=?',(pid,)).fetchone()[0]>=p['quantity']:raise ValueError('Los ejemplares identificados no pueden superar las existencias.')
            try:c.execute('INSERT INTO units(piece,code,location,condition,notes) VALUES(?,?,?,?,?)',(pid,code,text(d,'location',1000),text(d,'condition',1000),text(d,'notes')))
            except sqlite3.IntegrityError:raise ValueError('Ya usaste esa etiqueta en esta pieza.')
            audit(c,'Ejemplar identificado',str(pid)+' / '+code)
        return jsonify(ok=True)
    @app.post('/api/items/<int:pid>/movements')
    @admin
    def move(pid):
        required(pid);d=request.get_json() or {};destination=text(d,'destination',1000)
        if not destination:raise ValueError('Escriba dónde está guardada ahora la pieza.')
        unit=d.get('unit') or None
        with db() as c:
            c.execute('BEGIN IMMEDIATE');p=json.loads(c.execute('SELECT payload FROM pieces WHERE id=?',(pid,)).fetchone()[0])
            if p['quantity']<=0:raise ValueError('No hay piezas en existencia para cambiar de lugar.')
            if unit:
                row=c.execute('SELECT location FROM units WHERE id=? AND piece=?',(unit,pid)).fetchone()
                if not row:raise ValueError('La etiqueta seleccionada no pertenece a esta pieza.')
                origin=row[0];c.execute('UPDATE units SET location=? WHERE id=?',(destination,unit))
            else:
                origin=p.get('location','');p['location']=destination;put(c,p)
                c.execute('UPDATE units SET location=? WHERE piece=?',(destination,pid))
            if session['username']!='Coleccion_Web_Programador':c.execute('INSERT INTO movements(piece,unit,origin,destination,note,occurred,username) VALUES(?,?,?,?,?,?,?)',(pid,unit,origin,destination,text(d,'note'),now(),session['username']))
            audit(c,'Traslado de pieza',str(pid))
        return jsonify(ok=True)
    @app.post('/api/items/<int:pid>/conservation')
    @admin
    def review(pid):
        required(pid);d=request.get_json() or {};reviewed=text(d,'reviewed',10);condition=text(d,'condition',1000)
        try:date.fromisoformat(reviewed)
        except ValueError:raise ValueError('Capture una fecha de revisión válida.')
        if not condition:raise ValueError('Indique el estado observado.')
        with db() as c:
            c.execute('BEGIN IMMEDIATE');p=json.loads(c.execute('SELECT payload FROM pieces WHERE id=?',(pid,)).fetchone()[0]);p['condition']=condition;put(c,p)
            if session['username']!='Coleccion_Web_Programador':c.execute('INSERT INTO conservation(piece,reviewed,condition,damage,action,username) VALUES(?,?,?,?,?,?)',(pid,reviewed,condition,text(d,'damage'),text(d,'action'),session['username']))
            audit(c,'Revisión de conservación',str(pid))
        return jsonify(ok=True)
    def issues(p):
        out=[]
        for key,label in [('image','Fotografía principal'),('material','Material'),('narrative','Tema'),('description','Descripción de la pieza')]:
            if not p.get(key):out.append(label+' pendiente')
        if p.get('visibility')!='visible':out.append('Publicación pendiente')
        if p.get('category')=='Pendiente de clasificación':out.append('Clasificación pendiente')
        with db() as c:
            if c.execute('SELECT COUNT(*) FROM units WHERE piece=?',(p['id'],)).fetchone()[0]>p['quantity']:out.append('Revisar ejemplares tras cambio de existencias')
        return out
    @app.get('/calidad')
    @admin
    def quality():
        ps=items();rs=[dict(piece=p,issues=issues(p)) for p in ps];rs=[r for r in rs if r['issues']]
        from collections import Counter
        counts=Counter(v for r in rs for v in r['issues'])
        return render_template('calidad.html',rows=rs,counts=counts,total=len(ps),units=sum(p['quantity'] for p in ps))

