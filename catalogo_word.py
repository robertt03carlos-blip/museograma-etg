"""Actualización editorial del catálogo conservando las partes del Word maestro.

Los registros y fotografías son una instantánea del inventario. No modifica
el maestro, el Excel, los registros, los precios ni las cuentas del sistema.
"""
from copy import deepcopy
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import io
import re
import xml.etree.ElementTree as ET

W='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
WP='http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing'
A='http://schemas.openxmlformats.org/drawingml/2006/main'
PIC='http://schemas.openxmlformats.org/drawingml/2006/picture'
REL='http://schemas.openxmlformats.org/package/2006/relationships'
NS={'w':W,'r':R,'wp':WP,'a':A,'pic':PIC}
GREEN='294F20'
CREAM='EDF3E9'

def _engine():
    # lxml preserves namespace declarations and opaque package structures.
    from lxml import etree
    return etree

def build_catalog(template, records, photos, target, on_progress=None, *, return_document=False, allow_partial_top=False, exhibitions=None):
    E=_engine();progress=on_progress or (lambda *args:None)
    records=sorted(records,key=lambda p:int(p['id']))
    ids=[int(p['id']) for p in records]
    if len(set(ids))!=len(ids):raise ValueError('Hay consecutivos de inventario repetidos.')
    top=[p for p in records if p.get('top100') and str(p.get('top100Rank') or p.get('top100Position') or '').isdigit()]
    ranks=[int(p.get('top100Rank') or p.get('top100Position')) for p in top]
    valid_top=(len(top)<=100 and len(set(ranks))==len(ranks) and all(1<=rank<=100 for rank in ranks)) if allow_partial_top else (len(top)==100 and set(ranks)==set(range(1,101)))
    if not valid_top:
        raise ValueError('El Word requiere exactamente cien piezas del Top 100, con posiciones únicas del 1 al 100. Revisa el inventario antes de generar el catálogo.')
    top.sort(key=lambda p:int(p.get('top100Rank') or p.get('top100Position')))
    with ZipFile(template) as z:parts={n:z.read(n) for n in z.namelist()}
    root=E.fromstring(parts['word/document.xml']);body=root.find('w:body',NS)
    rels=E.fromstring(parts['word/_rels/document.xml.rels'])
    content_types=E.fromstring(parts['[Content_Types].xml'])
    def el(tag,**attrs):
        n=E.Element('{'+W+'}'+tag)
        for k,v in attrs.items():n.set('{'+W+'}'+k,str(v))
        return n
    def text(n):return ''.join(n.xpath('.//w:t/text()',namespaces=NS))
    def set_text(n,value):
        nodes=n.xpath('.//w:t',namespaces=NS)
        if nodes:
            nodes[0].text=str(value)
            for x in nodes[1:]:x.text=''
        else:
            p=n if n.tag=='{'+W+'}p' else E.SubElement(n,'{'+W+'}p')
            r=E.SubElement(p,'{'+W+'}r');E.SubElement(r,'{'+W+'}t').text=str(value)
    def paragraph(value='',size=20,bold=False,color='222222',align=None,keep=False):
        p=el('p');pr=el('pPr');p.append(pr)
        pr.append(el('spacing',before=0,after=0,line=220,lineRule='auto'))
        if align:pr.append(el('jc',val=align))
        if keep:pr.append(el('keepNext'))
        r=el('r');p.append(r);rp=el('rPr');r.append(rp)
        rp.append(el('rFonts',ascii='Calibri',hAnsi='Calibri',cs='Calibri'))
        rp.append(el('sz',val=size));rp.append(el('color',val=color))
        if bold:rp.append(el('b'))
        t=el('t');t.text=str(value or '');t.set('{http://www.w3.org/XML/1998/namespace}space','preserve');r.append(t)
        return p
    def cell(value='',span=1,width=13004,fill=None,bold=False,color='222222',size=20,align=None,merge=None):
        c=el('tc');pr=el('tcPr');c.append(pr);pr.append(el('tcW',w=width,type='dxa'))
        if span>1:pr.append(el('gridSpan',val=span))
        if fill:pr.append(el('shd',val='clear',fill=fill))
        if merge:pr.append(el('vMerge',**({'val':'restart'} if merge=='restart' else {})))
        pr.append(el('vAlign',val='center'))
        c.append(paragraph(value,size,bold,color,align));return c
    def row(*cells):
        r=el('tr');pr=el('trPr');pr.append(el('cantSplit'));r.append(pr)
        for c in cells:r.append(c)
        return r
    def add_image(cell,pid,width=178,height=124):
        raw=photos.get(int(pid))
        if not raw:
            set_text(cell,'Fotografía no registrada');return
        from PIL import Image,ImageOps
        with Image.open(io.BytesIO(raw)) as image:
            image=ImageOps.exif_transpose(image)
            iw,ih=image.size
            if image.mode!='RGB':image=image.convert('RGB')
            image.thumbnail((1100,1100));stream=io.BytesIO();image.save(stream,'JPEG',quality=92)
        media=f'media/catalogo_actual_{pid}.jpg';parts['word/'+media]=stream.getvalue()
        rid=f'rIdCatalogoActual{pid}'
        relationship=E.SubElement(rels,'{'+REL+'}Relationship');relationship.set('Id',rid);relationship.set('Type',R+'/image');relationship.set('Target',media)
        scale=min(width/iw,height/ih);cx=int(iw*scale*12700);cy=int(ih*scale*12700)
        for p in list(cell.findall('w:p',NS)):cell.remove(p)
        p=paragraph('',align='center');cell.append(p);r=p.find('w:r',NS)
        drawing=E.SubElement(r,'{'+W+'}drawing');inline=E.SubElement(drawing,'{'+WP+'}inline',distT='0',distB='0',distL='0',distR='0')
        E.SubElement(inline,'{'+WP+'}extent',cx=str(cx),cy=str(cy))
        E.SubElement(inline,'{'+WP+'}docPr',id=str(10000+int(pid)),name=f'Fotografía pieza {pid}',descr=f'Fotografía del registro {pid}')
        frame=E.SubElement(inline,'{'+WP+'}cNvGraphicFramePr');E.SubElement(frame,'{'+A+'}graphicFrameLocks',noChangeAspect='1')
        graphic=E.SubElement(inline,'{'+A+'}graphic');data=E.SubElement(graphic,'{'+A+'}graphicData',uri=PIC)
        pic=E.SubElement(data,'{'+PIC+'}pic');nv=E.SubElement(pic,'{'+PIC+'}nvPicPr')
        E.SubElement(nv,'{'+PIC+'}cNvPr',id='0',name=f'pieza_{pid}.jpg');E.SubElement(nv,'{'+PIC+'}cNvPicPr')
        fill=E.SubElement(pic,'{'+PIC+'}blipFill');E.SubElement(fill,'{'+A+'}blip',{'{'+R+'}embed':rid});stretch=E.SubElement(fill,'{'+A+'}stretch');E.SubElement(stretch,'{'+A+'}fillRect')
        sp=E.SubElement(pic,'{'+PIC+'}spPr');xf=E.SubElement(sp,'{'+A+'}xfrm');E.SubElement(xf,'{'+A+'}off',x='0',y='0');E.SubElement(xf,'{'+A+'}ext',cx=str(cx),cy=str(cy));geom=E.SubElement(sp,'{'+A+'}prstGeom',prst='rect');E.SubElement(geom,'{'+A+'}avLst')
    def category(value):
        # Use the original Excel category labels, including their category code.
        known=['Energía y Transporte','Hogar y vida cotidiana','Documentación y Archivo','Identidad y Cultura Laboral']
        return str(known.index(value)+1)+'.- '+value if value in known else value
    def card(p):
        t=el('tbl');pr=el('tblPr');t.append(pr);pr.append(el('tblW',w=13004,type='dxa'));pr.append(el('jc',val='center'));pr.append(el('tblLayout',type='fixed'))
        borders=el('tblBorders')
        for side in ['top','left','bottom','right','insideH','insideV']:borders.append(el(side,val='single',sz=4,color='B5BDAA'))
        pr.append(borders);margins=el('tblCellMar')
        for side in ['top','bottom']:margins.append(el(side,w=35 if len(p['name'])>150 else 55,type='dxa'))
        for side in ['left','right']:margins.append(el(side,w=85,type='dxa'))
        pr.append(margins);grid=el('tblGrid');t.append(grid)
        # These shared boundaries reproduce Excel B:D, E:H, I:N and B:G/H:N.
        widths=[2861,2861,910,6372]
        for width in widths:grid.append(el('gridCol',w=width))
        rank=int(p.get('top100Rank') or p.get('top100Position'))
        t.append(row(cell('COLECCIÓN PRIVADA ETG - PEMEX',span=4,bold=True,color=GREEN,size=30,align='center')))
        number=cell('FICHA N.º ',width=2861,fill=GREEN,bold=True,color='FFFFFF',size=19,align='center')
        number.append(paragraph(str(p['id']),size=32,bold=True,color='FFFFFF',align='center'))
        t.append(row(number,cell(p['name'],span=3,width=10143,fill=GREEN,bold=True,color='FFFFFF',size=26 if len(p['name'])>130 else 28,align='center')))
        t.append(row(cell('Top 100: Sí',span=2,width=5722,fill=CREAM,bold=True,color=GREEN,size=24,align='center'),cell(f'N.º de Top: {rank}',span=2,width=7282,fill=CREAM,bold=True,color=GREEN,size=24,align='center')))
        fields=[('Categoría curatorial','category'),('Subcategoría','subcategory'),('Año / período','period'),('Autor / fabricante','maker'),('Dimensiones / escala / capacidad','dimensions'),('Material de elaboración','material'),('Estado de conservación','condition')]
        total=len(str(p.get('curatorial','')))+len(str(p.get('description','')))
        body_size=21 if total<1700 else 20
        for i,(label,key) in enumerate(fields):
            photo=cell('',width=2861,merge='restart' if i==0 else 'continue')
            if i==0:add_image(photo,p['id'],width=130,height=124)
            t.append(row(photo,cell(label,span=2,width=3771,fill=CREAM,bold=True,color=GREEN,size=20),cell(category(p.get(key,'')) if key=='category' else p.get(key,'') or 'Sin información registrada',width=6372,size=body_size)))
        t.append(row(cell('NARRATIVA TEMÁTICA',span=2,width=5722,fill=CREAM,bold=True,color=GREEN,size=20,align='center'),cell('RELEVANCIA CURATORIAL',span=2,width=7282,fill=CREAM,bold=True,color=GREEN,size=20,align='center')))
        t.append(row(cell(p.get('narrative') or 'Sin información registrada',span=2,width=5722,size=body_size,align='center'),cell(p.get('relevance') or 'Sin información registrada',span=2,width=7282,size=body_size,align='center')))
        for label,key in [('OBSERVACIONES CURATORIALES','curatorial'),('CÉDULA DE EXHIBICIÓN','description')]:
            t.append(row(cell(label,span=4,fill=CREAM,bold=True,color=GREEN,size=20)))
            t.append(row(cell(p.get(key) or 'Sin información registrada',span=4,fill=CREAM if key=='description' else None,size=body_size)))
        return t

    paragraphs=list(body.findall('w:p',NS))
    # Preserve paragraph/run formatting when updating the small editorial slots.
    headings={
        '4.3 Juguetes, juegos didácticos y autotransporte a escala':'4.3 Modelos, didácticos y autotransporte a escala',
        '4.3 Modelos a escala, material didáctico y autotransporte':'4.3 Modelos, didácticos y autotransporte a escala',
        '4.4 Bombas, equipos, autotanques y señalización':'4.4 Bombas, equipos y señalización',
        '4.5 Publicidad, propaganda y material promocional':'4.5 Publicidad, propaganda y material gráfico',
        '4.6 Filatelia, documentos y archivo histórico':'4.6 Filatelia, documentos y archivo',
        '4.9 Memorabilia institucional e identidad corporativa':'4.9 Memorabilia Institucional e identidad corporativa',
        '4.9 Memorabilia institucional':'4.9 Memorabilia Institucional e identidad corporativa',
        '4.10 Piezas especiales y producciones artesanales':'4.10 Piezas especiales',
        '10.2 Notas metodológicas':'10.2 Créditos y agradecimientos',
        '10.3 Créditos y agradecimientos':'10.3 Notas metodológicas',
        'Las fichas se presentan agrupadas por categoría, siguiendo la estructura definida en el capítulo anterior, lo que facilita su lectura temática y su consulta especializada.':'Las fichas se presentan en el orden del Top 100 registrado en Excel. Cada ficha distingue el consecutivo original de inventario de su número dentro del Top 100, para facilitar su consulta y evitar confusiones entre ambos identificadores.',
        'Dentro de cada grupo de piezas o sección temática correspondiente':'En el orden definido por el número de Top 100 del registro maestro',
        'En orden cronológico aproximado o por relevancia':'Con el consecutivo original de inventario y el número de Top identificados por separado',
        'Con una ficha por página o por bloque, según diseño final':'Con una ficha por página',
        'No todas las piezas registradas en Excel aparecen necesariamente en el catálogo completo; algunas se integran como:':'Las fichas técnicas documentan la selección Top 100; el listado general del apartado 10.4 incluye todos los registros del inventario. La presentación editorial también contempla:',
    }
    for p in paragraphs:
        value=text(p)
        if value in headings:set_text(p,headings[value])
        elif 'La selección de las 50 piezas clave' in value:set_text(p,value.replace('50 piezas clave','100 piezas del Top 100'))
    # Preserve existing group descriptions. Add only factual group names if the
    # user registers a new subcategory not represented in the original template.
    paras=list(body.findall('w:p',NS))
    group_headings=[p for p in paras if re.match(r'^4\.\d+\s',text(p))]
    known={re.sub(r'^4\.\d+\s+','',text(p)).strip().casefold() for p in group_headings}
    current_groups=sorted({p.get('subcategory','').strip() for p in records if p.get('subcategory','').strip()},key=str.casefold)
    extra=[name for name in current_groups if name.casefold() not in known]
    if extra:
        chapter5=[p for p in paras if text(p)=='5. Narrativas temáticas de la colección']
        last_heading=group_headings[-1]
        for index,name in enumerate(extra,11):
            heading=deepcopy(last_heading);set_text(heading,f'4.{index} '+name)
            position=list(body).index(chapter5[-1]);body.insert(position,heading)
            count=sum(p.get('subcategory','').strip()==name for p in records)
            note=paragraph(f'Esta subcategoría reúne {count} registros del inventario actual. Los datos de sus piezas se incluyen en el listado general del apartado 10.4.',size=22)
            body.insert(position+1,note)
            if len(chapter5)>1:
                entry=deepcopy(group_headings[0]);set_text(entry,f'4.{index} '+name);body.insert(list(body).index(chapter5[0]),entry)
    # Synchronize section 5 with the distinct nonempty Excel narratives.
    themes=sorted({p.get('narrative','').strip() for p in records if p.get('narrative','').strip()},key=str.casefold)
    paras=list(body.findall('w:p',NS));starts=[i for i,p in enumerate(paras) if text(p)=='5. Narrativas temáticas de la colección'];start=starts[-1]
    bullets=[]
    for p in paras[start+1:]:
        value=text(p)
        if value.startswith('6.'):break
        if value.lstrip().startswith('*'):bullets.append(p)
    if not bullets:raise ValueError('No se encontró el listado de narrativas en la plantilla Word.')
    at=list(body).index(bullets[0]);base=deepcopy(bullets[0])
    for p in bullets:body.remove(p)
    for i,value in enumerate(themes):p=deepcopy(base);set_text(p,'* '+value);body.insert(at+i,p)
    # Section 6.2 describes groups and the narratives actually present in Top 100.
    paras=list(body.findall('w:p',NS));start=next(i for i,p in enumerate(paras) if text(p)=='6.2 Alcance de la selección')
    bullets=[]
    for p in paras[start+1:]:
        if text(p).startswith('6.3'):break
        if text(p).lstrip().startswith('*'):bullets.append(p)
    groups=sorted({p.get('subcategory','').strip() for p in top if p.get('subcategory','').strip()},key=str.casefold)
    at=list(body).index(bullets[0]);base=deepcopy(bullets[0])
    for p in bullets:body.remove(p)
    for i,value in enumerate(groups):p=deepcopy(base);set_text(p,'* '+value);body.insert(at+i,p)
    p=deepcopy(base);top_themes=sorted({p.get('narrative','').strip() for p in top if p.get('narrative','').strip()},key=str.casefold)
    set_text(p,'Las narrativas temáticas representadas en el Top 100 son: '+ '; '.join(top_themes)+'.');body.insert(at+len(groups),p)
    # Replace the full technical-card block, never append to the old selection.
    tables=body.findall('w:tbl',NS);cards=[t for t in tables if text(t).lstrip().startswith('FICHA')]
    if not cards:raise ValueError('La plantilla no contiene el bloque de fichas técnicas esperado.')
    children=list(body);first=children.index(cards[0]);last=children.index(cards[-1])
    for node in children[first:last+1]:body.remove(node)
    for index,p in enumerate(top):
        progress('Generando las cien fichas del Top 100',index,100)
        if index:
            br=paragraph('',size=2,keep=True)
            pr=br.find('w:pPr',NS);pr.append(el('pageBreakBefore'))
            spacing=pr.find('w:spacing',NS);spacing.set('{'+W+'}line','1');spacing.set('{'+W+'}lineRule','exact')
            body.insert(first,br);first+=1
        body.insert(first,card(p));first+=1
    # Rebuild every row of 10.4, keeping its six original columns and widths.
    listing=tables[-1];rows=listing.findall('w:tr',NS)
    if len(rows)<2:raise ValueError('No se encontró el listado general de seis columnas.')
    # Let the table flow inline so the collection total cannot overlap its last row.
    for position in listing.findall('w:tblPr/w:tblpPr',NS):position.getparent().remove(position)
    header=deepcopy(rows[0]);pattern=deepcopy(rows[1])
    for r in rows:listing.remove(r)
    pr=header.find('w:trPr',NS)
    if pr is None:pr=el('trPr');header.insert(0,pr)
    if pr.find('w:tblHeader',NS) is None:pr.append(el('tblHeader'))
    listing.append(header)
    for p in records:
        r=deepcopy(pattern)
        values=[str(p['id']),p.get('name',''),category(p.get('category','')),p.get('subcategory',''),p.get('period',''),p.get('condition','')]
        for c,value in zip(r.findall('w:tc',NS),values):set_text(c,value)
        for h in r.findall('w:trPr/w:trHeight',NS):h.set('{'+W+'}hRule','atLeast')
        pr=r.find('w:trPr',NS)
        if pr is None:pr=el('trPr');r.insert(0,pr)
        if pr.find('w:cantSplit',NS) is None:pr.append(el('cantSplit'))
        listing.append(r)
    total=sum(int(p.get('quantity') or 0) for p in records)
    at=list(body).index(listing)+1
    summary=paragraph(f'Total de la colección: {len(records)} registros de inventario y {total} piezas físicas. El total de piezas corresponde a la suma de la columna Piezas del Excel; un registro puede reunir más de una pieza.',size=20,bold=True,color=GREEN)
    summary.find('w:pPr/w:spacing',NS).set('{'+W+'}before','120')
    body.insert(at,summary)
    if exhibitions is not None:
        anchors={}
        for node in body.findall('w:p',NS):
            value=text(node)
            if value.startswith('En junio de 2024, la colección'):anchors['los-pinos']=node
            if value.startswith('Posteriormente, con motivo del 87'):anchors['centro-administrativo']=node
        event_map={e['id']:e for e in exhibitions}
        for eid,node in anchors.items():set_text(node,event_map[eid]['description'] if eid in event_map else '')
        anchor=anchors.get('centro-administrativo')
        if anchor is not None:
            pos=list(body).index(anchor)+1
            for event in exhibitions:
                if event.get('id') not in anchors:
                    node=deepcopy(anchor);set_text(node,event['title']+'. '+event['description']);body.insert(pos,node);pos+=1
    if return_document:return root
    # Mark fields for refresh on opening without rebuilding unrelated parts.
    if 'word/settings.xml' in parts:
        settings=E.fromstring(parts['word/settings.xml']);u=settings.find('w:updateFields',NS)
        if u is None:u=el('updateFields');settings.append(u)
        u.set('{'+W+'}val','true');parts['word/settings.xml']=E.tostring(settings,xml_declaration=True,encoding='UTF-8',standalone=True)
    if not any(n.get('Extension')=='jpg' for n in content_types):
        E.SubElement(content_types,'{http://schemas.openxmlformats.org/package/2006/content-types}Default',Extension='jpg',ContentType='image/jpeg')
    parts['word/document.xml']=E.tostring(root,xml_declaration=True,encoding='UTF-8',standalone=True)
    parts['word/_rels/document.xml.rels']=E.tostring(rels,xml_declaration=True,encoding='UTF-8',standalone=True)
    parts['[Content_Types].xml']=E.tostring(content_types,xml_declaration=True,encoding='UTF-8',standalone=True)
    with ZipFile(target,'w',ZIP_DEFLATED,compresslevel=3) as z:
        for name,raw in parts.items():z.writestr(name,raw)
    progress('Catálogo Word generado',100,100)
    return dict(records=len(records),units=total,top100=len(top),themes=themes,topGroups=groups)
