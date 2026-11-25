"""Museograma: consulta de colección y administración del coleccionista."""
from __future__ import annotations
import re, io, json, os, secrets, sqlite3, sys, threading, time, unicodedata, zipfile, hashlib
from datetime import timedelta, datetime, timezone
from functools import wraps
from contextlib import contextmanager, closing
from pathlib import Path
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit
from flask import Flask, request, jsonify, session, abort, render_template, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from PIL import Image, ImageOps
from integracion import init_storage, photograph, store_photo, read_inventory, merge_plan, export_inventory, LIMIT


BASE = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
# La contraseña del paquete anterior se conserva solo como hash con sal.
_initial_access = json.loads((BASE/'config/acceso_inicial.json').read_text(encoding='utf-8'))
DEVELOPER_USER = _initial_access['username']
DEVELOPER_HASH = 'pbkdf2:sha256:100000$'+_initial_access['salt']+'$'+_initial_access['hash']
from verificacion import load_env
load_env(BASE/'.env')
PUBLIC_FIELDS = set(('id name catalog category subcategory period maker dimensions material condition narrative relevance curatorial description observations quantity pre visibility image sources evidence objectType acquisitionDate publicationDate eventDate datingNotes yearStart yearEnd topReason top10 top25 top50 top100 top10Rank top25Rank top50Rank top100Rank top100Position loose pack').split())
COMMERCIAL_FIELDS = {'priceLoose','pricePack','priceLooseState','pricePackState','salePrice','saleQuantity','sale'}

CATEGORIES = ['Energía y Transporte', 'Hogar y vida cotidiana', 'Documentación y Archivo', 'Identidad y Cultura Laboral']
SALE = ['Por confirmar', 'No en venta', 'Disponible', 'Reservada', 'Vendida']
TEXT_FIELDS = ['acquisitionDate','publicationDate','provenance','sources','evidence','objectType','eventDate','datingNotes','location','topReason','name','catalog','category','subcategory','period','maker','dimensions','material','condition','narrative','relevance','curatorial','description','observations','loose','pack','reviewNotes']
PRICE_FIELDS = ['priceLoose','pricePack','salePrice','acquisitionPrice']

def clean_header(v):
    s = ''.join(c for c in unicodedata.normalize('NFD',str(v or '').lower()) if unicodedata.category(c) != 'Mn')
    return ' '.join(''.join(c if c.isalnum() else ' ' for c in s).split())

HEADERS = {
 'procedencia':'provenance','fuentes documentales':'sources','certeza documental':'evidence','tipo de objeto':'objectType','fecha del acontecimiento':'eventDate','notas de datacion':'datingNotes','ubicacion fisica':'location','motivo de seleccion':'topReason','ano inicial':'yearStart','ano final':'yearEnd','consecutivo n pieza':'id','consecutivo n de pieza':'id','consecutivo no pieza':'id','observaciones de validacion':'reviewNotes','consecutivo':'id','identificador':'id','id':'id','nombre':'name','pieza':'name','nombre de la pieza':'name','categoria curatorial':'category','subcategoria':'subcategory',
 'ano aproximado periodo de tiempo':'period','autor fabricante':'maker','dimensiones escala capacidad':'dimensions',
 'material de elaboracion':'material','estado de conservacion':'condition','narratia tematica':'narrative',
 'narrativa tematica':'narrative','relevancia curatorial':'relevance','observaciones curatoriales':'curatorial',
 'cedula de exhibicion':'description','observaciones':'observations','piezas':'quantity','top 100':'top100',
 'top 50':'top50','top 25':'top25','top 10':'top10','pre pemex':'pre','suelto':'loose','en empaque':'pack',
 'precio suelto':'priceLoose','precio en empaque':'pricePack','precio en empaque $':'pricePack',
 'numero consecutivo':'id','no consecutivo':'id','numero de registro':'id','nombre pieza':'name','categoria':'category','cantidad':'quantity','catalogo':'catalog','ruta de imagen':'sourceImage','consecutivo top 100':'top100Position',
 'ano aproximado periodo':'period','ano periodo':'period','periodo':'period','fabricante':'maker','autor':'maker','material':'material','dimensiones':'dimensions','descripcion':'description','cedula':'description','precio de adquisicion':'acquisitionPrice','estado de venta':'sale','disponibilidad comercial':'sale','visibilidad':'visibility','cantidad en venta':'saleQuantity','precio de venta':'salePrice'
}

HEADERS.update({'fecha de adquisicion':'acquisitionDate','fecha de publicacion':'publicationDate'})
HEADERS.update({'version':'catalog','version del catalogo':'catalog','version de inventario':'catalog'})
HEADERS.update({'categoria de la pieza':'category','tema de la pieza':'narrative','importancia historica':'relevance','notas sobre la pieza':'curatorial','descripcion de la pieza':'description','documentos y referencias':'sources','verificacion de la informacion':'evidence','notas sobre la fecha':'datingNotes'})

def parse_catalog_text(text):
    """Extract labeled fields without guessing missing historical information."""
    if not isinstance(text,str) or not text.strip():raise ValueError('Pegue el texto de una pieza.')
    if len(text)>100000:raise ValueError('El texto supera 100 000 caracteres.')
    fields={};warnings=[];unknown=[];conflicts=[];current=None;parts=[]
    def flush():
        if current and parts:
            value='\n'.join(parts).strip()
            if current in fields:
                if fields[current]!=value:
                    conflicts.append(current)
                    unknown.append('Otro valor de '+current+': '+value)
                    warnings.append('Hay valores diferentes para el mismo campo. Revise que no se mezclaron dos piezas.')
            else:fields[current]=value
    for line in text.splitlines():
        line=line.rstrip().removesuffix('\\').rstrip()
        if clean_header(line) in ('registro de catalogo','registro del catalogo'):
            flush();parts=[];current=None;continue
        candidate=re.match(r'^\s*(?:[-*•]\s*)?(?:\*\*)?([^:\n]{1,90}?)\s*(?:\*\*)?\s*[:：]\s*(.*)$',line)
        if candidate:
            flush();parts=[];label=candidate[1].strip().strip('*').strip()
            current=HEADERS.get(clean_header(label))
            if current:parts=[candidate[2].strip().strip('*').strip()]
            else:unknown.append(line);current=None
        elif current:parts.append(line)
        elif line.strip() and clean_header(line) not in ('registro de catalogo','registro del catalogo'):
            unknown.append(line)
    flush()
    fields.pop('id',None) # New text captures always receive a fresh consecutive number.
    fields.pop('sourceImage',None)
    if 'category' in fields:
        category=next((c for c in CATEGORIES if clean_header(fields['category']).endswith(clean_header(c))),None)
        if category:fields['category']=category
        else:
            fields['category']=re.sub(r'^\s*\d+\s*[.\-):]+\s*','',fields['category']).strip()
    for key in PRICE_FIELDS:
        if key in fields:
            try:
                original=fields[key]
                fields[key+'State']='pending' if str(original).strip().lower()=='por confirmar' else 'na' if str(original).strip().lower() in ('n/a','na','no aplica') else 'value'
                cleaned=re.sub(r'(?i)\b(MXN|pesos mexicanos|pesos)\b','',original).strip()
                cleaned=re.sub(r'(?i)\s+(por\s+(?:el\s+)?(?:conjunto|pieza|unidad|lote)|(?:el\s+)?conjunto)\s*$','',cleaned).strip()
                fields[key]=price(cleaned)
                if re.search(r'(?i)\b(conjunto|lote)\b',original):
                    note='Precio de adquisición indicado por el conjunto: '+original if key=='acquisitionPrice' else 'Importe indicado por el conjunto: '+original
                    fields['observations']=(fields.get('observations','')+'\n'+note).strip()
            except ValueError:
                unknown.append('Importe por revisar: '+fields.pop(key));warnings.append('Revise el importe antes de guardar.')
    if 'quantity' in fields:
        try:fields['quantity']=positive_int(fields['quantity'],'Piezas')
        except ValueError:
            unknown.append('Cantidad por revisar: '+fields.pop('quantity'));warnings.append('Revise la cantidad de piezas.')
    if 'pre' in fields:
        value=clean_header(fields['pre'])
        if value in ('si','pre','pre pemex'):fields['pre']='pre'
        elif value in ('no',''):fields['pre']=''
        else:unknown.append('Pre-PEMEX: '+fields.pop('pre'))
    if unknown:warnings.append('Hay texto sin asignar. Se muestra completo para que pueda revisarlo.')
    if not fields:raise ValueError('No se reconocieron campos. Use etiquetas como Nombre de la pieza: y Material: antes de cada dato.')
    return dict(fields=fields,warnings=list(dict.fromkeys(warnings)),unassigned='\n'.join(unknown),conflictingFields=list(dict.fromkeys(conflicts)))

def price(value):
    if value is None or str(value).strip().lower() in ('','n/a','na','no aplica','none','por confirmar'):return None
    if isinstance(value,bool):raise ValueError('El precio debe ser un importe válido.')
    try:
        n=Decimal(str(value).replace('$','').replace(',','').strip())
        if not n.is_finite() or n < 0 or n > 10**12:raise ValueError()
        return float(n.quantize(Decimal('.01')))
    except (InvalidOperation,ValueError):raise ValueError('El precio debe ser un importe positivo o quedar vacío.')

def positive_int(v, label):
    try:
        n=Decimal(str(v))
        if not n.is_finite() or n != n.to_integral_value() or n < 1 or n > 10000000:raise ValueError()
        return int(n)
    except (InvalidOperation,ValueError):raise ValueError(label+' debe ser un entero mayor que cero.')

def nonnegative_int(v, label):
    try:
        n=Decimal(str(v))
        if not n.is_finite() or n != n.to_integral_value() or n < 0 or n > 10000000:raise ValueError()
        return int(n)
    except (InvalidOperation,ValueError):raise ValueError(label+' debe ser un entero igual o mayor que cero.')

def normalized(p, old=None):
    if not isinstance(p,dict):raise ValueError('Registro inválido.')
    out=dict(old or {})
    for k in TEXT_FIELDS:
        out[k]=str(p.get(k,out.get(k,'')) or '').strip()[:16000 if k in ('description','curatorial','observations','sources','narrative') else 1000]
    from datetime import date
    for date_key in ('acquisitionDate','publicationDate'):
        if out.get(date_key):
            try: date.fromisoformat(out[date_key])
            except ValueError: raise ValueError('La fecha debe tener formato AAAA-MM-DD.')
    if not out['name']:raise ValueError('Falta el Nombre de la Pieza.')
    if not out['catalog']:raise ValueError('Falta CATALOGO.')
    if not out['category']:raise ValueError('Falta la categoría de la pieza.')
    if 'top100Position' in p:
        v=p['top100Position'];out['top100Position']='Posible' if str(v).strip().lower()=='posible' else (positive_int(v,'Consecutivo Top 100') if v not in ('',None) else None)
    out['id']=positive_int(p.get('id',out.get('id')),'Consecutivo')
    out['quantity']=nonnegative_int(p.get('quantity',out.get('quantity',1)),'Piezas')
    if old is None and out['quantity']<1:raise ValueError('Una pieza nueva debe iniciar con al menos una unidad.')
    for k in PRICE_FIELDS:
        raw=p.get(k,out.get(k));out[k]=price(raw)
        if k in p:
            raw_state=str(raw or '').strip().lower()
            state=p.get(k+'State')
            if raw_state=='por confirmar':state='pending'
            elif raw_state in ('n/a','na','no aplica'):state='na'
            elif out[k] is not None:state='value'
            elif state not in ('pending','na'):state=out.get(k+'State','') if raw is None else ''
            out[k+'State']=state

    for k in ('yearStart','yearEnd'):
        v=p.get(k,out.get(k))
        out[k]=None if v in ('',None) else positive_int(v,'Año')
        if out[k] is not None and not 1000<=out[k]<=2200:raise ValueError('El año debe estar entre 1000 y 2200.')
    if out['yearStart'] and out['yearEnd'] and out['yearStart']>out['yearEnd']:raise ValueError('El año inicial supera al final.')
    for k,allowed in [('evidence',('','Pendiente de verificar','Atribución probable','Dato documentado')),('objectType',('','Pendiente de identificar','Original','Reproducción','Conmemorativo'))]:
        if out[k] not in allowed:raise ValueError('Valor no válido en '+k+'.')
    if out['evidence']=='Dato documentado' and not out['sources']:raise ValueError('Agregue la fuente que respalda el dato documentado.')
    sq=p.get('saleQuantity',out.get('saleQuantity',0))
    try: out['saleQuantity']=int(sq or 0)
    except (TypeError,ValueError): raise ValueError('Cantidad en venta inválida.')
    if out['saleQuantity']<0 or out['saleQuantity']>out['quantity']:raise ValueError('La cantidad en venta no puede superar las piezas disponibles.')
    out['pre']='pre' if str(p.get('pre',out.get('pre',''))).strip().lower() in ('pre','pre-pemex','pre pemex','si','sí') else ''
    for k in ('top10','top25','top50','top100'):
        rank=p.get(k+'Rank',out.get(k+'Rank'))
        out[k+'Rank']=positive_int(rank,'Posición Top') if rank not in ('',None) else None
        v=p.get(k,out.get(k,False));
        if not isinstance(v,bool) and str(v).strip().isdigit() and int(str(v).strip())>0:out[k+'Rank']=int(str(v).strip())
        out[k]=v is True or (isinstance(v,(int,float)) and not isinstance(v,bool) and v>0) or str(v).strip().lower() in ('si','sí','x','true') or (str(v).strip().isdigit() and int(str(v).strip())>0)
    out['sale']=str(p.get('sale',out.get('sale','No en venta')))
    if out['sale'] not in SALE:raise ValueError('Disponibilidad comercial inválida.')
    out['visibility']=p.get('visibility',out.get('visibility','visible'))
    if out['visibility'] not in ('visible','draft'):raise ValueError('Visibilidad inválida.')
    # Image paths are created only by the validated photo upload endpoint.
    out['image']=out.get('image','')
    return out

def create_app(data_dir=None):
    data=Path(data_dir or os.environ.get('MUSEOGRAMA_DATA') or ((Path(sys.executable).parent if getattr(sys,'frozen',False) else BASE)/'datos'))
    data.mkdir(parents=True,exist_ok=True)
    for d in ('fotografias','respaldos','excel'):(data/d).mkdir(exist_ok=True)
    secret_file=data/'clave_sesion.txt'
    if not secret_file.exists():secret_file.write_text(secrets.token_hex(32), encoding='utf-8')
    app=Flask(__name__,template_folder=str(BASE/'templates'),static_folder=str(BASE/'static'))
    app.config.update(TEMPLATES_AUTO_RELOAD=True,SECRET_KEY=secret_file.read_text(encoding='utf-8').strip(),MAX_CONTENT_LENGTH=LIMIT,
      SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Lax',SESSION_COOKIE_SECURE=os.environ.get('MUSEOGRAMA_HTTPS')=='1',PERMANENT_SESSION_LIFETIME=timedelta(hours=4),DATA_DIR=data)
    db_path=data/'coleccion.sqlite3'
    @contextmanager
    def db():
        con=sqlite3.connect(db_path,timeout=15);con.execute('PRAGMA foreign_keys=ON')
        try:
            with con:yield con
        finally:con.close()
    def setting(k,default=None):
        with db() as c:r=c.execute('SELECT value FROM settings WHERE key=?',(k,)).fetchone()
        return r[0] if r else default
    def has_accounts():
        with db() as c:return bool(c.execute('SELECT 1 FROM accounts LIMIT 1').fetchone())
    def set_setting(c,k,v):c.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(k,v))
    def items():
        with db() as c:return [json.loads(r[0]) for r in c.execute('SELECT payload FROM pieces ORDER BY id')]
    def item(pid):
        with db() as c:r=c.execute('SELECT payload FROM pieces WHERE id=?',(pid,)).fetchone()
        return json.loads(r[0]) if r else None
    def put(c,p):c.execute('INSERT INTO pieces VALUES(?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',(p['id'],json.dumps(p,ensure_ascii=False)))
    with db() as c:
        init_storage(c)
        c.executescript('CREATE TABLE IF NOT EXISTS accounts(username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, recovery_hash TEXT); CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL, action TEXT NOT NULL, detail TEXT, occurred TEXT NOT NULL); CREATE TABLE IF NOT EXISTS visits(day TEXT NOT NULL, piece INTEGER NOT NULL DEFAULT 0, count INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(day,piece)); CREATE TABLE IF NOT EXISTS trash(id INTEGER PRIMARY KEY, payload TEXT NOT NULL, deleted_at TEXT NOT NULL); CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL); CREATE TABLE IF NOT EXISTS pieces(id INTEGER PRIMARY KEY,payload TEXT NOT NULL); CREATE TABLE IF NOT EXISTS catalogs(name TEXT PRIMARY KEY); CREATE TABLE IF NOT EXISTS selections(token TEXT PRIMARY KEY,ids TEXT NOT NULL,created TEXT NOT NULL); CREATE TABLE IF NOT EXISTS imports(token TEXT PRIMARY KEY,payload TEXT NOT NULL,created REAL NOT NULL); CREATE TABLE IF NOT EXISTS sales(id INTEGER PRIMARY KEY AUTOINCREMENT,piece INTEGER NOT NULL,quantity INTEGER NOT NULL,unit_price REAL,note TEXT,sold_at TEXT NOT NULL,username TEXT NOT NULL);')
        # Preserve an existing developer account when updating its username.
        previous_developer = 'Coleccion-Web-Programador'
        if DEVELOPER_USER != previous_developer and c.execute('SELECT 1 FROM accounts WHERE username=?',(previous_developer,)).fetchone() and not c.execute('SELECT 1 FROM accounts WHERE username=?',(DEVELOPER_USER,)).fetchone():
            for account_table in ('accounts','audit','sales','verified_contacts','verification_codes','activation_codes'):
                if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(account_table,)).fetchone():
                    c.execute('UPDATE '+account_table+' SET username=? WHERE username=?',(DEVELOPER_USER,previous_developer))
            c.execute('UPDATE settings SET key=? WHERE key=?',('authVersion:'+DEVELOPER_USER,'authVersion:'+previous_developer))
        c.execute('INSERT OR IGNORE INTO accounts(username,password_hash) VALUES(?,?)',(DEVELOPER_USER,DEVELOPER_HASH))
        c.execute('INSERT OR IGNORE INTO accounts(username,password_hash) VALUES(?,?)',('AdminETG_176463',generate_password_hash(secrets.token_urlsafe(40))))
        if not c.execute('SELECT 1 FROM settings WHERE key="initialized"').fetchone():
            seed=json.loads((BASE/'datos_iniciales/catalogo.json').read_text(encoding='utf-8'))
            for p in seed['items']:put(c,normalized(p,p))
            for cat in seed['catalogs']:c.execute('INSERT OR IGNORE INTO catalogs VALUES(?)',(cat,))
            set_setting(c,'initialized','1')
    def audit(c, action, detail=''):
        if session.get('username') == DEVELOPER_USER:return
        c.execute('INSERT INTO audit(username,action,detail,occurred) VALUES(?,?,?,?)',(session.get('username','Sistema'),action,str(detail)[:1000],datetime.now(timezone.utc).isoformat()))
    def sync_catalog_excel(name):
        filename='Catalogo_'+hashlib.sha256(name.encode('utf-8')).hexdigest()[:12]+'.xlsx'
        with db() as con:
            return export_inventory([record for record in items() if record['catalog']==name],con,data/'excel'/filename)
    def backup():
        p=data/'respaldos'/('coleccion_'+datetime.now().strftime('%Y%m%d_%H%M%S_')+secrets.token_hex(3)+'.sqlite3')
        with db() as source,closing(sqlite3.connect(p)) as target:source.backup(target)
        return p
    app.backup=backup
    def csrf():
        if 'csrf' not in session:session['csrf']=secrets.token_hex(24)
        return session['csrf']
    def admin(fn):
        @wraps(fn)
        def wrapper(*a,**kw):
            if not session.get('owner') or session.get('username') not in (DEVELOPER_USER,'AdminETG_176463'):abort(401,description='Inicie sesión para continuar.')
            return fn(*a,**kw)
        return wrapper
    @app.before_request
    def protect():
        if session.get('owner') and session.get('authVersion','0')!=setting('authVersion:'+session.get('username',''),'0'):
            session.clear()
        if request.method in ('POST','PUT','PATCH','DELETE'):
            supplied=request.headers.get('X-CSRF-Token','')
            if not secrets.compare_digest(supplied,session.get('csrf','missing')):abort(403,description='La sesión venció. Actualice la página e vuelva a intentarlo.')
    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options']='nosniff';response.headers['Referrer-Policy']='same-origin'
        response.headers['X-Frame-Options']='DENY';response.headers['Cache-Control']='no-store'
        response.headers['Content-Security-Policy']="default-src 'self'; img-src 'self' data:; script-src 'self'; style-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        return response
    @app.errorhandler(400)
    @app.errorhandler(401)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(409)
    @app.errorhandler(413)
    @app.errorhandler(429)
    def error(e):
        if request.path.startswith('/api/'):return jsonify(error=e.description),e.code
        return render_template('error.html',message=e.description),e.code
    @app.errorhandler(ValueError)
    def validation(e):return jsonify(error=str(e)),400
    @app.get('/')
    def home():
        if not session.get('owner'):count_visit()
        return render_template('index.html')
    @app.get('/api/session')
    def status():return jsonify(username=session.get('username',''),developer=session.get('username')==DEVELOPER_USER,collectorConfigured=setting('collectorConfigured')=='1',owner=bool(session.get('owner')),configured=bool(setting('password')) or has_accounts(),csrf=csrf(),local=request.remote_addr in ('127.0.0.1','::1'))
    attempts={};attempt_lock=threading.Lock()
    @app.post('/api/login')
    def login():
        ip=request.remote_addr
        with attempt_lock:
            now=time.time()
            for k in list(attempts):
                attempts[k]=[t for t in attempts[k] if now-t<600]
                if not attempts[k]:del attempts[k]
            tries=attempts.get(ip,[])
            if len(tries)>=8:abort(429,description='Demasiados intentos. Espera 10 minutos.')
            if len(attempts)>1000:abort(429)
            attempts.setdefault(ip,[]).append(now)
        payload=request.get_json(silent=True) or {}
        username=str(payload.get('username','')).strip()
        if username not in (DEVELOPER_USER,'AdminETG_176463'):abort(401,description='Usuario o contraseña incorrectos.')
        password=str(payload.get('password',''))
        with db() as c:
            row=c.execute('SELECT password_hash FROM accounts WHERE username=?',(username,)).fetchone()
        if not row and username=='AdminETG_176463':
            with db() as c: has_accounts=bool(c.execute('SELECT 1 FROM accounts LIMIT 1').fetchone())
            hashed=None if has_accounts else setting('password')
        else: hashed=row[0] if row else None
        if not hashed:abort(401,description='Usuario o contraseña incorrectos.')
        if not check_password_hash(hashed,password):abort(401,description='Usuario o contraseña incorrectos.')
        session.clear();session['owner']=True;session['username']=username;session['authVersion']=setting('authVersion:'+username,'0');session.permanent=True
        with attempt_lock:attempts.pop(ip,None)
        return jsonify(owner=True,csrf=csrf())
    @app.post('/api/setup')
    def setup():
        abort(403,description='Solo el desarrollador puede configurar la cuenta del coleccionista desde su panel.')
    @app.post('/api/logout')
    def logout():session.clear();return jsonify(ok=True)
    def revision(p):return hashlib.sha256(json.dumps(p,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    @app.get('/api/items')
    def list_items():
        complete=items();published=[p for p in complete if p.get('visibility')=='visible']
        allowed=PUBLIC_FIELDS | (COMMERCIAL_FIELDS if request.args.get('modo')=='comercial' else set())
        ps=complete if session.get('owner') else [{k:v for k,v in p.items() if k in allowed} for p in published]
        with db() as c:
            cats=[r[0] for r in c.execute('SELECT name FROM catalogs ORDER BY name')]
            sold=c.execute('SELECT COALESCE(SUM(quantity),0) FROM sales').fetchone()[0]
            sold_by_piece=dict(c.execute('SELECT piece,SUM(quantity) FROM sales GROUP BY piece'))
            amounts_by_piece=dict(c.execute('SELECT piece,COALESCE(SUM(quantity*unit_price),0) FROM sales GROUP BY piece'))
            unpriced_by_piece=dict(c.execute('SELECT piece,SUM(quantity) FROM sales WHERE unit_price IS NULL GROUP BY piece'))
        if not session.get('owner'):
            complete=published
            sold=0
            cats=sorted({p['catalog'] for p in published})
        summary=dict(recordsTotal=len(complete),recordsPublished=len(published),recordsPending=len(complete)-len(published),
          unitsTotal=sum(int(p.get('quantity') or 0) for p in complete),unitsPublished=sum(int(p.get('quantity') or 0) for p in published),
          unitsForSale=sum(int(p.get('saleQuantity') or 0) for p in complete if p.get('sale')=='Disponible'),unitsSold=int(sold or 0),salesRevenue=sum(amounts_by_piece.values()) if session.get('owner') else 0,unpricedSales=sum(unpriced_by_piece.values()) if session.get('owner') else 0)
        if session.get('owner'):ps=[{**p,'_revision':revision(p),'_soldQuantity':int(sold_by_piece.get(p['id'],0)),'_salesAmount':amounts_by_piece.get(p['id'],0),'_unpricedSales':unpriced_by_piece.get(p['id'],0)} for p in ps]
        return jsonify(items=ps,catalogs=cats,summary=summary)
    @app.post('/api/text/preview')
    @admin
    def text_preview():
        text=(request.get_json(silent=True) or {}).get('text')
        if not isinstance(text,str) or len(text)>100000:raise ValueError('Pegue un texto de hasta 100 000 caracteres.')
        chunks=[s.strip() for s in re.split(r'(?im)^\s*REGISTRO\s+DE\s+CAT[ÁA]LOGO\s*\\?\s*$',text) if s.strip()]
        if len(chunks)>1:
            return jsonify(records=[parse_catalog_text(s) for s in chunks],message='Se encontraron varios registros. Seleccione uno para revisar sus datos.')
        return jsonify(parse_catalog_text(text))

    @app.post('/api/items')
    @admin
    def save_item():
        p=request.get_json(silent=True) or {};raw_id=p.get('id')
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            if raw_id is None:p['id']=c.execute('SELECT COALESCE(MAX(id),0)+1 FROM (SELECT id FROM pieces UNION ALL SELECT id FROM trash)').fetchone()[0];old=None
            else:
                pid=positive_int(raw_id,'Consecutivo');row=c.execute('SELECT payload FROM pieces WHERE id=?',(pid,)).fetchone()
                if not row:abort(404)
                old=json.loads(row[0])
                if p.get('_revision')!=revision(old):abort(409,description='La pieza cambió en otra ventana. Guarda una copia de tu texto y vuelve a abrir el registro antes de editar.')
            p=normalized(p,old)
            if not c.execute('SELECT 1 FROM catalogs WHERE name=?',(p['catalog'],)).fetchone():raise ValueError('Crea o seleccione un catálogo existente.')
            put(c,p)
        with db() as c:audit(c,'Pieza guardada',str(p['id']))
        # El inventario completo se genera al descargarlo, sin sobrescribir el original.
        return jsonify(item={**p,'_revision':revision(p)})
    @app.post('/api/catalogs')
    @admin
    def add_catalog():
        name=str((request.get_json(silent=True) or {}).get('name','')).strip()
        if not name or len(name)>80:raise ValueError('Capture un nombre de catálogo de hasta 80 caracteres.')
        with db() as c:
            if c.execute('SELECT 1 FROM catalogs WHERE lower(name)=lower(?)',(name,)).fetchone():raise ValueError('Ya existe ese catálogo.')
            c.execute('INSERT INTO catalogs VALUES(?)',(name,));audit(c,'Catálogo creado',name)
        sync_catalog_excel(name)
        return jsonify(ok=True)
    @app.post('/api/catalogs/rename')
    @admin
    def rename_catalog():
        d=request.get_json(silent=True) or {};name=str(d.get('name','')).strip();target=str(d.get('target','')).strip()
        if not name or not target or len(target)>80:raise ValueError('Seleccione el catálogo y escriba un nombre de hasta 80 caracteres.')
        backup()
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            if not c.execute('SELECT 1 FROM catalogs WHERE name=?',(name,)).fetchone():abort(404)
            existing=c.execute('SELECT name FROM catalogs WHERE lower(name)=lower(?)',(target,)).fetchone()
            if existing:target=existing[0]
            changed=0
            if name!=target:
                c.execute('INSERT OR IGNORE INTO catalogs VALUES(?)',(target,))
                for pid,payload in c.execute('SELECT id,payload FROM pieces').fetchall():
                    p=json.loads(payload)
                    if p.get('catalog')==name:p['catalog']=target;put(c,p);changed+=1
                c.execute('DELETE FROM catalogs WHERE name=?',(name,))
                audit(c,'Catálogo corregido',name+' → '+target)
        return jsonify(ok=True,changed=changed,catalog=target)
    @app.delete('/api/catalogs/empty')
    @admin
    def delete_empty_catalog():
        name=str((request.get_json(silent=True) or {}).get('name','')).strip()
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            if not c.execute('SELECT 1 FROM catalogs WHERE name=?',(name,)).fetchone():abort(404)
            if any(json.loads(row[0]).get('catalog')==name for row in c.execute('SELECT payload FROM pieces')):
                raise ValueError('Este catálogo contiene piezas. Corrija su nombre o páselas a otro catálogo; no se eliminaron registros.')
            c.execute('DELETE FROM catalogs WHERE name=?',(name,));audit(c,'Catálogo vacío eliminado',name)
        return jsonify(ok=True)
    @app.post('/api/items/<int:pid>/photo')
    @admin
    def upload_photo(pid):
        p=item(pid)
        if not p:abort(404)
        f=request.files.get('photo')
        if not f:raise ValueError('Seleccione una fotografía.')
        raw=f.read(8*1024*1024+1)
        if len(raw)>8*1024*1024:raise ValueError('La fotografía supera 8 MB.')
        try:
            Image.MAX_IMAGE_PIXELS=25_000_000
            im=Image.open(io.BytesIO(raw));im.verify()
            im=ImageOps.exif_transpose(Image.open(io.BytesIO(raw)));im.thumbnail((2000,2000));im=im.convert('RGB')
        except Exception as e:raise ValueError('La fotografía no es una imagen válida o es demasiado grande.') from e
        converted=photograph(raw)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');r=c.execute('SELECT payload FROM pieces WHERE id=?',(pid,)).fetchone()
            if not r:abort(404)
            p=json.loads(r[0]);p['image']='/media/'+str(pid);p.pop('imageFile',None)
            p['photoName']=Path(f.filename or '').name[:255]
            store_photo(c,pid,converted);c.execute('INSERT OR REPLACE INTO original_photographs VALUES(?,?,?)',(pid,raw,Image.MIME.get(Image.open(io.BytesIO(raw)).format,'application/octet-stream')));put(c,p);audit(c,'Fotografía actualizada',str(pid))
        return jsonify(item={**p,'_revision':revision(p)})
    @app.delete('/api/items/<int:pid>/photo')
    @admin
    def remove_primary_photo(pid):
        backup()
        with db() as c:
            c.execute('BEGIN IMMEDIATE');r=c.execute('SELECT payload FROM pieces WHERE id=?',(pid,)).fetchone()
            if not r:abort(404)
            p=json.loads(r[0]);p['image']='';p.pop('photoName',None);p.pop('imageFile',None)
            c.execute('DELETE FROM photographs WHERE piece=?',(pid,))
            c.execute('DELETE FROM original_photographs WHERE piece=?',(pid,))
            put(c,p);audit(c,'Fotografía principal eliminada',str(pid))
        return jsonify(item={**p,'_revision':revision(p)})
    @app.post('/api/items/<int:pid>/availability')
    @admin
    def set_availability(pid):
        d=request.get_json(silent=True) or {}
        if not isinstance(d.get('inSale'),bool):raise ValueError('Seleccione si la pieza está en venta.')
        with db() as c:
            c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT payload FROM pieces WHERE id=?',(pid,)).fetchone()
            if not row:abort(404)
            p=json.loads(row[0])
            if d.get('revision')!=revision(p):abort(409,description='La pieza cambió. Actualice el inventario antes de continuar.')
            if d['inSale']:
                quantity=positive_int(d.get('quantity',p.get('saleQuantity') or p['quantity']),'Cantidad en venta')
                if quantity>p['quantity']:raise ValueError('La cantidad en venta no puede superar las existencias.')
                p['sale']='Disponible';p['saleQuantity']=quantity
                if 'price' in d:p['salePrice']=price(d['price'])
            else:p['sale']='No en venta';p['saleQuantity']=0
            put(c,p);audit(c,'Disponibilidad actualizada',str(pid))
        return jsonify(ok=True,item={**p,'_revision':revision(p)})
    @app.get('/media/<int:pid>')
    def image(pid):
        p=item(pid)
        if not p or (p['visibility']!='visible' and not session.get('owner')):abort(404)
        with db() as c:
            row=c.execute('SELECT thumbnail,content FROM photographs WHERE piece=?',(pid,)).fetchone()
        if row:
            content=row[0] if request.args.get('miniatura')=='1' else row[1]
            return send_file(io.BytesIO(content),mimetype='image/jpeg')
        name=p.get('imageFile','')
        if not name or Path(name).name!=name or not (data/'fotografias'/name).is_file():abort(404)
        return send_file(data/'fotografias'/name,mimetype='image/jpeg')
    @app.post('/api/import/preview')
    @admin
    def import_preview():
        f=request.files.get('excel')
        if not f or not f.filename.lower().endswith(('.xlsx','.xlsm')):raise ValueError('Seleccione un archivo .xlsx o .xlsm.')
        result,photos=read_inventory(f.stream,request.form.get('catalog','').strip(),HEADERS,clean_header,normalized,CATEGORIES)
        originals=result.pop('_originals',{})
        existing={p['id']:p for p in items()}
        with db() as c:
            baseline={pid:json.loads(payload) for pid,payload in c.execute('SELECT piece,payload FROM inventory_baseline')}
            photo_digests=dict(c.execute('SELECT piece,digest FROM photographs'))
        planned,conflicts,errors=merge_plan(result['records'],existing,baseline,normalized)
        result['errors'].extend(errors)
        for pid,photo in photos.items():
            if pid in photo_digests and photo_digests[pid]!=photo[2]:
                conflicts.append(dict(id=pid,field='photograph',system='Fotografía del sistema',excel='Fotografía del inventario'))
        result.update(conflicts=conflicts,revision=hashlib.sha256(json.dumps(existing,sort_keys=True).encode()).hexdigest(),photoRevision=photo_digests)
        result['new']=sum(p['id'] not in existing for p in result['records'])
        result['update']=sum(p!=existing.get(p['id']) for p in planned if p['id'] in existing)
        token=secrets.token_urlsafe(24)
        with db() as c:
            c.execute('DELETE FROM pending_originals WHERE token IN (SELECT token FROM imports WHERE created<?)',(time.time()-1800,))
            c.execute('DELETE FROM inventory_photos WHERE token IN (SELECT token FROM imports WHERE created<?)',(time.time()-1800,))
            c.execute('DELETE FROM imports WHERE created<?',(time.time()-1800,))
            c.execute('INSERT INTO imports VALUES(?,?,?)',(token,json.dumps(result,ensure_ascii=False),time.time()))
            for pid,photo in photos.items():
                if photo_digests.get(pid)!=photo[2]:
                    c.execute('INSERT INTO inventory_photos VALUES(?,?,?,?,?)',(token,pid,*photo))
                    if pid in originals:c.execute('INSERT INTO pending_originals VALUES(?,?,?,?)',(token,pid,*originals[pid]))
        session['import_token']=token
        return jsonify(token=token,new=result['new'],update=result['update'],errors=result['errors'],photosPending=result['photosPending'],photos=len(photos),conflicts=conflicts,notices=result['notices'],count=len(result['records']),sample=[{'id':p['id'],'name':p.get('name',''),'catalog':p.get('catalog','')} for p in result['records'][:10]])
    @app.post('/api/import/apply')
    @admin
    def import_apply():
        body=request.get_json(silent=True) or {};token=body.get('token');choices=body.get('choices',{})
        if not token or token!=session.get('import_token'):abort(403)
        if not isinstance(choices,dict):raise ValueError('Las decisiones de actualización no son válidas.')
        with db() as c:r=c.execute('SELECT payload,created FROM imports WHERE token=?',(token,)).fetchone()
        if not r or time.time()-r[1]>1800:raise ValueError('La revisión venció. Vuelva a cargar el inventario.')
        result=json.loads(r[0])
        if result['errors']:raise ValueError('Corrija las filas señaladas antes de importar.')
        for conflict in result['conflicts']:
            if choices.get(str(conflict['id'])+':'+conflict['field']) not in ('system','excel'):
                raise ValueError('Seleccione qué dato conservar en cada diferencia pendiente.')
        backup()
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            existing={pid:json.loads(payload) for pid,payload in c.execute('SELECT id,payload FROM pieces')}
            revision=hashlib.sha256(json.dumps(existing,sort_keys=True).encode()).hexdigest()
            digests={str(pid):digest for pid,digest in c.execute('SELECT piece,digest FROM photographs')}
            if revision!=result['revision'] or digests!=result['photoRevision']:
                abort(409,description='La colección cambió durante la revisión. Vuelva a cargar el inventario para conservar los cambios recientes.')
            baseline={pid:json.loads(payload) for pid,payload in c.execute('SELECT piece,payload FROM inventory_baseline')}
            planned,conflicts,errors=merge_plan(result['records'],existing,baseline,normalized)
            by_id={p['id']:p for p in planned}
            for conflict in conflicts:
                if choices.get(str(conflict['id'])+':'+conflict['field'])=='excel':
                    by_id[conflict['id']][conflict['field']]=conflict['excel']
            for pid,content,thumb,digest in c.execute('SELECT piece,content,thumbnail,digest FROM inventory_photos WHERE token=?',(token,)).fetchall():
                if pid not in by_id:continue
                if str(pid) not in digests or choices.get(str(pid)+':photograph','excel')=='excel':
                    store_photo(c,pid,(content,thumb,digest))
                    original=c.execute('SELECT content,mime FROM pending_originals WHERE token=? AND piece=?',(token,pid)).fetchone()
                    if original:c.execute('INSERT OR REPLACE INTO original_photographs VALUES(?,?,?)',(pid,*original))
                by_id[pid]['image']='/media/'+str(pid)
            changed=0
            for pid,p in by_id.items():
                p=normalized(p,p)
                if p!=existing.get(pid):put(c,p);changed+=1
                c.execute('INSERT OR IGNORE INTO catalogs VALUES(?)',(p['catalog'],))
            # Baseline stores the inventory's supplied fields, never synthesized defaults.
            for p in result['records']:
                merged={**baseline.get(p['id'],{}),**p}
                c.execute('INSERT INTO inventory_baseline VALUES(?,?) ON CONFLICT(piece) DO UPDATE SET payload=excluded.payload',(p['id'],json.dumps(merged,ensure_ascii=False)))
            c.execute('DELETE FROM pending_originals WHERE token=?',(token,))
            c.execute('DELETE FROM inventory_photos WHERE token=?',(token,))
            c.execute('DELETE FROM imports WHERE token=?',(token,));audit(c,'Inventario importado',str(changed)+' registros actualizados')
        session.pop('import_token',None)
        return jsonify(ok=True,count=len(by_id),changed=changed)
    @app.get('/pieza/<int:pid>')
    def piece_page(pid):
        p=item(pid)
        if not p or (p['visibility']!='visible' and not session.get('owner')):abort(404,description='Esta pieza no está disponible para consulta.')
        if not session.get('owner'):count_visit(pid)
        return render_template('piece.html',piece=p,print_mode=False)
    @app.get('/pieza/<int:pid>/ficha')
    @app.get('/pieza/<int:pid>/ficha.pdf')
    def piece_sheet(pid):
        p=item(pid)
        if not p or (p['visibility']!='visible' and not session.get('owner')):abort(404)
        with db() as c:r=c.execute('SELECT content FROM photographs WHERE piece=?',(pid,)).fetchone()
        photo=r[0] if r else None
        if photo is None and p.get('imageFile'):
            path=data/'fotografias'/Path(p['imageFile']).name
            if path.is_file():photo=path.read_bytes()
        from ficha_pdf import make_piece_pdf
        content=make_piece_pdf(p,photo,bool(session.get('owner')) or request.args.get('modo')=='comercial')
        return send_file(io.BytesIO(content),mimetype='application/pdf',as_attachment=False,download_name='Ficha_'+str(pid)+'.pdf')
    @app.post('/api/selections')
    @admin
    def selection():
        ids=(request.get_json(silent=True) or {}).get('ids',[])
        if not isinstance(ids,list) or not 1<=len(ids)<=100:raise ValueError('Seleccione de 1 a 100 piezas visibles.')
        ids=list(dict.fromkeys(positive_int(v,'Consecutivo') for v in ids))
        for pid in ids:
            p=item(pid)
            if not p or p['visibility']!='visible':raise ValueError('Solo se comparten piezas visibles.')
        token=secrets.token_urlsafe(20)
        with db() as c:c.execute('INSERT INTO selections VALUES(?,?,?)',(token,json.dumps(ids),datetime.now(timezone.utc).isoformat()))
        return jsonify(path='/seleccion/'+token)
    @app.get('/seleccion/<token>')
    def selected_page(token):
        with db() as c:r=c.execute('SELECT ids FROM selections WHERE token=?',(token,)).fetchone()
        if not r:abort(404)
        ps=[p for pid in json.loads(r[0]) if (p:=item(pid)) and p['visibility']=='visible']
        return render_template('selection.html',pieces=ps)
    @app.get('/api/catalogs/template')
    @admin
    def download_inventory_template():
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
        from integracion import EXPORT_COLUMNS
        wb=Workbook();ws=wb.active;ws.title='Inventario de piezas'
        columns=EXPORT_COLUMNS[:28]
        ws.append([label for label,key in columns]);ws.freeze_panes='A2';ws.auto_filter.ref=ws.dimensions
        for cell in ws[1]:
            cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='1B5140')
            ws.column_dimensions[cell.column_letter].width=28
        help_sheet=wb.create_sheet('Instrucciones')
        for line in ['Plantilla vacía para registrar piezas nuevas.','Escribe una pieza por fila debajo de los encabezados.','Usa un consecutivo que no exista: repetirlo se interpreta como actualización.','Importa el archivo desde Actualizar inventario y revisa los cambios antes de aplicar.','No se incluyen piezas, contraseñas ni fotografías de la colección.']:
            help_sheet.append([line])
        help_sheet.column_dimensions['A'].width=110
        out=io.BytesIO();wb.save(out);out.seek(0)
        return send_file(out,mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',as_attachment=True,download_name='Plantilla_coleccion.xlsx')
    @app.get('/api/backup')
    @admin
    def download_backup():
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
            copy=backup();z.write(copy,'coleccion.sqlite3')
            for p in (data/'fotografias').glob('*.jpg'):z.write(p,'fotografias/'+p.name)
        out.seek(0);return send_file(out,mimetype='application/zip',as_attachment=True,download_name='Museograma_respaldo_'+datetime.now().strftime('%Y%m%d_%H%M')+'.zip')
    @app.get('/api/settings')
    @admin
    def get_settings():return jsonify(publicBase=setting('publicBase',''))
    @app.post('/api/settings')
    @admin
    def update_settings():
        v=str((request.get_json(silent=True) or {}).get('publicBase','')).strip().rstrip('/')
        if v:
            u=urlsplit(v)
            if u.scheme not in ('http','https') or not u.hostname or u.username or u.password or u.query or u.fragment or u.path not in ('','/'):raise ValueError('Utilice una dirección completa, sin ruta, por ejemplo http://192.168.1.20:8765')
        with db() as c:set_setting(c,'publicBase',v)
        return jsonify(ok=True)
    @app.get('/api/share-base')
    def share_base():return jsonify(publicBase=setting('publicBase',''))
    @app.post('/api/accounts/password')
    @admin
    def change_password():
        body=request.get_json(silent=True) or {}
        username=session['username']
        old=str(body.get('current',''));new=str(body.get('new',''))
        if len(new)<12 or len(new)>200:raise ValueError('La nueva contraseña debe tener al menos 12 caracteres.')
        with db() as c:
            row=c.execute('SELECT password_hash FROM accounts WHERE username=?',(username,)).fetchone()
            old_hash=row[0] if row else setting('password')
            if not old_hash or not check_password_hash(old_hash,old):abort(403,description='Contraseña actual incorrecta.')
            c.execute('INSERT INTO accounts(username,password_hash) VALUES(?,?) ON CONFLICT(username) DO UPDATE SET password_hash=excluded.password_hash',(username,generate_password_hash(new)))
            version=secrets.token_hex(24)
            set_setting(c,'authVersion:'+username,version)
            session['authVersion']=version
            audit(c,'Contraseña actualizada')
        return jsonify(ok=True)

    @app.post('/api/accounts/username')
    @admin
    def change_username():
        raise ValueError('Los usuarios de esta edición son fijos: Coleccion_Web_Programador y AdminETG_176463.')

    @app.post('/api/accounts/recovery-codes')
    @admin
    def recovery_codes():
        codes=[secrets.token_urlsafe(12) for _ in range(6)]
        with db() as c:
            c.execute('UPDATE accounts SET recovery_hash=? WHERE username=?',(json.dumps([generate_password_hash(x) for x in codes]),session['username']))
            audit(c,'Códigos de recuperación generados')
        return jsonify(codes=codes)

    @app.post('/api/accounts/recover')
    def recover_account():
        body=request.get_json(silent=True) or {}
        username=str(body.get('username',''));code=str(body.get('code',''));new=str(body.get('new',''))
        if not 12<=len(new)<=200:raise ValueError('La contraseña debe tener entre 12 y 200 caracteres.')
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT recovery_hash FROM accounts WHERE username=?',(username,)).fetchone()
            hashes=json.loads(row[0]) if row and row[0] else []
            match=next((i for i,h in enumerate(hashes) if check_password_hash(h,code)),None)
            if match is None:abort(403,description='Código de recuperación incorrecto.')
            hashes.pop(match)
            set_setting(c,'authVersion:'+username,secrets.token_hex(24))
            c.execute('UPDATE accounts SET password_hash=?,recovery_hash=? WHERE username=?',(generate_password_hash(new),json.dumps(hashes),username))
            if username!=DEVELOPER_USER:c.execute('INSERT INTO audit(username,action,detail,occurred) VALUES(?,?,?,?)',(username,'Acceso recuperado','',datetime.now(timezone.utc).isoformat()))
        return jsonify(ok=True)

    @app.get('/api/items/<int:pid>/sales')
    @admin
    def item_sales(pid):
        if not item(pid):abort(404)
        with db() as c:
            movements=[dict(id=r[0],quantity=r[1],unitPrice=r[2],note=r[3],date=r[4],username=r[5]) for r in c.execute('SELECT id,quantity,unit_price,note,sold_at,username FROM sales WHERE piece=? ORDER BY id DESC LIMIT 100',(pid,))]
        return jsonify(items=[m for m in movements if m.get('username')!=DEVELOPER_USER or session.get('username')==DEVELOPER_USER])

    @app.post('/api/items/<int:pid>/sales')
    @admin
    def register_sale(pid):
        body=request.get_json(silent=True) or {}
        sold=positive_int(body.get('quantity'),'Unidades vendidas')
        unit_price=price(body.get('unitPrice'))
        note=str(body.get('note','') or '').strip()[:1000]
        backup()
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT payload FROM pieces WHERE id=?',(pid,)).fetchone()
            if not row:abort(404)
            p=json.loads(row[0]);available=nonnegative_int(p.get('quantity',0),'Piezas')
            if sold>available:raise ValueError('No puede vender más unidades de las existentes.')
            p['quantity']=available-sold
            p['saleQuantity']=max(0,int(p.get('saleQuantity') or 0)-sold)
            if p['quantity']==0:
                p['sale']='Vendida';p['saleQuantity']=0
            elif p['sale']=='Vendida':p['sale']='Por confirmar'
            put(c,p)
            c.execute('INSERT INTO sales(piece,quantity,unit_price,note,sold_at,username) VALUES(?,?,?,?,?,?)',(pid,sold,unit_price,note,datetime.now(timezone.utc).isoformat(),session['username']))
            audit(c,'Venta registrada',json.dumps({'pieza':pid,'unidades':sold,'existencia':p['quantity']},ensure_ascii=False))
        sync_catalog_excel(p['catalog'])
        return jsonify(item=p,remaining=p['quantity'])

    @app.delete('/api/items/<int:pid>')
    @admin
    def delete_item(pid):
        backup()
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT payload FROM pieces WHERE id=?',(pid,)).fetchone()
            if not row:abort(404)
            c.execute('INSERT OR REPLACE INTO trash VALUES(?,?,?)',(pid,row[0],datetime.now(timezone.utc).isoformat()))
            c.execute('DELETE FROM pieces WHERE id=?',(pid,))
            audit(c,'Pieza eliminada',str(pid))
        sync_catalog_excel(json.loads(row[0])['catalog'])
        return jsonify(ok=True)

    @app.post('/api/items/<int:pid>/restore')
    @admin
    def restore_item(pid):
        with db() as c:
            row=c.execute('SELECT payload FROM trash WHERE id=?',(pid,)).fetchone()
            if not row:abort(404)
            if c.execute('SELECT 1 FROM pieces WHERE id=?',(pid,)).fetchone():abort(409,description='Ya existe una pieza con ese número.')
            c.execute('INSERT INTO pieces VALUES(?,?)',(pid,row[0]))
            c.execute('DELETE FROM trash WHERE id=?',(pid,))
            audit(c,'Pieza recuperada',str(pid))
        sync_catalog_excel(json.loads(row[0])['catalog'])
        return jsonify(ok=True)

    @app.get('/api/trash')
    @admin
    def list_trash():
        with db() as c:return jsonify(items=[json.loads(r[0]) for r in c.execute('SELECT payload FROM trash ORDER BY id')])

    @app.delete('/api/catalogs')
    @admin
    def delete_catalog():
        name=str((request.get_json(silent=True) or {}).get('name',''))
        if not name:raise ValueError('Seleccione un catálogo.')
        backup()
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            if not c.execute('SELECT 1 FROM catalogs WHERE name=?',(name,)).fetchone():abort(404)
            for pid,payload in c.execute('SELECT id,payload FROM pieces').fetchall():
                if json.loads(payload).get('catalog')==name:
                    for table, key in (('gallery','piece'),('original_photographs','piece'),('movements','piece'),('conservation','piece'),('units','piece'),('photographs','piece'),('inventory_baseline','piece'),('inventory_photos','piece'),('sales','piece'),('visits','piece'),('trash','id'),('pieces','id')):
                        c.execute(f'DELETE FROM {table} WHERE {key}=?',(pid,))
                    legacy=json.loads(payload).get('imageFile','')
                    if legacy and Path(legacy).name==legacy:(data/'fotografias'/legacy).unlink(missing_ok=True)
            c.execute('DELETE FROM catalogs WHERE name=?',(name,))
            c.execute('DELETE FROM imports')
            c.execute('DELETE FROM pending_originals')
            c.execute('DELETE FROM inventory_photos')
            c.execute('DELETE FROM selections')
            audit(c,'Catálogo eliminado',name)
        filename='Catalogo_'+hashlib.sha256(name.encode('utf-8')).hexdigest()[:12]+'.xlsx'
        (data/'excel'/filename).unlink(missing_ok=True)
        return jsonify(ok=True)

    @app.get('/api/catalogs/excel')
    @admin
    def catalog_excel():
        name=request.args.get('name','')
        with db() as c:
            if not c.execute('SELECT 1 FROM catalogs WHERE name=?',(name,)).fetchone():abort(404)
        path=sync_catalog_excel(name)
        return send_file(path,mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',as_attachment=True,download_name='Catalogo_'+''.join(c if c.isalnum() or c in '-_' else '_' for c in name)[:60]+'.xlsx')

    @app.get('/api/activity')
    @admin
    def activity():
        with db() as c:
            history=[dict(action=r[0],detail=r[1],date=r[2]) for r in c.execute("SELECT action,detail,occurred FROM audit ORDER BY id DESC LIMIT 100")]
            visits=[dict(day=r[0],count=r[1]) for r in c.execute('SELECT day,SUM(count) FROM visits GROUP BY day ORDER BY day DESC LIMIT 30')]
        return jsonify(history=history,visits=visits)

    def count_visit(pid=0):
        if session.get("username") == DEVELOPER_USER:return
        try:
            with db() as c:
                c.execute('INSERT INTO visits(day,piece,count) VALUES(?,?,1) ON CONFLICT(day,piece) DO UPDATE SET count=count+1',(datetime.now(timezone.utc).strftime('%Y-%m-%d'),pid))
        except sqlite3.OperationalError:pass
    @app.template_filter('catalog_price')
    def catalog_price(p,key):
        v=p.get(key);state=p.get(key+'State','')
        if state=='pending':return 'Por confirmar'
        if state=='na':return 'N/A'
        if v is not None:return mxn(v)
        return 'N/A'
    @app.template_filter('mxn')
    def mxn(v):return 'Por confirmar' if v is None else '${:,.2f} MXN'.format(v)
    from mejoras import register
    register(app,db,items,item,put,admin,audit,photograph)
    from verificacion import register_verification
    register_verification(app,db,admin,csrf,DEVELOPER_USER)
    from descargas import register_downloads
    register_downloads(app,data,admin)
    from contenido import register_content
    register_content(app,db,items,admin,csrf,audit)
    from catalogo_publico import register_public_catalog
    register_public_catalog(app,items)
    return app

if __name__=='__main__':
    import argparse, webbrowser
    from waitress import serve
    parser=argparse.ArgumentParser(description='Museograma · Colección petrolera')
    parser.add_argument('--red',action='store_true',help='Permite consulta desde otros equipos de la misma red')
    parser.add_argument('--sin-navegador',action='store_true')
    parser.add_argument('--puerto',type=int,default=int(os.environ.get('PORT','8765')))
    args=parser.parse_args()
    if sys.platform=='win32':
        import ctypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.CreateMutexW.argtypes=[ctypes.c_void_p,ctypes.c_bool,ctypes.c_wchar_p]
        kernel.CreateMutexW.restype=ctypes.c_void_p
        ctypes.set_last_error(0)
        _server_mutex=kernel.CreateMutexW(None,False,'Local\\Museograma_Port_'+str(args.puerto))
        if not _server_mutex:raise OSError('No se pudo reservar la instancia de Museograma.')
        if ctypes.get_last_error()==183:
            print('Museograma ya está abierto. No se inició otra copia del servidor.')
            if not args.sin_navegador:webbrowser.open('http://127.0.0.1:'+str(args.puerto))
            raise SystemExit(0)
    app=create_app()
    host='0.0.0.0' if args.red else os.environ.get('MUSEOGRAMA_HOST','127.0.0.1')
    print('Museograma: http://127.0.0.1:'+str(args.puerto))
    print('Datos: '+str(app.config['DATA_DIR']))
    if args.red:print('Consulta en red activada. Utilice la IP de este equipo en los enlaces. Cierra esta ventana para detener el servidor.')
    if not args.sin_navegador:threading.Timer(1.5,lambda:webbrowser.open('http://127.0.0.1:'+str(args.puerto))).start()
    serve(app,host=host,port=args.puerto,threads=6,max_request_body_size=LIMIT)
