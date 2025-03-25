import os
from datetime import timedelta, datetime
from flask import Flask, request, Response
from dotenv import load_dotenv
import dateparser
from twilio.twiml.messaging_response import MessagingResponse
from google.oauth2 import service_account
from googleapiclient.discovery import build

# Configuración inicial
load_dotenv()
app = Flask(__name__)

# Constantes del negocio
HORARIO = "de lunes a sábado de 8:00 a 17:00"
HORA_APERTURA = 8  # 8 am
HORA_CIERRE = 17   # 5 pm

SERVICIOS = {
    "corte de cabello": {"precio": "100 MXN", "duracion": 30},
    "afeitado": {"precio": "500 MXN", "duracion": 45},
    "diseño de barba": {"precio": "150 MXN", "duracion": 30},
    "tratamiento capilar": {"precio": "200 MXN", "duracion": 60}
}

# Estados de conversación
conversaciones = {}

def get_calendar_service():
    creds = service_account.Credentials.from_service_account_file(
        os.getenv('GOOGLE_CREDENTIALS_FILE', 'credentials.json'),
        scopes=['https://www.googleapis.com/auth/calendar']
    )
    return build('calendar', 'v3', credentials=creds)

def parsear_fecha(texto):
    try:
        return dateparser.parse(
            texto,
            settings={
                'PREFER_DATES_FROM': 'future',
                'RELATIVE_BASE': datetime.now(),
                'TIMEZONE': 'America/Mexico_City',
                'RETURN_AS_TIMEZONE_AWARE': True,
                'LANGUAGES': ['es']
            }
        )
    except:
        return None

def validar_fecha(fecha):
    ahora = datetime.now().astimezone()
    
    if not fecha:
        return False, "No entendí la fecha. Por favor escribe algo como:\n'Mañana a las 10am'\n'Jueves a las 4pm'"
    
    if fecha < ahora - timedelta(minutes=30):
        return False, "⚠️ Esa hora ya pasó. ¿Quieres agendar para otro momento?"
    
    if fecha.weekday() >= 6:
        return False, "🔒 Solo trabajamos de lunes a sábado."
    
    if fecha.hour < HORA_APERTURA or fecha.hour >= HORA_CIERRE:
        return False, f"⏰ Nuestro horario es de {HORA_APERTURA}am a {HORA_CIERRE-12}pm. ¿Qué otra hora te viene bien?"
    
    return True, None

def mostrar_servicios():
    return (
        "💈 *Servicios disponibles* 💈\n\n" +
        "\n".join([f"• ✂️ {k.capitalize()}: {v['precio']} ({v['duracion']} min)" 
                 for k, v in SERVICIOS.items()]) +
        "\n\n_Responde con el nombre del servicio que deseas_"
    )

@app.route('/webhook', methods=['POST'])
def webhook():
    mensaje = request.form.get('Body', '').strip().lower()
    remitente = request.form.get('From')
    
    # Inicializar conversación si es nuevo
    if remitente not in conversaciones:
        conversaciones[remitente] = {'estado': 'inicio', 'intentos': 0}
    
    estado_actual = conversaciones[remitente]['estado']
    
    try:
        # Manejo de "hola" en cualquier estado
        if any(saludo in mensaje for saludo in ['hola', 'buenos días', 'buenas tardes']):
            if estado_actual != 'inicio':
                conversaciones[remitente] = {'estado': 'inicio'}
                return Response(
                    str(MessagingResponse().message(
                        "Hemos reiniciado la conversación. ¿En qué puedo ayudarte?\n\n"
                        "📋 Servicios\n"
                        "🗓️ Agendar cita\n"
                        "📍 Ubicación"
                    )),
                    content_type='text/xml'
                )
        
        # Flujo principal de conversación
        if estado_actual == 'inicio':
            if any(saludo in mensaje for saludo in ['hola', 'buenos días', 'buenas tardes']):
                respuesta = (
                    "¡Hola! 👋 Soy tu asistente de *Barbería d' Leo*.\n\n"
                    "¿En qué puedo ayudarte hoy?\n\n"
                    "📋 *Servicios disponibles*\n"
                    "🗓️ Agendar cita\n"
                    "📍 Ubicación y horarios\n\n"
                    f"*Horario:* {HORARIO}"
                )
            elif 'servicio' in mensaje or 'precio' in mensaje:
                conversaciones[remitente]['estado'] = 'listando_servicios'
                respuesta = mostrar_servicios()
            elif 'agendar' in mensaje or 'cita' in mensaje:
                conversaciones[remitente]['estado'] = 'listando_servicios'
                respuesta = (
                    "Perfecto, vamos a agendar tu cita. Primero elige un servicio:\n\n" +
                    mostrar_servicios()
                )
            else:
                respuesta = (
                    "¡Bienvenido a Barbería d' Leo! ✂️\n\n"
                    "Puedes preguntar por:\n"
                    "• 'servicios' para ver opciones\n"
                    "• 'agendar' para reservar cita\n"
                    "• 'horario' para conocer disponibilidad"
                )
        
        elif estado_actual == 'listando_servicios':
            if mensaje in SERVICIOS:
                conversaciones[remitente].update({
                    'estado': 'solicitando_nombre',
                    'servicio': mensaje
                })
                respuesta = f"✍️ ¿Cómo te llamas para agendar tu *{mensaje}*?"
            elif 'servicio' in mensaje or 'precio' in mensaje:
                respuesta = mostrar_servicios()
            elif 'agendar' in mensaje or 'cita' in mensaje:
                respuesta = "Por favor elige primero un servicio:\n\n" + mostrar_servicios()
            else:
                respuesta = (
                    "No reconozco ese servicio. Por favor elige uno:\n\n" +
                    "\n".join([f"• {k.capitalize()}" for k in SERVICIOS.keys()]) +
                    "\n\nO escribe 'servicios' para ver detalles."
                )
        
        elif estado_actual == 'solicitando_nombre':
            if len(mensaje.split()) >= 1:  # Aceptar al menos un nombre
                conversaciones[remitente].update({
                    'estado': 'solicitando_fecha',
                    'nombre': mensaje.title()
                })
                respuesta = (
                    f"👋 Perfecto, {mensaje.title()}. ¿Para qué día y hora quieres tu "
                    f"*{conversaciones[remitente]['servicio']}*?\n\n"
                    "Ejemplos:\n"
                    "• Mañana a las 10am\n"
                    "• Viernes a las 3pm\n"
                    "• 15 de abril a las 11"
                )
            else:
                respuesta = "Por favor escribe tu nombre para continuar"
        
        elif estado_actual == 'solicitando_fecha':
            fecha = parsear_fecha(mensaje)
            valido, error = validar_fecha(fecha)
            
            if valido:
                conversaciones[remitente].update({
                    'estado': 'confirmando_cita',
                    'fecha': fecha
                })
                respuesta = (
                    "📝 *Confirmación de cita*\n\n"
                    f"👤 Cliente: {conversaciones[remitente]['nombre']}\n"
                    f"💈 Servicio: {conversaciones[remitente]['servicio'].capitalize()}\n"
                    f"📅 Fecha: {fecha.strftime('%A %d/%m/%Y')}\n"
                    f"⏰ Hora: {fecha.strftime('%I:%M %p')}\n\n"
                    "¿Todo es correcto? Responde:\n"
                    "'✅ sí' para confirmar\n"
                    "'✏️ no' para modificar"
                )
            else:
                respuesta = error
        
        elif estado_actual == 'confirmando_cita':
            if mensaje.startswith(('sí', 'si', 'confirm')):
                try:
                    servicio = get_calendar_service()
                    evento = {
                        'summary': f"Cita: {conversaciones[remitente]['nombre']}",
                        'description': f"Servicio: {conversaciones[remitente]['servicio']}",
                        'start': {
                            'dateTime': conversaciones[remitente]['fecha'].isoformat(),
                            'timeZone': 'America/Mexico_City',
                        },
                        'end': {
                            'dateTime': (conversaciones[remitente]['fecha'] + 
                                       timedelta(minutes=SERVICIOS[conversaciones[remitente]['servicio']]['duracion'])).isoformat(),
                            'timeZone': 'America/Mexico_City',
                        },
                        'reminders': {
                            'useDefault': True,
                        }
                    }
                    servicio.events().insert(calendarId='primary', body=evento).execute()
                    
                    respuesta = (
                        "🎉 *¡Cita confirmada con éxito!* 🎉\n\n"
                        f"📅 {conversaciones[remitente]['fecha'].strftime('%A %d/%m/%Y a las %I:%M %p')}\n"
                        f"💈 Servicio: {conversaciones[remitente]['servicio'].capitalize()}\n\n"
                        "📍 *Ubicación:* Av. Principal 123, Col. Centro\n"
                        "📞 *Teléfono:* 555-1234\n\n"
                        "⏰ Llega 5 minutos antes.\n"
                        "🔔 Te enviaremos un recordatorio.\n\n"
                        "¡Gracias por elegir *Barbería d' Leo*!"
                    )
                    del conversaciones[remitente]
                except Exception as e:
                    print(f"Error al agendar: {e}")
                    respuesta = (
                        "❌ Hubo un error al agendar tu cita.\n\n"
                        "Por favor llámanos al 📞 555-1234 para asistencia inmediata."
                    )
            else:
                conversaciones[remitente]['estado'] = 'solicitando_fecha'
                respuesta = "Entendido. ¿Para qué nueva fecha y hora quieres agendar?"
        
        # Enviar respuesta
        twiml = MessagingResponse()
        twiml.message(respuesta)
        return Response(str(twiml), content_type='text/xml')
    
    except Exception as e:
        print(f"Error en webhook: {e}")
        # Reiniciar conversación en caso de error
        if remitente in conversaciones:
            del conversaciones[remitente]
        twiml = MessagingResponse()
        twiml.message("🔧 Ocurrió un error inesperado. Por favor envía 'hola' para comenzar de nuevo.")
        return Response(str(twiml), content_type='text/xml')

@app.route('/')
def home():
    return "Chatbot Barbería d' Leo - Servicio activo"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)