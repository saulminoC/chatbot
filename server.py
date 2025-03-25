import os
from datetime import timedelta, datetime
from openai import OpenAI
from flask import Flask, request, Response
from dotenv import load_dotenv
import dateparser
from twilio.twiml.messaging_response import MessagingResponse
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Cargar variables de entorno
load_dotenv()

# Inicializar Flask
app = Flask(__name__)

# Configurar OpenAI
client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

# Configurar Google Calendar
SCOPES = ['https://www.googleapis.com/auth/calendar']
SERVICE_ACCOUNT_FILE = os.getenv('GOOGLE_CREDENTIALS_FILE', 'credentials.json')

# Constantes de negocio
HORARIO_ATENCION = "de lunes a sábado de 8:00 a 17:00"
HORA_APERTURA = 8  # 8 am
HORA_CIERRE = 17   # 5 pm

SERVICIOS = {
    "corte de cabello": {"precio": "100 MXN", "duracion": 30},
    "afeitado": {"precio": "500 MXN", "duracion": 45},
    "diseño de barba": {"precio": "150 MXN", "duracion": 30},
    "tratamiento capilar": {"precio": "200 MXN", "duracion": 60},
}

# Estados de conversación
estado_conversacion = {}

def get_calendar_service():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    return build('calendar', 'v3', credentials=creds)

def extraer_fecha_hora(mensaje):
    """Extrae fecha/hora con mejor manejo de formatos"""
    try:
        settings = {
            'PREFER_DATES_FROM': 'future',
            'RELATIVE_BASE': datetime.now(),
            'TIMEZONE': 'America/Mexico_City',
            'RETURN_AS_TIMEZONE_AWARE': True
        }
        return dateparser.parse(mensaje, settings=settings)
    except Exception as e:
        print(f"Error al parsear fecha: {e}")
        return None

def validar_fecha_hora(fecha):
    """Valida fecha considerando horario de atención"""
    ahora = datetime.now().astimezone()
    
    if not fecha:
        return False, "No entendí la fecha/hora. Ej: 'Jueves a las 4pm'"
    
    if not hasattr(fecha, 'tzinfo') or fecha.tzinfo is None:
        fecha = fecha.replace(tzinfo=ahora.tzinfo)
    
    if fecha < ahora - timedelta(minutes=30):
        return False, "Esa hora ya pasó. ¿Quisiste decir otro día?"
    
    if fecha.weekday() >= 6:
        return False, "Solo trabajamos de lunes a sábado."
    
    if fecha.hour < HORA_APERTURA or fecha.hour >= HORA_CIERRE:
        return False, f"Horario: {HORA_APERTURA}am-{HORA_CIERRE-12}pm. ¿Otra hora?"
    
    return True, None

def manejar_inicio(mensaje, from_number):
    mensaje_lower = mensaje.lower()
    
    if any(palabra in mensaje_lower for palabra in ["hola", "buenos días", "buenas tardes"]):
        estado_conversacion[from_number] = {"estado": "inicio"}
        return (f"¡Hola! 👋 Soy el asistente de Barbería d' Leo.\n\n"
                f"Puedo ayudarte con:\n"
                f"• 📋 Servicios\n"
                f"• 💰 Precios\n"
                f"• 🗓️ Agendar cita\n\n"
                f"Horario: {HORARIO_ATENCION}")
    
    elif any(palabra in mensaje_lower for palabra in ["servicios", "precios"]):
        estado_conversacion[from_number] = {"estado": "seleccion_servicio"}
        return ("💈 Servicios:\n\n" +
                "\n".join([f"• {k.capitalize()}: {v['precio']} ({v['duracion']} min)" 
                          for k, v in SERVICIOS.items()]) +
                "\n\nResponde con el servicio que deseas.")
    
    return "Envía 'hola' para comenzar."

def manejar_servicio(mensaje, from_number):
    mensaje_lower = mensaje.lower()
    
    if mensaje_lower in SERVICIOS:
        estado_conversacion[from_number] = {
            "estado": "preguntando_nombre",
            "servicio": mensaje_lower
        }
        return f"¿Cómo te llamas para agendar tu {mensaje_lower}?"
    return "Servicio no válido. Elige uno de la lista."

def manejar_nombre(mensaje, from_number):
    estado_conversacion[from_number].update({
        "estado": "agendando_cita",
        "nombre": mensaje.title(),
        "intentos_fecha": 0
    })
    return (f"Gracias, {mensaje.title()}. ¿Para qué día y hora quieres tu " +
            f"{estado_conversacion[from_number]['servicio']}?\n" +
            f"Ej: 'Mañana a las 10am' o 'Jueves 27 a las 4pm'")

def manejar_cita(mensaje, from_number):
    estado = estado_conversacion[from_number]
    fecha = extraer_fecha_hora(mensaje)
    valido, error = validar_fecha_hora(fecha)
    
    if not valido:
        estado["intentos_fecha"] += 1
        if estado["intentos_fecha"] >= 2:
            return f"{error}\n\n¿Necesitas ayuda? Llama al 555-1234."
        return f"{error}\n\nPor favor, ingresa otra fecha/hora:"
    
    estado.update({
        "estado": "confirmacion_cita",
        "fecha": fecha
    })
    return (f"📅 Confirmación:\n\n"
            f"• Cliente: {estado['nombre']}\n"
            f"• Servicio: {estado['servicio'].capitalize()}\n"
            f"• Fecha: {fecha.strftime('%A %d/%m')}\n"
            f"• Hora: {fecha.strftime('%I:%M %p')}\n\n"
            f"¿Es correcto? Responde 'sí' o 'no'.")

def manejar_confirmacion(mensaje, from_number):
    estado = estado_conversacion[from_number]
    
    if mensaje.lower() in ["sí", "si", "confirmar"]:
        try:
            event = {
                'summary': f"Cita: {estado['nombre']} - {estado['servicio']}",
                'start': {'dateTime': estado['fecha'].isoformat(), 'timeZone': 'America/Mexico_City'},
                'end': {'dateTime': (estado['fecha'] + timedelta(minutes=SERVICIOS[estado['servicio']]['duracion'])).isoformat(),
                        'timeZone': 'America/Mexico_City'},
                'reminders': {'useDefault': True}
            }
            service = get_calendar_service()
            service.events().insert(calendarId='primary', body=event).execute()
            
            respuesta = (f"✅ ¡Listo! Tu cita está agendada:\n"
                        f"📅 {estado['fecha'].strftime('%A %d/%m a las %I:%M %p')}\n\n"
                        f"📍 Av. Principal 123\n"
                        f"📞 555-1234")
            
            del estado_conversacion[from_number]
            return respuesta
            
        except Exception as e:
            print(f"Error al agendar: {e}")
            return "❌ Error al agendar. Por favor llama al 555-1234."
    
    estado["estado"] = "agendando_cita"
    return "Entendido. ¿Qué nueva fecha/hora prefieres?"

@app.route('/webhook', methods=['POST'])
def webhook():
    mensaje = request.form.get('Body', '').strip()
    from_number = request.form.get('From')
    
    if not mensaje or not from_number:
        return "Mensaje inválido", 400
    
    try:
        # Reiniciar si no existe estado
        if from_number not in estado_conversacion:
            estado_conversacion[from_number] = {"estado": "inicio"}
        
        estado = estado_conversacion[from_number]["estado"]
        
        # Manejar flujo
        if estado == "inicio":
            respuesta = manejar_inicio(mensaje, from_number)
        elif estado == "seleccion_servicio":
            respuesta = manejar_servicio(mensaje, from_number)
        elif estado == "preguntando_nombre":
            respuesta = manejar_nombre(mensaje, from_number)
        elif estado == "agendando_cita":
            respuesta = manejar_cita(mensaje, from_number)
        elif estado == "confirmacion_cita":
            respuesta = manejar_confirmacion(mensaje, from_number)
        else:
            respuesta = "Envía 'hola' para comenzar."
        
        twiml = MessagingResponse()
        twiml.message(respuesta)
        return Response(str(twiml), content_type='text/xml')
    
    except Exception as e:
        print(f"Error en webhook: {e}")
        # Reiniciar conversación en caso de error
        if from_number in estado_conversacion:
            del estado_conversacion[from_number]
        twiml = MessagingResponse()
        twiml.message("Ocurrió un error. Por favor envía 'hola' para reiniciar.")
        return Response(str(twiml), content_type='text/xml')

@app.route('/')
def home():
    return "Chatbot Barbería d' Leo - Operativo"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)