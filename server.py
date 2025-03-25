import os
from datetime import timedelta, datetime
from flask import Flask, request, Response
from dotenv import load_dotenv
import dateparser
from twilio.twiml.messaging_response import MessagingResponse
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import pytz

# Configuración inicial
load_dotenv()
app = Flask(__name__)

# Constantes del negocio
HORARIO = "de lunes a sábado de 8:00 a 17:00"
HORA_APERTURA = 8  # 8 am
HORA_CIERRE = 17   # 5 pm
TIMEZONE = pytz.timezone('America/Mexico_City')

SERVICIOS = {
    "corte de cabello": {"precio": "100 MXN", "duracion": 30},
    "afeitado": {"precio": "500 MXN", "duracion": 45},
    "diseño de barba": {"precio": "150 MXN", "duracion": 30},
    "tratamiento capilar": {"precio": "200 MXN", "duracion": 60}
}

# Estados de conversación
conversaciones = {}

def get_calendar_service():
    """Obtiene el servicio de Google Calendar"""
    try:
        creds = service_account.Credentials.from_service_account_file(
            os.getenv('GOOGLE_CREDENTIALS_FILE', 'credentials.json'),
            scopes=['https://www.googleapis.com/auth/calendar']
        )
        return build('calendar', 'v3', credentials=creds)
    except Exception as e:
        print(f"Error al obtener servicio de Google Calendar: {e}")
        return None

def parsear_fecha(texto):
    """Intenta parsear una fecha a partir de texto natural"""
    try:
        parsed = dateparser.parse(
            texto,
            settings={
                'PREFER_DATES_FROM': 'future',
                'RELATIVE_BASE': datetime.now(TIMEZONE),
                'TIMEZONE': 'America/Mexico_City',
                'RETURN_AS_TIMEZONE_AWARE': True,
                'LANGUAGES': ['es']
            }
        )
        if parsed and parsed.tzinfo is None:
            parsed = TIMEZONE.localize(parsed)
        return parsed
    except Exception as e:
        print(f"Error al parsear fecha: {e}")
        return None

def validar_fecha(fecha):
    """Valida si una fecha es adecuada para agendar cita"""
    ahora = datetime.now(TIMEZONE)
    
    if not fecha:
        return False, "No entendí la fecha. Por favor escribe algo como:\n'Mañana a las 10am'\n'Jueves a las 4pm'"
    
    if fecha < ahora - timedelta(minutes=30):
        return False, "⚠️ Esa hora ya pasó. ¿Quieres agendar para otro momento?"
    
    if fecha.weekday() >= 6:
        return False, "🔒 Solo trabajamos de lunes a sábado."
    
    if fecha.hour < HORA_APERTURA or fecha.hour >= HORA_CIERRE:
        return False, f"⏰ Nuestro horario es de {HORA_APERTURA}am a {HORA_CIERRE-12}pm. ¿Qué otra hora te viene bien?"
    
    return True, None

def verificar_disponibilidad(fecha, duracion_minutos):
    """Verifica disponibilidad en el calendario"""
    service = get_calendar_service()
    if not service:
        return False, "Error al conectar con el calendario"
    
    try:
        tiempo_fin = fecha + timedelta(minutes=duracion_minutos)
        
        eventos = service.events().list(
            calendarId='primary',
            timeMin=fecha.isoformat(),
            timeMax=tiempo_fin.isoformat(),
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        return len(eventos.get('items', [])) == 0, None
    except HttpError as e:
        print(f"Error al verificar disponibilidad: {e}")
        return False, "Error al verificar disponibilidad en el calendario"

def mostrar_servicios():
    """Genera texto con los servicios disponibles"""
    servicios_texto = "💈 *Servicios disponibles* 💈\n\n"
    for servicio, detalles in SERVICIOS.items():
        servicios_texto += f"• ✂️ {servicio.capitalize()}: {detalles['precio']} ({detalles['duracion']} min)\n"
    servicios_texto += "\n_Responde con el nombre del servicio que deseas_"
    return servicios_texto

def crear_evento_calendario(datos_cita):
    """Crea un evento en Google Calendar"""
    service = get_calendar_service()
    if not service:
        return False
    
    try:
        evento = {
            'summary': f"Cita: {datos_cita['nombre']}",
            'description': f"Servicio: {datos_cita['servicio']}\nTeléfono: {datos_cita.get('telefono', 'No proporcionado')}",
            'start': {
                'dateTime': datos_cita['fecha'].isoformat(),
                'timeZone': 'America/Mexico_City',
            },
            'end': {
                'dateTime': (datos_cita['fecha'] + timedelta(minutes=SERVICIOS[datos_cita['servicio']]['duracion'])).isoformat(),
                'timeZone': 'America/Mexico_City',
            },
            'reminders': {
                'useDefault': True,
            }
        }
        
        evento_creado = service.events().insert(
            calendarId='primary',
            body=evento,
            sendUpdates='all'
        ).execute()
        
        return evento_creado.get('id') is not None
    except HttpError as e:
        print(f"Error al crear evento: {e}")
        return False

@app.route('/webhook', methods=['POST'])
def webhook():
    mensaje = request.form.get('Body', '').strip().lower()
    remitente = request.form.get('From')
    
    # Inicializar respuesta
    resp = MessagingResponse()
    
    # Manejo de saludos iniciales
    if any(saludo in mensaje for saludo in ['hola', 'holi', 'buenos días', 'buenas tardes', 'buenas noches']):
        conversaciones[remitente] = {'estado': 'inicio'}
        resp.message(
            "¡Bienvenido a Barbería d' Leo! ✂️\n\n"
            "Puedes preguntar por:\n"
            "* 'servicios' para ver opciones\n"
            "* 'agendar' para reservar cita"
        )
        return Response(str(resp), content_type='text/xml')
    
    # Inicializar conversación si es nuevo
    if remitente not in conversaciones:
        conversaciones[remitente] = {'estado': 'inicio'}
    
    estado_actual = conversaciones[remitente]['estado']
    
    try:
        # Flujo principal de conversación
        if estado_actual == 'inicio':
            if 'servicio' in mensaje or 'precio' in mensaje or 'qué hacen' in mensaje or 'servicios' in mensaje:
                conversaciones[remitente]['estado'] = 'listando_servicios'
                resp.message(mostrar_servicios())
            elif 'agendar' in mensaje or 'cita' in mensaje or 'reservar' in mensaje:
                conversaciones[remitente] = {
                    'estado': 'solicitando_nombre',
                    'servicio': None
                }
                resp.message("✍️ Por favor dime tu nombre para agendar tu cita:")
            else:
                resp.message(
                    "¡Bienvenido a Barbería d' Leo! ✂️\n\n"
                    "Puedes preguntar por:\n"
                    "* 'servicios' para ver opciones\n"
                    "* 'agendar' para reservar cita"
                )
        
        elif estado_actual == 'listando_servicios':
            if mensaje in SERVICIOS:
                conversaciones[remitente] = {
                    'estado': 'solicitando_nombre',
                    'servicio': mensaje
                }
                resp.message(f"✍️ Por favor dime tu nombre para agendar tu *{mensaje}*:")
            elif 'servicio' in mensaje or 'precio' in mensaje or 'servicios' in mensaje:
                resp.message(mostrar_servicios())
            elif 'agendar' in mensaje or 'cita' in mensaje:
                resp.message("Por favor elige primero un servicio:\n\n" + mostrar_servicios())
            else:
                resp.message(
                    "No reconozco ese servicio. Por favor elige uno de nuestra lista:\n\n" +
                    mostrar_servicios()
                )
        
        # Resto del flujo de conversación (solicitando_nombre, solicitando_fecha, confirmando_cita)
        # ... (mantener el mismo código que en la versión anterior)
        
        return Response(str(resp), content_type='text/xml')
    
    except Exception as e:
        print(f"Error en webhook: {e}")
        if remitente in conversaciones:
            del conversaciones[remitente]
        resp.message("🔧 Ocurrió un error inesperado. Por favor envía 'hola' para comenzar de nuevo.")
        return Response(str(resp), content_type='text/xml')

@app.route('/')
def home():
    return "Chatbot Barbería d' Leo - Servicio activo"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000, debug=True)