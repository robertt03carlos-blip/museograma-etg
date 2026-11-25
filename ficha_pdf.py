"""Ficha de catálogo PDF independiente de los márgenes del navegador."""
from io import BytesIO
from html import escape
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, HRFlowable

GREEN=colors.HexColor('#194b3d')
CREAM=colors.HexColor('#f3eddf')
COPPER=colors.HexColor('#9c6243')

def make_piece_pdf(piece,photo=None,show_prices=False):
    def text(value):
        return escape(str(value if value is not None else '')).replace('\n','<br/>')
    def price(key):
        state=piece.get(key+'State');value=piece.get(key)
        if state=='pending':return 'Por confirmar'
        if state=='na' or value is None:return 'N/A'
        return '${:,.2f} MXN'.format(float(value))
    def story(size):
        normal=ParagraphStyle('body',fontName='Helvetica',fontSize=size,leading=size*1.28,textColor=GREEN,spaceAfter=4)
        small=ParagraphStyle('small',parent=normal,fontSize=max(7,size-1),leading=max(9,size*1.1),spaceAfter=3)
        title=ParagraphStyle('title',fontName='Times-Roman',fontSize=18 if size>=9 else 16,leading=21 if size>=9 else 18,textColor=GREEN,spaceAfter=9)
        heading=ParagraphStyle('heading',parent=normal,fontName='Helvetica-Bold',fontSize=size+1,leading=size+4,spaceBefore=7,spaceAfter=3,keepWithNext=True)
        P=lambda value,style=normal:Paragraph(text(value),style)
        elements=[P('COLECCIÓN PRIVADA',small),Paragraph('Eduardo Reinhardt Thomae González',ParagraphStyle('owner',fontName='Times-Roman',fontSize=13,leading=16,textColor=GREEN,spaceAfter=5)),HRFlowable(width='100%',thickness=1.3,color=COPPER),Spacer(1,5*mm),P('CATÁLOGO '+str(piece.get('catalog',''))+'  /  N.º '+str(piece['id'])+'  /  '+str(piece.get('category','')),small),P(piece['name'],title)]
        tops=[]
        for n in (10,25,50,100):
            key='top'+str(n)
            if piece.get(key):
                rank=piece.get(key+'Rank')
                tops.append('Top '+str(rank)+' de '+str(n) if rank else 'Top '+str(n)+' · posición pendiente')
        if tops:elements += [P('   |   '.join(tops),small),Spacer(1,3*mm)]
        if photo:
            image=Image(BytesIO(photo));scale=min(65*mm/image.imageWidth,66*mm/image.imageHeight)
            image.drawWidth=image.imageWidth*scale;image.drawHeight=image.imageHeight*scale
            image.hAlign='CENTER';left=[image]
        else:left=[P('Fotografía no registrada',small)]
        if show_prices:
            prices=Table([[P('Precio Suelto',small),P(price('priceLoose'),small)],[P('Precio en Empaque',small),P(price('pricePack'),small)]],colWidths=[34*mm,33*mm])
            prices.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),CREAM),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),5),('RIGHTPADDING',(0,0),(-1,-1),5),('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),4)]))
            left += [Spacer(1,3*mm),prices]
        fields=[('Subcategoría','subcategory'),('Año aproximado / Periodo de Tiempo','period'),('Autor / Fabricante','maker'),('Dimensiones / Escala / Capacidad','dimensions'),('Material de elaboración','material'),('Estado de Conservación','condition'),('Piezas','quantity')]
        rows=[]
        for label,key in fields:
            value=piece.get(key)
            if value not in (None,''):rows.append([Paragraph('<b>'+text(label)+'</b><br/>'+text(value),small)])
        right=Table(rows,colWidths=[109*mm]);right.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LINEBELOW',(0,0),(-1,-1),.3,colors.HexColor('#d9d8ca')),('LEFTPADDING',(0,0),(-1,-1),0),('TOPPADDING',(0,0),(-1,-1),3),('BOTTOMPADDING',(0,0),(-1,-1),4)]))
        details=Table([[left,right]],colWidths=[70*mm,114*mm]);details.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),5),('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),5)]))
        elements.append(details)
        for label,key in [('Cédula de Exhibición','description'),('Narrativa Temática','narrative'),('Relevancia curatorial','relevance'),('Observaciones Curatoriales','curatorial'),('Observaciones','observations')]:
            if piece.get(key):elements += [P(label,heading),P(piece[key])]
        return elements
    def draw_footer(canvas,doc):
        canvas.setStrokeColor(COPPER);canvas.setLineWidth(.5);canvas.line(13*mm,12*mm,197*mm,12*mm)
        canvas.setFillColor(GREEN);canvas.setFont('Helvetica',7)
        canvas.drawString(13*mm,8*mm,'Colección privada · Ficha '+str(piece['id']))
        canvas.drawRightString(197*mm,8*mm,'Página '+str(doc.page))
    # Se reduce solo hasta un tamaño legible; si hace falta, se conserva el texto en más páginas.
    from pypdf import PdfReader
    selected=None
    for size in (10,9.5,9,8.5):
        out=BytesIO();doc=SimpleDocTemplate(out,pagesize=A4,leftMargin=13*mm,rightMargin=13*mm,topMargin=12*mm,bottomMargin=17*mm,title=piece['name'],author='Museograma')
        doc.build(story(size),onFirstPage=draw_footer,onLaterPages=draw_footer)
        selected=out.getvalue()
        if len(PdfReader(BytesIO(selected)).pages)==1:break
    return selected
