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
HORA_APERTURA = 8
HORA_CIERRE = 17

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
                'RETURN_AS_TIMEZONE_AWARE': True
            }
        )
    except:
        return None

def validar_fecha(fecha):
    ahora = datetime.now().astimezone()
    
    if not fecha:
        return False, "No entendí la fecha. Ejemplo: 'Mañana a las 10am'"
    
    if fecha < ahora - timedelta(minutes=30):
        return False, "Esa hora ya pasó. ¿Podrías indicar una hora futura?"
    
    if fecha.weekday() >= 6:
        return False, "Solo trabajamos de lunes a sábado."
    
    if fecha.hour < HORA_APERTURA or fecha.hour >= HORA_CIERRE:
        return False, f"Nuestro horario es de {HORA_APERTURA}am a {HORA_CIERRE-12}pm"
    
    return True, None

@app.route('/webhook', methods=['POST'])
def webhook():
    mensaje = request.form.get('Body', '').strip().lower()
    remitente = request.form.get('From')
    
    if not remitente in conversaciones:
        conversaciones[remitente] = {'estado': 'inicio'}
    
    estado = conversaciones[remitente]['estado']
    
    try:
        # Flujo de conversación principal
        if estado == 'inicio':
            if any(saludo in mensaje for saludo in ['hola', 'buenos días', 'buenas tardes']):
                respuesta = (
                    "¡Hola! 👋 Soy el asistente de Barbería d' Leo.\n\n"
                    "Puedo ayudarte con:\n"
                    "• 📋 Servicios\n"
                    "• 💰 Precios\n"
                    "• 🗓️ Agendar cita\n\n"
                    f"Horario: {HORARIO}"
                )
            elif 'servicios' in mensaje or 'precios' in mensaje:
                conversaciones[remitente]['estado'] = 'listando_servicios'
                respuesta = (
                    "💈 Servicios disponibles:\n\n" +
                    "\n".join([f"• {k.capitalize()}: {v['precio']} ({v['duracion']} min)" 
                             for k, v in SERVICIOS.items()]) +
                    "\n\nResponde con el servicio que deseas."
                )
            else:
                respuesta = "Envía 'servicios' para ver nuestras opciones o 'agendar' para una cita."
        
        elif estado == 'listando_servicios':
            if mensaje in SERVICIOS:
                conversaciones[remitente].update({
                    'estado': 'solicitando_nombre',
                    'servicio': mensaje
                })
                respuesta = f"¿Cómo te llamas para agendar tu {mensaje}?"
            else:
                respuesta = "Por favor elige un servicio de la lista."
        
        elif estado == 'solicitando_nombre':
            conversaciones[remitente].update({
                'estado': 'solicitando_fecha',
                'nombre': mensaje.title()
            })
            respuesta = (
                f"Gracias, {mensaje.title()}. ¿Para qué día y hora quieres tu "
                f"{conversaciones[remitente]['servicio']}?\n"
                "Ejemplos:\n• Mañana a las 10am\n• Viernes a las 3pm"
            )
        
        elif estado == 'solicitando_fecha':
            fecha = parsear_fecha(mensaje)
            valido, error = validar_fecha(fecha)
            
            if valido:
                conversaciones[remitente].update({
                    'estado': 'confirmando_cita',
                    'fecha': fecha
                })
                respuesta = (
                    f"📅 Confirmación:\n\n"
                    f"• Servicio: {conversaciones[remitente]['servicio'].capitalize()}\n"
                    f"• Fecha: {fecha.strftime('%A %d/%m')}\n"
                    f"• Hora: {fecha.strftime('%I:%M %p')}\n\n"
                    f"¿Es correcto? Responde 'sí' para confirmar."
                )
            else:
                respuesta = f"{error}\n\nPor favor ingresa otra fecha/hora:"
        
        elif estado == 'confirmando_cita':
            if mensaje in ['sí', 'si', 'confirmar']:
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
                        }
                    }
                    servicio.events().insert(calendarId='primary', body=evento).execute()
                    
                    respuesta = (
                        "✅ ¡Cita agendada con éxito!\n\n"
                        f"📅 {conversaciones[remitente]['fecha'].strftime('%A %d/%m a las %I:%M %p')}\n"
                        f"💈 Servicio: {conversaciones[remitente]['servicio'].capitalize()}\n\n"
                        "📍 Av. Principal 123\n"
                        "📞 Tel: 555-1234\n\n"
                        "Te esperamos en tu cita."
                    )
                    del conversaciones[remitente]
                except Exception as e:
                    respuesta = "❌ Error al agendar. Por favor llama al 555-1234."
            else:
                conversaciones[remitente]['estado'] = 'solicitando_fecha'
                respuesta = "Entendido. ¿Qué nueva fecha prefieres?"
        
        else:
            respuesta = "Envía 'hola' para reiniciar la conversación."
        
        # Enviar respuesta
        twiml = MessagingResponse()
        twiml.message(respuesta)
        return Response(str(twiml), content_type='text/xml')
    
    except Exception as e:
        print(f"Error: {e}")
        twiml = MessagingResponse()
        twiml.message("Ocurrió un error. Por favor envía 'hola' para reiniciar.")
        return Response(str(twiml), content_type='text/xml')

@app.route('/')
def home():
    return "Chatbot Barbería d' Leo - Operativo"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)