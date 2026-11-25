"""Lectura pública del catálogo desde los campos publicados del inventario."""
from pathlib import Path
from threading import Lock
import hashlib
import json
import re
from flask import render_template
from catalogo_word import build_catalog, NS

FIELDS=set('id name catalog category subcategory period maker dimensions material condition narrative relevance curatorial description quantity pre visibility image top100 top100Rank top100Position'.split())

def register_public_catalog(app,items):
    cache={};lock=Lock();template=Path(app.root_path)/'plantillas/catalogo_base.docx'

    @app.get('/catalogo')
    def public_catalog():
        records=[{key:value for key,value in p.items() if key in FIELDS} for p in items() if p.get('visibility')=='visible']
        records.sort(key=lambda p:p['id'])
        top=[p for p in records if p.get('top100')]
        top.sort(key=lambda p:(p.get('top100Rank') or 9999,p['id']))
        events=app.collection_events()
        signature=hashlib.sha256(json.dumps([records,events],sort_keys=True,ensure_ascii=False).encode()).hexdigest()+str(template.stat().st_mtime_ns)
        with lock:
            if cache.get('signature')==signature:
                parts=cache['parts'];notice=cache['notice']
            else:
                parts=[];notice=''
                if records:
                    try:
                        document=build_catalog(template,records,{},None,return_document=True,allow_partial_top=True,exhibitions=events)
                        skip_index=False;intro_count=0;cards_added=False
                        for node in document.find('w:body',NS):
                            text=''.join(node.xpath('.//w:t/text()',namespaces=NS)).strip()
                            if node.tag.endswith('}tbl'):
                                if 'N.º de Top:' in text:
                                    if not cards_added:parts.append(dict(kind='cards'));cards_added=True
                                else:parts.append(dict(kind='listing'))
                                continue
                            if not text:continue
                            if text=='ÍNDICE':skip_index=True;continue
                            if skip_index:
                                if text=='1. Introducción':intro_count+=1
                                if intro_count<2:continue
                                skip_index=False
                            heading=bool(re.match(r'^\d+(?:\.\d+)*\.?\s',text)) or text in ('Página legal y créditos','Prólogo del Coleccionista','Texto del coleccionista','Edición del catálogo','Nota editorial')
                            bullet=text.startswith('*') or node.find('w:pPr/w:numPr',NS) is not None
                            parts.append(dict(kind='heading' if heading else 'bullet' if bullet else 'text',text=text.lstrip('* ').strip() if bullet else text))
                    except ValueError:
                        notice='La selección del Top 100 está en revisión. Se muestran las piezas publicadas del inventario.'
                        parts=[dict(kind='cards'),dict(kind='listing')]
                cache.update(signature=signature,parts=parts,notice=notice)
        return render_template('catalogo_publico.html',records=records,top=top,parts=parts,notice=notice,units=sum(int(p.get('quantity') or 0) for p in records))
