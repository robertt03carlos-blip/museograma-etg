"""Activación y recuperación con códigos de un solo uso; proveedores configurables."""
import base64,hashlib,hmac,json,os,re,secrets,smtplib,ssl,time,threading,urllib.request,urllib.parse
from email.message import EmailMessage
from flask import request,session,jsonify,abort
from werkzeug.security import generate_password_hash

COLLECTOR='AdminETG_176463'

def load_env(path):
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            line=line.strip()
            if line and not line.startswith('#') and '=' in line:
                k,v=line.split('=',1);os.environ.setdefault(k.strip(),v.strip().strip('"').strip("'"))

def send_code(address,code,channel):
    if channel=='email':
        host=os.environ.get('MUSEO_SMTP_HOST');sender=os.environ.get('MUSEO_MAIL_FROM')
        if not host or not sender:raise ValueError('El correo no está configurado. Revisa .env y LEER_PRIMERO.txt.')
        port=int(os.environ.get('MUSEO_SMTP_PORT','587'))
        msg=EmailMessage();msg['Subject']='Museograma: código de verificación';msg['From']=sender;msg['To']=address
        msg.set_content('Tu código de Museograma es: '+code+'\nCaduca en 10 minutos. No lo compartas.')
        context=ssl.create_default_context()
        with (smtplib.SMTP_SSL(host,port,timeout=20,context=context) if port==465 else smtplib.SMTP(host,port,timeout=20)) as smtp:
            if port!=465:smtp.starttls(context=context)
            if os.environ.get('MUSEO_SMTP_USER'):smtp.login(os.environ['MUSEO_SMTP_USER'],os.environ.get('MUSEO_SMTP_PASSWORD',''))
            if smtp.send_message(msg):raise ValueError('El servidor rechazó el destinatario. Revisa el correo e inténtalo de nuevo.')
        return
    sid=os.environ.get('TWILIO_ACCOUNT_SID','');token=os.environ.get('TWILIO_AUTH_TOKEN','')
    sender=os.environ.get('TWILIO_SMS_FROM' if channel=='sms' else 'TWILIO_WHATSAPP_FROM','')
    if not re.fullmatch(r'AC[0-9a-fA-F]{32}',sid) or not token or not sender:raise ValueError('El envío de SMS o WhatsApp no está configurado. Revisa .env.')
    fields={'To':address,'From':sender,'Body':'Museograma: tu código es '+code+'. Caduca en 10 minutos.'}
    if channel=='whatsapp':
        template=os.environ.get('TWILIO_WHATSAPP_CONTENT_SID','')
        if not template:raise ValueError('Configura una plantilla de autenticación aprobada para WhatsApp.')
        fields={'To':'whatsapp:'+address,'From':'whatsapp:'+sender.removeprefix('whatsapp:'),'ContentSid':template,'ContentVariables':json.dumps({'1':code})}
    req=urllib.request.Request('https://api.twilio.com/2010-04-01/Accounts/'+sid+'/Messages.json',data=urllib.parse.urlencode(fields).encode(),headers={'Authorization':'Basic '+base64.b64encode((sid+':'+token).encode()).decode()})
    with urllib.request.urlopen(req,timeout=20) as r:
        if not json.loads(r.read()).get('sid'):raise ValueError('El proveedor no confirmó la solicitud de envío.')

def register_verification(app,db,admin,csrf,developer):
    with db() as c:
        c.executescript('''CREATE TABLE IF NOT EXISTS verified_contacts(username TEXT PRIMARY KEY,email TEXT,phone TEXT);
        CREATE TABLE IF NOT EXISTS verification_codes(id TEXT PRIMARY KEY,username TEXT NOT NULL,kind TEXT NOT NULL,digest TEXT NOT NULL,expires REAL NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,destination TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS activation_codes(username TEXT PRIMARY KEY,digest TEXT NOT NULL,expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS code_limits(key TEXT PRIMARY KEY,expires REAL NOT NULL);
        ''')
    def data():
        d=request.get_json(silent=True)
        if not isinstance(d,dict):raise ValueError('Solicitud no válida.')
        return d
    def password(d):
        p=d.get('password','')
        if not isinstance(p,str) or not 12<=len(p)<=200:raise ValueError('Usa una contraseña de 12 a 200 caracteres.')
        return p
    def digest(code):return hmac.new(app.secret_key.encode(),str(code).encode(),hashlib.sha256).hexdigest()
    def limit(key,seconds=60):
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            r=c.execute('SELECT expires FROM code_limits WHERE key=?',(key,)).fetchone()
            if r and r[0]>time.time():abort(429,description='Espera un minuto antes de solicitar otro código.')
            c.execute('DELETE FROM code_limits WHERE expires<?',(time.time(),))
            c.execute('INSERT OR REPLACE INTO code_limits VALUES(?,?)',(key,time.time()+seconds))
    def issue(name,kind,address,channel):
        code=''.join(secrets.choice('0123456789') for _ in range(6));ident=secrets.token_urlsafe(24)
        try:(app.config.get('CODE_SENDER') or send_code)(address,code,channel)
        except ValueError:raise
        except Exception as e:raise ValueError('No se pudo enviar el código. Revisa la conexión y la configuración del proveedor.') from e
        with db() as c:
            c.execute('DELETE FROM verification_codes WHERE (username=? AND kind=?) OR expires<?',(name,kind,time.time()))
            c.execute('INSERT INTO verification_codes VALUES(?,?,?,?,?,0,?)',(ident,name,kind,digest(code),time.time()+600,address))
        return ident
    def consume(c,d,kind,name=None):
        r=c.execute('SELECT username,digest,expires,attempts,destination FROM verification_codes WHERE id=? AND kind=?',(d.get('requestId'),kind)).fetchone()
        if not r or r[2]<time.time() or r[3]>=5 or (name and name!=r[0]):return None
        c.execute('UPDATE verification_codes SET attempts=attempts+1 WHERE id=?',(d.get('requestId'),))
        code=d.get('code','')
        if not isinstance(code,str) or not re.fullmatch(r'[0-9]{6}',code) or not hmac.compare_digest(r[1],digest(code)):return None
        c.execute('DELETE FROM verification_codes WHERE id=?',(d.get('requestId'),));return r
    @app.get('/api/contacts')
    @admin
    def contacts():
        with db() as c:r=c.execute('SELECT email,phone FROM verified_contacts WHERE username=?',(session['username'],)).fetchone()
        return jsonify(emailVerified=bool(r and r[0]),phoneVerified=bool(r and r[1]))
    @app.post('/api/invite')
    @admin
    def invite():
        if session['username']!=developer:abort(403)
        code=secrets.token_urlsafe(24)
        with db() as c:
            if c.execute("SELECT 1 FROM settings WHERE key='collectorConfigured' AND value='1'").fetchone():raise ValueError('La cuenta ya está activa. Usa la recuperación de acceso.')
            c.execute('INSERT OR REPLACE INTO activation_codes VALUES(?,?,?)',(COLLECTOR,digest(code),time.time()+72*3600))
        return jsonify(username=COLLECTOR,code=code,expiresHours=72)
    @app.post('/api/activate')
    def activate():
        d=data();p=password(d);name=str(d.get('username',''))
        limit('activate:'+str(request.remote_addr),2)
        with db() as c:
            c.execute('BEGIN IMMEDIATE')
            r=c.execute('SELECT digest,expires FROM activation_codes WHERE username=?',(name,)).fetchone()
            if name!=COLLECTOR or not r or r[1]<time.time() or not hmac.compare_digest(r[0],digest(d.get('code',''))):abort(400,description='Usuario o código de activación incorrecto o caducado.')
            c.execute('UPDATE accounts SET password_hash=?,recovery_hash=NULL WHERE username=?',(generate_password_hash(p),name))
            c.execute('DELETE FROM activation_codes WHERE username=?',(name,));c.execute("INSERT OR REPLACE INTO settings VALUES('collectorConfigured','1')")
        session.clear();session.update(owner=True,username=name,authVersion='0');session.permanent=True
        return jsonify(ok=True,csrf=csrf())
    @app.post('/api/contacts/request')
    @admin
    def request_contact():
        d=data();channel=d.get('channel');address=str(d.get('address','')).strip()
        if channel not in ('email','sms','whatsapp'):raise ValueError('Elige correo, SMS o WhatsApp.')
        if channel=='email':
            if len(address)>254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',address):raise ValueError('Escribe un correo válido.')
        elif not re.fullmatch(r'\+[1-9][0-9]{7,14}',address):raise ValueError('Usa el teléfono con código de país, por ejemplo +525512345678.')
        limit('send:'+session['username']);kind='email' if channel=='email' else 'phone'
        return jsonify(requestId=issue(session['username'],kind,address,channel))
    @app.post('/api/contacts/verify')
    @admin
    def verify_contact():
        d=data();kind=d.get('kind')
        if kind not in ('email','phone'):raise ValueError('Selecciona el medio que vas a verificar.')
        with db() as c:
            c.execute('BEGIN IMMEDIATE');r=consume(c,d,kind,session['username'])
            if r:c.execute('INSERT INTO verified_contacts(username,'+kind+') VALUES(?,?) ON CONFLICT(username) DO UPDATE SET '+kind+'=excluded.'+kind,(session['username'],r[4]))
        if not r:abort(400,description='Código incorrecto, caducado o con cinco intentos fallidos.')
        return jsonify(ok=True)
    @app.post('/api/recover/request')
    def recover_request():
        d=data();name=str(d.get('username',''))[:100];channel=d.get('channel')
        if channel not in ('email','sms','whatsapp'):raise ValueError('Elige correo, SMS o WhatsApp.')
        limit('recover-ip:'+str(request.remote_addr));limit('send:'+name)
        with db() as c:r=c.execute('SELECT email,phone FROM verified_contacts WHERE username=?',(name,)).fetchone()
        address=(r[0] if channel=='email' else r[1]) if r else None
        # Only a random request identifier is returned; no contact is disclosed.
        ident=issue(name,'reset',address,channel) if address else secrets.token_urlsafe(24)
        return jsonify(requestId=ident,message='Si la cuenta tiene ese medio verificado, recibirá un código. Caduca en 10 minutos.')
    @app.post('/api/recover/reset')
    def recover_reset():
        d=data();p=password(d)
        with db() as c:
            c.execute('BEGIN IMMEDIATE');r=consume(c,d,'reset')
            if r:
                c.execute('UPDATE accounts SET password_hash=?,recovery_hash=NULL WHERE username=?',(generate_password_hash(p),r[0]))
                c.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('authVersion:'+r[0],secrets.token_hex(24)))
        if not r:abort(400,description='Código incorrecto, caducado o con cinco intentos fallidos.')
        return jsonify(ok=True)
