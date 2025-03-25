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
    return (
        "💈 *Servicios disponibles* 💈\n\n" +
        "\n".join([f"• ✂️ {k.capitalize()}: {v['precio']} ({v['duracion']} min)" 
                 for k, v in SERVICIOS.items()]) +
        "\n\n_Responde con el nombre del servicio que deseas_"
    )

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
            if 'servicio' in mensaje or 'precio' in mensaje:
                conversaciones[remitente]['estado'] = 'listando_servicios'
                resp.message(mostrar_servicios())
            elif 'agendar' in mensaje or 'cita' in mensaje:
                conversaciones[remitente] = {
                    'estado': 'solicitando_nombre',
                    'servicio': None  # No se ha seleccionado servicio aún
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
            else:
                resp.message(
                    "No reconozco ese servicio. Por favor elige uno:\n\n" +
                    mostrar_servicios()
                )
        
        elif estado_actual == 'solicitando_nombre':
            if len(mensaje.split()) >= 1:  # Aceptar al menos un nombre
                conversaciones[remitente]['nombre'] = mensaje.title()
                
                if conversaciones[remitente]['servicio'] is None:
                    # Si llegó aquí directamente desde 'agendar', pedir servicio
                    conversaciones[remitente]['estado'] = 'listando_servicios'
                    resp.message(
                        f"👋 Perfecto, {mensaje.title()}. Primero elige un servicio:\n\n" +
                        mostrar_servicios()
                    )
                else:
                    # Si ya tenía servicio, pedir fecha
                    conversaciones[remitente]['estado'] = 'solicitando_fecha'
                    resp.message(
                        f"📅 ¿Para qué día y hora quieres tu *{conversaciones[remitente]['servicio']}*?\n\n"
                        "Ejemplos:\n"
                        "* Mañana a las 10am\n"
                        "* Viernes a las 3pm\n"
                        "* 15 de abril a las 11"
                    )
            else:
                resp.message("Por favor escribe tu nombre completo para continuar")
        
        elif estado_actual == 'solicitando_fecha':
            fecha = parsear_fecha(mensaje)
            valido, error = validar_fecha(fecha)
            
            if valido:
                # Verificar disponibilidad
                disponible, error_disponibilidad = verificar_disponibilidad(
                    fecha,
                    SERVICIOS[conversaciones[remitente]['servicio']]['duracion']
                )
                
                if not disponible:
                    resp.message(
                        f"⚠️ Lo siento, ese horario no está disponible. {error_disponibilidad or ''}\n\n"
                        "Por favor elige otra fecha y hora:"
                    )
                    return Response(str(resp), content_type='text/xml')
                
                conversaciones[remitente]['fecha'] = fecha
                conversaciones[remitente]['estado'] = 'confirmando_cita'
                
                resp.message(
                    "📝 *Confirmación de cita*\n\n"
                    f"👤 Cliente: {conversaciones[remitente]['nombre']}\n"
                    f"💈 Servicio: {conversaciones[remitente]['servicio'].capitalize()}\n"
                    f"📅 Fecha: {fecha.strftime('%A %d/%m/%Y')}\n"
                    f"⏰ Hora: {fecha.strftime('%I:%M %p')}\n\n"
                    "¿Es correcto? Responde:\n"
                    "* 'sí' para confirmar\n"
                    "* 'no' para modificar"
                )
            else:
                resp.message(error)
        
        elif estado_actual == 'confirmando_cita':
            if mensaje.startswith(('sí', 'si', 'confirm', 'correcto')):
                if crear_evento_calendario(conversaciones[remitente]):
                    respuesta_exito = (
                        "🎉 *¡Cita confirmada con éxito!* 🎉\n\n"
                        f"📅 {conversaciones[remitente]['fecha'].strftime('%A %d/%m/%Y a las %I:%M %p')}\n"
                        f"💈 Servicio: {conversaciones[remitente]['servicio'].capitalize()}\n\n"
                        "📍 *Ubicación:* Av. Principal 123, Col. Centro\n"
                        "📞 *Teléfono:* 555-1234\n\n"
                        "⏰ Llega 5 minutos antes.\n"
                        "🔔 Te enviaremos un recordatorio.\n\n"
                        "¡Gracias por elegir *Barbería d' Leo*!"
                    )
                    resp.message(respuesta_exito)
                    del conversaciones[remitente]
                else:
                    resp.message(
                        "❌ Hubo un error al agendar tu cita.\n\n"
                        "Por favor llámanos al 📞 555-1234 para asistencia inmediata."
                    )
                    del conversaciones[remitente]
            else:
                conversaciones[remitente]['estado'] = 'solicitando_fecha'
                resp.message("Entendido. ¿Para qué nueva fecha y hora quieres agendar?")
        
        return Response(str(resp), content_type='text/xml')
    
    except Exception as e:
        print(f"Error en webhook: {e}")
        # Reiniciar conversación en caso de error
        if remitente in conversaciones:
            del conversaciones[remitente]
        resp.message("🔧 Ocurrió un error inesperado. Por favor envía 'hola' para comenzar de nuevo.")
        return Response(str(resp), content_type='text/xml')

@app.route('/')
def home():
    return "Chatbot Barbería d' Leo - Servicio activo"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000, debug=True)