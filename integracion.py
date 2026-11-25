"""Lectura flexible de inventarios y fotografías integradas, sin ejecutar macros."""
import io
import json
import hashlib
import posixpath
import sqlite3
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from PIL import Image, ImageOps

S = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
X = 'http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
PR = 'http://schemas.openxmlformats.org/package/2006/relationships'
NS = {'s': S, 'x': X, 'a': A}
LIMIT = 600 * 1024 * 1024

def init_storage(con):
    con.executescript('''
      CREATE TABLE IF NOT EXISTS photographs(
        piece INTEGER PRIMARY KEY, content BLOB NOT NULL, thumbnail BLOB NOT NULL,
        digest TEXT NOT NULL, mime TEXT NOT NULL DEFAULT 'image/jpeg');
      CREATE TABLE IF NOT EXISTS inventory_baseline(
        piece INTEGER PRIMARY KEY, payload TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS inventory_photos(
        token TEXT NOT NULL, piece INTEGER NOT NULL, content BLOB NOT NULL,
        thumbnail BLOB NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(token,piece));
    ''')

def photograph(raw):
    if len(raw) > 40 * 1024 * 1024:
        raise ValueError('La fotografía supera 40 MB.')
    with Image.open(io.BytesIO(raw)) as source:
        if source.width * source.height > 40_000_000:
            raise ValueError('La fotografía supera 40 millones de píxeles.')
        reusable=(source.format=='JPEG' and source.mode=='RGB' and
                  max(source.size)<=1800 and not source.getexif())
        im = ImageOps.exif_transpose(source)
        if im.mode in ('RGBA', 'LA') or 'transparency' in im.info:
            rgba = im.convert('RGBA')
            im = Image.new('RGB', rgba.size, 'white')
            im.paste(rgba, mask=rgba.getchannel('A'))
        else:
            im = im.convert('RGB')
        im.thumbnail((1800,1800))
        out = io.BytesIO(); im.save(out, 'JPEG', quality=90, optimize=True)
        im.thumbnail((360,360))
        thumb = io.BytesIO(); im.save(thumb, 'JPEG', quality=82, optimize=True)
    # Keep an already optimized JPEG byte-for-byte so an exported inventory
    # can be reimported without false photograph conflicts or quality loss.
    content = raw if reusable else out.getvalue()
    return content, thumb.getvalue(), hashlib.sha256(content).hexdigest()

def store_photo(con, pid, photo):
    con.execute('INSERT INTO photographs(piece,content,thumbnail,digest) VALUES(?,?,?,?) '
                'ON CONFLICT(piece) DO UPDATE SET content=excluded.content, '
                'thumbnail=excluded.thumbnail,digest=excluded.digest', (pid,*photo))

def relationships(z, part):
    folder, name = posixpath.split(part)
    rel = posixpath.join(folder, '_rels', name+'.rels')
    if rel not in z.namelist(): return {}
    root = ET.fromstring(z.read(rel))
    return {e.get('Id'): posixpath.normpath(posixpath.join(folder,e.get('Target',''))).lstrip('/')
            if not e.get('Target','').startswith('/') else e.get('Target').lstrip('/')
            for e in root if e.get('TargetMode') != 'External'}

def rows(z, part, strings):
    # Streaming avoids loading the original workbook's large drawing tree.
    with z.open(part) as stream:
        for event, el in ET.iterparse(stream, events=('end',)):
            if el.tag != '{'+S+'}row': continue
            values = {}
            for cell in el:
                ref = cell.get('r',''); col = ref.rstrip('0123456789')
                value = cell.find('{'+S+'}v')
                value = value.text if value is not None else None
                kind = cell.get('t')
                if kind == 's': value = strings[int(value)] if value else ''
                elif kind == 'inlineStr': value = ''.join(t.text or '' for t in cell.iter('{'+S+'}t'))
                elif kind == 'b': value = value == '1'
                if kind != 'e': values[col] = value
            yield int(el.get('r','0')), values
            el.clear()

class SeekableUpload:
    def __init__(self, source): self.source=source
    def read(self, *args): return self.source.read(*args)
    def seek(self, *args): return self.source.seek(*args)
    def tell(self): return self.source.tell()
    def seekable(self): return True

def read_inventory(source, fallback, headers, clean, normalize, categories):
    if hasattr(source,'read'):source=SeekableUpload(source)
    try:
        return _read_inventory(source,fallback,headers,clean,normalize,categories)
    except (zipfile.BadZipFile,KeyError,ET.ParseError,IndexError) as exc:
        raise ValueError('El archivo no contiene un inventario Excel válido o está dañado.') from exc

def _read_inventory(source, fallback, headers, clean, normalize, categories):
    records=[]; errors=[]; notices=[]; photo_refs={}; embedded={}; fields={}; seen=set()
    with zipfile.ZipFile(source) as z:
        infos=z.infolist()
        if len(infos)>25000 or sum(i.file_size for i in infos)>1024*1024*1024:
            raise ValueError('El inventario supera los límites de lectura segura (1 GB descomprimido).')
        strings=[]
        if 'xl/sharedStrings.xml' in z.namelist():
            for _,el in ET.iterparse(z.open('xl/sharedStrings.xml'),events=('end',)):
                if el.tag=='{'+S+'}si':
                    strings.append(''.join(t.text or '' for t in el.iter('{'+S+'}t')));el.clear()
        book=ET.fromstring(z.read('xl/workbook.xml')); rels=relationships(z,'xl/workbook.xml')
        selected=None; mapping=None; start=0; sheet_name=''; sheet_rows=[]
        for sheet in book.find('{'+S+'}sheets'):
            part=rels.get(sheet.get('{'+R+'}id'))
            if not part or part not in z.namelist(): continue
            iterator=rows(z,part,strings)
            recent=[]
            for line,vals in iterator:
                if line>30:break
                recent.append((line,vals));recent=recent[-3:]
                candidate={col:headers[clean(v)] for col,v in vals.items() if clean(v) in headers}
                if not ('id' in candidate.values() and 'name' in candidate.values()):
                    columns=set().union(*(v.keys() for _,v in recent))
                    for col in columns:
                        cells=[str(v.get(col) or '').strip() for _,v in recent]
                        possibilities=[clean(v) for v in reversed(cells) if v]+[clean(' '.join(cells))]
                        match=next((headers[v] for v in possibilities if v in headers),None)
                        if match:candidate[col]=match
                if 'id' in candidate.values() and 'name' in candidate.values():
                    if len(candidate.values())!=len(set(candidate.values())):
                        raise ValueError('Hay encabezados repetidos que identifican el mismo campo. Revise la fila '+str(line)+'.')
                    selected=part;mapping=candidate;start=line;sheet_name=sheet.get('name');break
            if selected:break
        if not selected:
            raise ValueError('No se identificó la tabla. Conserve los encabezados Consecutivo y Nombre de la Pieza en las primeras 30 filas.')
        for line,vals in rows(z,selected,strings):
            if line<=start:continue
            if line>20002:raise ValueError('El inventario admite hasta 20 000 filas.')
            raw={key:vals.get(col) for col,key in mapping.items()}
            if not raw.get('id') and not raw.get('name'):continue
            if not raw.get('id') and clean(raw.get('name')) in ('total','totales','total general'):
                notices.append('Se omitió la fila de totales '+str(line)+'. No es un registro de pieza.')
                continue
            try:
                raw={k:v for k,v in raw.items() if v is not None and str(v).strip()!=''}
                pid=int(raw.get('id',''))
                if str(raw.get('id')).strip() not in (str(pid),str(pid)+'.0') or pid<1:
                    raise ValueError('Consecutivo inválido.')
                if pid in seen:raise ValueError('Consecutivo repetido en el inventario.')
                seen.add(pid)
                if str(raw.get('top100Position','')).strip().lower() in ('n/a','na','no aplica'):
                    raw.pop('top100Position',None)
                if 'catalog' not in raw and fallback:raw['catalog']=fallback
                notes=[]
                if str(raw.get('catalog','')).strip().lower() in ('si','sí','no','true','false'):
                    notes.append('Valor original de Catálogo: '+str(raw['catalog'])+'. Requiere validación.')
                    raw['catalog']='Pendiente de validación';raw['visibility']='draft'
                elif str(raw.get('catalog','')).strip().lower()=='v1.0':raw['catalog']='V1.0'
                if 'category' in raw:
                    match=next((cat for cat in categories if clean(raw['category']).endswith(clean(cat))),None)
                    if match:raw['category']=match
                    else:raw['category']=str(raw['category']).strip()
                if notes:
                    raw['reviewNotes']=' '.join(notes)
                    notices.append('Consecutivo '+str(pid)+': '+raw['reviewNotes'])
                path=str(raw.pop('sourceImage','') or '').replace('\\','/')
                if path:photo_refs[pid]=path.rsplit('/',1)[-1]
                # Validate supplied fields with placeholders; missing columns remain absent.
                trial={'id':pid,'name':'Registro pendiente','catalog':'Sin clasificar',
                       'category':categories[0],'quantity':1,**raw}
                checked=normalize(trial)
                if 'top100Position' in raw:
                    checked['top100Position']=checked.get('top100Position')
                entry={k:checked[k] for k in raw if k in checked}
                for key in ('priceLoose','pricePack','acquisitionPrice','salePrice'):
                    if key in raw:entry[key+'State']=checked.get(key+'State','')
                for rank_key in ('top10','top25','top50','top100'):
                    if rank_key+'Rank' in checked:entry[rank_key+'Rank']=checked[rank_key+'Rank']
                entry['id']=pid
                fields[pid]=list(entry)
                records.append(entry)
            except (ValueError,TypeError,OverflowError) as e:
                errors.append({'hoja':sheet_name,'fila':line,'error':str(e) or 'Consecutivo inválido.'})
        # Existing inventory explicitly maps each record to an embedded image filename.
        for pid,name in photo_refs.items():
            path='xl/media/'+name
            if path in z.namelist():embedded[pid]=path
            else:
                candidates=[part for part in z.namelist() if part.startswith('xl/media/') and
                            posixpath.splitext(posixpath.basename(part))[0]==posixpath.splitext(name)[0]]
                if len(candidates)==1:embedded[pid]=candidates[0]
        # Exported inventories and conventional Excel pictures use drawing anchors.
        # Parse incrementally: original drawing XML is approximately 85 MB.
        sheet_rels=relationships(z,selected)
        row_ids={}
        id_col=next(col for col,key in mapping.items() if key=='id')
        for line,vals in rows(z,selected,strings):
            try:row_ids[line]=int(vals.get(id_col,''))
            except (ValueError,TypeError):pass
        if len(embedded)<len(records):
            for drawing in sheet_rels.values():
                if not drawing.startswith('xl/drawings/') or not drawing.endswith('.xml'):continue
                drawing_rels=relationships(z,drawing)
                for _,el in ET.iterparse(z.open(drawing),events=('end',)):
                    if el.tag not in ('{'+X+'}oneCellAnchor','{'+X+'}twoCellAnchor'):continue
                    row=el.find('x:from/x:row',NS);blips=el.findall('.//a:blip',NS)
                    if row is not None and row.text and blips:
                        pid=row_ids.get(int(row.text)+1)
                        image=drawing_rels.get(blips[0].get('{'+R+'}embed'))
                        if pid and image in z.namelist():
                            if pid in embedded and embedded[pid]!=image:
                                notices.append('El registro '+str(pid)+' tiene varias fotografías. Se conserva la indicada en Ruta de imagen.')
                            else:embedded[pid]=image
                    el.clear()
        photos={};originals={}
        for pid,path in embedded.items():
            try:
                raw=z.read(path);photos[pid]=photograph(raw)
                with Image.open(io.BytesIO(raw)) as im:mime=Image.MIME.get(im.format,'application/octet-stream')
                originals[pid]=(raw,mime)
            except Exception as e:errors.append({'hoja':sheet_name,'fila':str(pid),'error':'Fotografía del consecutivo '+str(pid)+': '+str(e)})
    return {'records':records,'errors':errors,'headers':list(mapping.values()),
            '_originals':originals, 'photosPending':len(set(photo_refs)-set(photos)), 'notices':notices}, photos

def merge_plan(records, existing, baseline, normalize):
    result=[];conflicts=[];errors=[]
    for incoming in records:
        pid=incoming['id'];old=existing.get(pid)
        if old is None:
            try:
                new=dict(incoming)
                missing=[]
                if not new.get('catalog'):new['catalog']='Pendiente de validación';missing.append('Catálogo no indicado.')
                if not new.get('category'):new['category']='Pendiente de clasificación';missing.append('Categoría no indicada.')
                if missing:
                    new['visibility']='draft';new['reviewNotes']=' '.join(missing)
                result.append(normalize(new))
            except ValueError as e:errors.append({'hoja':'Inventario','fila':str(pid),'error':str(e)})
            continue
        merged=dict(old);base=baseline.get(pid,{})
        for key,value in incoming.items():
            if key=='id' or value in ('',None):continue
            current=old.get(key)
            if current==value:continue
            # First contact: differences in populated fields require a decision.
            # Later: compare Excel and system against their last common value.
            if current not in ('',None) and (key not in base or
                    (current!=base[key] and value!=base[key])):
                conflicts.append({'id':pid,'field':key,'system':current,'excel':value})
            elif key not in base or value!=base[key]:merged[key]=value
        try:result.append(normalize(merged,merged))
        except ValueError as e:errors.append({'hoja':'Inventario','fila':str(pid),'error':str(e)})
    return result,conflicts,errors

EXPORT_COLUMNS=[('Consecutivo','id'),('Consecutivo Top 100','top100Position'),
 ('Nombre de la Pieza','name'),('Categoría Curatorial','category'),('Subcategoría','subcategory'),
 ('Año aproximado / Periodo de Tiempo','period'),('Autor / Fabricante','maker'),
 ('Dimensiones / Escala / Capacidad','dimensions'),('Material de elaboración','material'),
 ('Estado de Conservación','condition'),('Narrativa Temática','narrative'),
 ('Relevancia curatorial','relevance'),('Observaciones Curatoriales','curatorial'),
 ('Cédula de Exhibición','description'),('Fotografía','photo'),('Observaciones','observations'),
 ('Piezas','quantity'),('Top 10','top10'),('Top 25','top25'),('Top 50','top50'),('Top 100','top100'),
 ('Pre-PEMEX','pre'),('Suelto','loose'),('En Empaque','pack'),('Precio Suelto','priceLoose'),
 ('Precio en Empaque','pricePack'),('Precio de adquisición','acquisitionPrice'),('CATALOGO','catalog'),('Disponibilidad comercial','sale'),
 ('Cantidad en venta','saleQuantity'),('Precio de venta','salePrice'),('Visibilidad','visibility'),
 ('Observaciones de validación','reviewNotes'),('Procedencia','provenance'),('Fuentes documentales','sources'),('Certeza documental','evidence'),('Tipo de objeto','objectType'),('Fecha del acontecimiento','eventDate'),('Notas de datación','datingNotes'),('Año inicial','yearStart'),('Año final','yearEnd'),('Ubicación física','location'),('Motivo de selección','topReason'),('Fecha de adquisición','acquisitionDate'),('Fecha de publicación','publicationDate')]

def export_inventory(records, con, target, on_progress=None):
    from openpyxl import Workbook
    from openpyxl.drawing.image import Image as ExcelImage
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter
    import os, tempfile
    wb=Workbook();ws=wb.active;ws.title='Inventario de piezas'
    ws.append([label for label,key in EXPORT_COLUMNS])
    progress=on_progress or (lambda *args:None)
    progress('Leyendo las fotografías del inventario',0,len(records))
    photos={}
    ids_list=[r['id'] for r in records]
    for offset in range(0,len(ids_list),900):
        chunk=ids_list[offset:offset+900]
        photos.update(con.execute('SELECT piece,content FROM photographs WHERE piece IN ('+','.join('?' for _ in chunk)+')',chunk))
    for line,record in enumerate(records,2):
        if line%10==2:progress('Integrando registros y fotografías',line-2,len(records))
        values=[record.get(key+'Rank',record.get(key)) if key in ('top10','top25','top50','top100') and record.get(key) else record.get(key) for _,key in EXPORT_COLUMNS]
        for index,(_,key) in enumerate(EXPORT_COLUMNS):
            if key in ('priceLoose','pricePack','acquisitionPrice','salePrice') and record.get(key) is None:
                values[index]='Por confirmar' if record.get(key+'State')=='pending' else 'N/A' if record.get(key+'State')=='na' else None
        # Prevent text from becoming an executable Excel formula.
        ws.append(values)
        for cell,value in zip(ws[line],values):
            if isinstance(value,str):cell.data_type='s'
            cell.alignment=Alignment(vertical='top',wrap_text=True)
        if record['id'] in photos:
            image=ExcelImage(io.BytesIO(photos[record['id']]))
            ratio=min(135/image.width,95/image.height)
            image.width*=ratio;image.height*=ratio;ws.add_image(image,'O'+str(line))
            ws.row_dimensions[line].height=76
    for cell in ws[1]:
        cell.font=Font(color='FFFFFF',bold=True);cell.fill=PatternFill('solid',fgColor='006341')
        cell.alignment=Alignment(wrap_text=True,vertical='center')
    ws.row_dimensions[1].height=42
    for col,(_,key) in enumerate(EXPORT_COLUMNS,1):
        ws.column_dimensions[get_column_letter(col)].width=42 if key in ('name','description','curatorial','observations') else 23
    ws.freeze_panes='D2';ws.auto_filter.ref=ws.dimensions
    ids={r['id'] for r in records}
    for title,table,columns in [('Ejemplares','units',['id','piece','code','location','condition','notes']),('Movimientos','movements',['id','piece','unit','origin','destination','note','occurred','username']),('Conservación','conservation',['id','piece','reviewed','condition','damage','action','username'])]:
        extra=wb.create_sheet(title);extra.append(columns)
        for row in con.execute('SELECT '+','.join(columns)+' FROM '+table):
            if row[1] in ids:
                extra.append(list(row))
                for c in extra[extra.max_row]:
                    if isinstance(c.value,str):c.data_type='s'
        extra.freeze_panes='A2';extra.auto_filter.ref=extra.dimensions
        for c in extra[1]:c.font=Font(bold=True)
        for i in range(1,len(columns)+1):extra.column_dimensions[get_column_letter(i)].width=28
    extra=wb.create_sheet('Acerca de esta exportación')
    extra.append(['La hoja principal permite actualizar el inventario.'])
    extra.append(['Ejemplares, movimientos y conservación son hojas de consulta. No se reimportan.'])
    extra.append(['El respaldo ZIP conserva toda la base: galerías, originales y seguimiento.'])
    target=Path(target);target.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(suffix='.xlsx',dir=target.parent);os.close(fd)
    try:
        progress('Guardando y comprimiendo el Excel',len(records),len(records))
        from openpyxl.writer.excel import ExcelWriter
        with zipfile.ZipFile(tmp,'w',zipfile.ZIP_DEFLATED,compresslevel=1) as archive:ExcelWriter(wb,archive).save()
        os.replace(tmp,target)
    finally:
        if Path(tmp).exists():Path(tmp).unlink()
    wb.close()
    return target
