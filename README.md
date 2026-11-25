# Edición de recuperación por correo

Esta copia se trabaja en Descargas/Museograma_Correo.
Consulta ../EMPEZAR_AQUI.txt. Ejecuta 6_CONFIGURAR_CORREO.cmd para configurar
el remitente SMTP localmente. Luego reinicia y verifica el correo de cada cuenta.
No se han enviado correos reales ni se incluyen credenciales del ZIP original.
La interfaz de esta edición usa correo; los proveedores telefónicos del código
anterior se conservan para una integración posterior.

# Museograma: proyecto completo para Visual Studio Code

Esta carpeta contiene el código fuente completo, los módulos Python, las plantillas,
JavaScript, CSS, la portada y la lista de dependencias. El catálogo está vacío.
Las bibliotecas de terceros se instalan desde `requirements.txt`; no se incluye un
entorno virtual de otra computadora. La instalación inicial requiere internet.

## Abrir y ejecutar en Windows

1. Haz clic derecho en el ZIP y selecciona **Extraer todo**. Abre la carpeta extraída
   `Museograma_VSCode`. No ejecutes archivos dentro del ZIP.
2. Comprueba que `app.py`, `requirements.txt` y `preparar_entorno.py` están juntos.
3. Ejecuta `1_PREPARAR_ENTORNO.cmd`. Se requiere Python 3.11 o posterior.
4. En Visual Studio Code elige **Archivo → Abrir carpeta** y selecciona
   `Museograma_VSCode`, o abre `Museograma.code-workspace`.
5. Instala las extensiones Python y Python Debugger de Microsoft recomendadas.
6. Pulsa **F5** y elige **Abrir Museograma**. También puedes ejecutar
   `2_ABRIR_MUSEOGRAMA.cmd` sin Visual Studio Code.
7. Abre http://127.0.0.1:8765/ si el navegador no se abre automáticamente.

La configuración F5 usa el Python del entorno virtual de esta carpeta. Si eliges
otro intérprete manualmente, selecciona `.venv\Scripts\python.exe`.

## Alternativa desde la terminal de Visual Studio Code

En Windows, situada en la carpeta que contiene este README:

```powershell
py -3 preparar_entorno.py
.\.venv\Scripts\python.exe app.py
```

En macOS/Linux:

```sh
python3 preparar_entorno.py
.venv/bin/python app.py
```

No necesitas activar el entorno ni cambiar la política de ejecución de PowerShell.
En **Terminal → Ejecutar tarea** también están las tareas de preparar y probar.

## Si aparece «Could not open requirements file»

Ese mensaje indica que falta el archivo en la ubicación usada. La advertencia de
actualización de pip no es la causa. Extrae el ZIP completo en una carpeta nueva;
no copies únicamente los archivos `.cmd`. Ejecuta desde la terminal:

```powershell
py -3 preparar_entorno.py --comprobar
```

El comprobador indica los archivos que falten. El nuevo instalador utiliza rutas
absolutas para encontrar `requirements.txt`, aunque se abra desde otra carpeta.

## Contenido

- `app.py`: servidor y administración de piezas.
- `integracion.py`: importación/exportación de Excel y procesamiento de fotos.
- `mejoras.py`: temas de la colección, galerías y seguimiento.
- `verificacion.py`: activación y recuperación por correo, SMS y WhatsApp.
- `templates/`: páginas y formularios completos.
- `static/`: JavaScript, estilos y portada original verde y crema.
- `config/`: hash de acceso inicial conservado del paquete café.
- `datos_iniciales/`: catálogo vacío; `datos/` se crea al ejecutar.
- `tests/`: once pruebas de integración con datos temporales.
- `.vscode/`: configuración de ejecución, tareas y extensiones recomendadas.
- `requirements.txt` y `requirements_compilar.txt`: dependencias.
- `.env.example`: ejemplo de configuración sin credenciales reales.
- `Plantilla_Inventario.xlsx`: plantilla sin registros.

Usa `Coleccion-Web-Programador` con la contraseña original del paquete café.
El administrador `AdminETG_176463` se activa con el código emitido por el desarrollador.
Consulta `LEER_PRIMERO.txt` para cuentas, importación, respaldo y proveedores.
No se incluyen credenciales SMTP/Twilio ni una copia de datos de tu computadora.

Esta entrega no publica nada ni modifica el sitio actual. La dirección 127.0.0.1
solo funciona en la computadora que ejecuta el programa.
Windows y la ejecución F5 deben confirmarse en tu equipo; las pruebas automáticas
se ejecutaron en Linux con Python 3.12.
