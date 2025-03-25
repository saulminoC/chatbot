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

# --- Funciones principales ---
def get_calendar_service():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    return build('calendar', 'v3', credentials=creds)

def extraer_fecha_hora(mensaje):
    """Extrae fecha/hora con mejor manejo de formatos como '4 pm'"""
    try:
        settings = {
            'PREFER_DATES_FROM': 'future',
            'RELATIVE_BASE': datetime.now(),
            'TIMEZONE': 'America/Mexico_City',
            'RETURN_AS_TIMEZONE_AWARE': True
        }
        fecha = dateparser.parse(mensaje, settings=settings)
        
        if not fecha:
            # Respaldo con OpenAI para formatos complejos
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {"role": "system", "content": "Extrae fecha y hora en formato ISO. Ej: 2025-03-27T16:00:00"},
                    {"role": "user", "content": mensaje}
                ]
            )
            fecha_str = response.choices[0].message.content.strip()
            fecha = dateparser.parse(fecha_str, settings=settings)
        
        return fecha
    except Exception as e:
        print(f"Error al parsear fecha: {e}")
        return None

def validar_fecha_hora(fecha):
    """Valida fecha considerando horario de atención (8am-5pm)"""
    ahora = datetime.now().astimezone()
    
    if not fecha:
        return False, "No entendí la fecha/hora. Ej: 'Jueves a las 4pm'"
    
    # Asegurar zona horaria
    if not hasattr(fecha, 'tzinfo') or fecha.tzinfo is None:
        fecha = fecha.replace(tzinfo=ahora.tzinfo)
    
    # Validaciones
    if fecha < ahora - timedelta(minutes=30):
        return False, "Esa hora ya pasó. ¿Quisiste decir otro día?"
    
    if fecha.weekday() >= 6:  # Domingo=6
        return False, "Solo trabajamos de lunes a sábado."
    
    # Validación clave para horario (8am-5pm)
    if fecha.hour < HORA_APERTURA or fecha.hour >= HORA_CIERRE:
        return False, f"Horario: {HORA_APERTURA}am-{HORA_CIERRE-12}pm. ¿Otra hora?"
    
    return True, None

# --- Manejo de conversación ---
def manejar_inicio(mensaje, from_number):
    mensaje_lower = mensaje.lower()
    
    if any(palabra in mensaje_lower for palabra in ["hola", "buenos días", "buenas tardes"]):
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
    
    elif mensaje_lower in SERVICIOS:
        estado_conversacion[from_number] = {
            "estado": "preguntando_nombre",
            "servicio": mensaje_lower
        }
        return f"¿Cómo te llamas para agendar tu {mensaje_lower}?"
    
    else:
        return obtener_respuesta_openai(mensaje)

def manejar_agendamiento(mensaje, from_number):
    estado = estado_conversacion[from_number]
    
    if estado["estado"] == "preguntando_nombre":
        estado.update({
            "estado": "agendando_cita",
            "nombre": mensaje.title(),
            "intentos_fecha": 0
        })
        return (f"Gracias, {estado['nombre']}. ¿Para qué día y hora quieres tu {estado['servicio']}?\n"
                f"Ej: 'Mañana a las 10am' o 'Jueves 27 a las 4pm'")
    
    elif estado["estado"] == "agendando_cita":
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
    
    elif estado["estado"] == "confirmacion_cita":
        if mensaje.lower() in ["sí", "si", "confirmar"]:
            try:
                # Agendar en Google Calendar
                event = {
                    'summary': f"Cita: {estado['nombre']} - {estado['servicio']}",
                    'start': {'dateTime': estado['fecha'].isoformat(), 'timeZone': 'America/Mexico_City'},
                    'end': {'dateTime': (estado['fecha'] + timedelta(minutes=SERVICIOS[estado['servicio']]['duracion'])).isoformat(),
                            'timeZone': 'America/Mexico_City'},
                    'reminders': {'useDefault': True}
                }
                service = get_calendar_service()
                service.events().insert(calendarId='primary', body=event).execute()
                
                # Respuesta de éxito
                respuesta = (f"✅ ¡Listo! Tu cita para {estado['servicio']} está agendada:\n"
                            f"📅 {estado['fecha'].strftime('%A %d/%m a las %I:%M %p')}\n\n"
                            f"📍 Av. Principal 123\n"
                            f"📞 555-1234\n\n"
                            f"Te enviaremos un recordatorio.")
                
                del estado_conversacion[from_number]
                return respuesta
            
            except Exception as e:
                print(f"Error al agendar: {e}")
                return "❌ Error al agendar. Por favor llama al 555-1234."
        
        else:
            estado["estado"] = "agendando_cita"
            return "Entendido. ¿Qué nueva fecha/hora prefieres?"

# --- Webhook principal ---
@app.route('/webhook', methods=['POST'])
def webhook():
    mensaje = request.form.get('Body', '').strip()
    from_number = request.form.get('From')
    
    if not mensaje or not from_number:
        return "Mensaje inválido", 400
    
    try:
        # Inicializar estado si es nuevo
        if from_number not in estado_conversacion:
            estado_conversacion[from_number] = {"estado": "inicio"}
        
        estado = estado_conversacion[from_number]["estado"]
        
        # Manejar flujo de conversación
        if estado == "inicio":
            respuesta = manejar_inicio(mensaje, from_number)
        elif estado in ["preguntando_nombre", "agendando_cita", "confirmacion_cita"]:
            respuesta = manejar_agendamiento(mensaje, from_number)
        else:
            respuesta = "Ocurrió un error. Por favor envía 'hola' para reiniciar."
        
        # Enviar respuesta
        twiml = MessagingResponse()
        twiml.message(respuesta)
        return Response(str(twiml), content_type='text/xml')
    
    except Exception as e:
        print(f"Error en webhook: {e}")
        twiml = MessagingResponse()
        twiml.message("⚠️ Error temporal. Por favor intenta de nuevo.")
        return Response(str(twiml), content_type='text/xml')

@app.route('/')
def home():
    return "Chatbot Barbería d' Leo - Operativo"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)