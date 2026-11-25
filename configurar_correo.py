"""Configura SMTP localmente sin mostrar ni enviar contraseñas al chat."""
from getpass import getpass
from pathlib import Path
import os
import re
import tempfile


def main():
    print('Configurar correo de Museograma. Los datos se guardan solo en este equipo.')
    print('Usa las credenciales SMTP que indique tu proveedor; no tu contraseña habitual.')
    host=input('Servidor SMTP: ').strip()
    port=input('Puerto TLS (587 o 465) [587]: ').strip() or '587'
    sender=input('Correo remitente: ').strip()
    user=input('Usuario SMTP [correo remitente]: ').strip() or sender
    secret=getpass('Contraseña SMTP o contraseña de aplicación (oculta): ')
    values=dict(MUSEO_SMTP_HOST=host,MUSEO_SMTP_PORT=port,MUSEO_MAIL_FROM=sender,
                MUSEO_SMTP_USER=user,MUSEO_SMTP_PASSWORD=secret)
    if not host or port not in ('587','465') or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',sender) or not secret:
        raise ValueError('Revisa el servidor, puerto, correo y contraseña SMTP.')
    if any('\n' in v or '\r' in v for v in values.values()):
        raise ValueError('Los campos no pueden contener saltos de línea.')
    target=Path(__file__).resolve().parent/'.env'
    lines=target.read_text(encoding='utf-8-sig').splitlines() if target.exists() else []
    lines=[line for line in lines if line.partition('=')[0].strip() not in values]
    lines.extend(key+'='+value for key,value in values.items())
    fd,name=tempfile.mkstemp(prefix='.correo_',dir=target.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf8') as output:
            output.write('\n'.join(lines)+'\n')
        os.replace(name,target)
    finally:
        Path(name).unlink(missing_ok=True)
    print('Configuración guardada. Reinicia Museograma para aplicarla.')
    print('Todavía no se ha probado el envío. Entra en Datos de recuperación y verifica tu correo.')


if __name__=='__main__':
    try:
        main()
    except (ValueError,EOFError,KeyboardInterrupt,OSError):
        print('No se completó la configuración. Revisa los datos y vuelve a intentarlo.')
        raise SystemExit(1)
