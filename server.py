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

# Cargar las variables de entorno
load_dotenv()

# Inicializar la aplicación Flask
app = Flask(__name__)

# Configurar el cliente de OpenAI
client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

# Configura las credenciales de Google Calendar
SCOPES = ['https://www.googleapis.com/auth/calendar']
SERVICE_ACCOUNT_FILE = os.getenv('GOOGLE_CREDENTIALS_FILE', 'credentials.json')

# Horario de atención
HORARIO_ATENCION = "de lunes a sábado de 8:00 a 17:00"

# Servicios y precios
SERVICIOS = {
    "corte de cabello": {"precio": "100 MXN", "duracion": 30},
    "afeitado": {"precio": "500 MXN", "duracion": 45},
    "diseño de barba": {"precio": "150 MXN", "duracion": 30},
    "tratamiento capilar": {"precio": "200 MXN", "duracion": 60},
}

# Estado de la conversación
estado_conversacion = {}

def get_calendar_service():
    """Obtiene el servicio de Google Calendar"""
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    service = build('calendar', 'v3', credentials=creds)
    return service

def obtener_respuesta_openai(mensaje, from_number=None):
    """Obtiene una respuesta contextual de OpenAI"""
    try:
        # Construir contexto basado en el estado
        contexto = []
        
        if from_number and from_number in estado_conversacion:
            estado = estado_conversacion[from_number]
            if estado.get('servicio'):
                contexto.append(f"El cliente está interesado en: {estado['servicio']}")
            if estado.get('nombre'):
                contexto.append(f"Nombre del cliente: {estado['nombre']}")
        
        messages = [{
            "role": "system", 
            "content": """
            Eres el asistente virtual de la barbería d' Leo. 
            Sé amable, profesional y conciso. Usa emojis moderadamente.
            Si no sabes algo, ofrece contactar al personal.
            """
        }]
        
        if contexto:
            messages.append({"role": "user", "content": "\n".join(contexto)})
        
        messages.append({"role": "user", "content": mensaje})
        
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=messages,
            temperature=0.7,
            max_tokens=150
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"Error en OpenAI: {e}")
        return "Disculpa, estoy teniendo dificultades. ¿Podrías repetir o llamar al 555-1234?"

def extraer_fecha_hora(mensaje):
    """Extrae fecha y hora usando OpenAI y dateparser como respaldo"""
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Extrae fecha y hora en formato ISO. Si no hay, responde 'No'."},
                {"role": "user", "content": mensaje}
            ]
        )
        fecha_hora = response.choices[0].message.content.strip()
        return dateparser.parse(fecha_hora) if fecha_hora.lower() != "no" else None
    except:
        return dateparser.parse(mensaje)

def validar_fecha_hora(fecha):
    """Valida que la fecha sea adecuada"""
    ahora = datetime.now()
    
    if fecha < ahora - timedelta(hours=1):
        return False, "Esa fecha ya pasó. ¿Podrías indicar una futura?"
    
    if fecha.weekday() >= 6:
        return False, "Solo trabajamos de lunes a sábado."
    
    if fecha.hour < 8 or fecha.hour >= 17:
        return False, f"Nuestro horario es de 8:00 a 17:00. ¿Otra hora?"
    
    if fecha < ahora + timedelta(hours=1):
        return False, "Necesitamos al menos 1 hora de anticipación."
    
    return True, None

def listar_servicios():
    """Devuelve lista formateada de servicios"""
    servicios_texto = "💈 Servicios disponibles:\n\n"
    for servicio, info in SERVICIOS.items():
        servicios_texto += f"• {servicio.capitalize()}: {info['precio']} ({info['duracion']} min)\n"
    servicios_texto += "\nResponde con el servicio que deseas."
    return servicios_texto

def generar_respuesta_servicio(servicio):
    """Genera respuesta específica para cada servicio"""
    respuestas = {
        "corte de cabello": "✂️ ¡Buen choice! ¿Qué estilo prefieres? (moderno, clásico, fade, etc.)",
        "afeitado": "🧔 ¡Excelente! Usamos toallas calientes y productos premium. ¿Es para hoy?",
        "diseño de barba": "🧔‍♂️ Perfecto para definir tu estilo. ¿Tienes algún diseño en mente?",
        "tratamiento capilar": "💆‍♂️ Ideal para tu cabello. ¿Buscas hidratación, crecimiento o control?"
    }
    return respuestas.get(servicio.lower(), f"✅ {servicio.capitalize()} seleccionado. ¿Tu nombre por favor?")

def manejar_inicio(mensaje, from_number):
    """Maneja el estado inicial de la conversación"""
    mensaje_lower = mensaje.lower()
    
    if any(palabra in mensaje_lower for palabra in ["hola", "buenos días", "buenas tardes", "buenas noches"]):
        return (f"¡Hola! 👋 Soy el asistente de Barbería d' Leo.\n\n"
                f"Puedes preguntar sobre:\n"
                f"• 📋 Nuestros servicios\n"
                f"• 💰 Precios\n"
                f"• 🗓️ Agendar cita\n"
                f"• 📍 Ubicación\n\n"
                f"Horario: {HORARIO_ATENCION}")
    
    elif any(palabra in mensaje_lower for palabra in ["servicios", "precios", "qué ofrecen"]):
        estado_conversacion[from_number]["estado"] = "seleccion_servicio"
        return listar_servicios()
    
    elif mensaje_lower in SERVICIOS:
        estado_conversacion[from_number].update({
            "estado": "confirmacion_servicio",
            "servicio": mensaje_lower
        })
        return generar_respuesta_servicio(mensaje_lower)
    
    else:
        return obtener_respuesta_openai(mensaje, from_number)

def manejar_seleccion_servicio(mensaje, from_number):
    """Maneja la selección de servicio"""
    if mensaje.lower() in SERVICIOS:
        estado_conversacion[from_number].update({
            "estado": "preguntando_nombre",
            "servicio": mensaje.lower()
        })
        return f"¿Cómo te llamas para agendar tu {mensaje.lower()}?"
    else:
        return "No reconozco ese servicio. Por favor elige uno de la lista."

def manejar_nombre(mensaje, from_number):
    """Maneja la captura del nombre"""
    estado_conversacion[from_number].update({
        "estado": "agendando_cita",
        "nombre": mensaje
    })
    return (f"Gracias, {mensaje}. ¿Para qué día y hora quieres tu {estado_conversacion[from_number]['servicio']}?\n"
            f"Ejemplo: 'Mañana a las 10am' o 'Viernes 15 a las 3pm'")

def manejar_cita(mensaje, from_number):
    """Maneja el agendamiento de cita"""
    fecha = extraer_fecha_hora(mensaje)
    
    if not fecha:
        return "No entendí la fecha. ¿Podrías ser más específico? Ej: '25 de junio a las 2pm'"
    
    es_valida, mensaje_error = validar_fecha_hora(fecha)
    if not es_valida:
        return mensaje_error
    
    estado_conversacion[from_number].update({
        "estado": "confirmacion_cita",
        "fecha": fecha
    })
    
    return (f"📅 Confirmación de cita:\n\n"
            f"• Cliente: {estado_conversacion[from_number]['nombre']}\n"
            f"• Servicio: {estado_conversacion[from_number]['servicio'].capitalize()}\n"
            f"• Fecha: {fecha.strftime('%A %d/%m/%Y')}\n"
            f"• Hora: {fecha.strftime('%H:%M')}\n\n"
            f"¿Es correcto? Responde 'sí' para confirmar o 'no' para cambiar.")

def manejar_confirmacion(mensaje, from_number):
    """Maneja la confirmación final"""
    mensaje_lower = mensaje.lower()
    
    if mensaje_lower in ["sí", "si", "confirmo", "correcto"]:
        try:
            estado = estado_conversacion[from_number]
            servicio = SERVICIOS[estado['servicio']]
            
            event = {
                'summary': f"Cita: {estado['nombre']} - {estado['servicio']}",
                'description': f"Cliente: {estado['nombre']}\nServicio: {estado['servicio']}\nAgendado vía WhatsApp",
                'start': {
                    'dateTime': estado['fecha'].isoformat(),
                    'timeZone': 'America/Mexico_City',
                },
                'end': {
                    'dateTime': (estado['fecha'] + timedelta(minutes=servicio['duracion'])).isoformat(),
                    'timeZone': 'America/Mexico_City',
                },
                'reminders': {
                    'useDefault': False,
                    'overrides': [
                        {'method': 'popup', 'minutes': 1440},
                        {'method': 'popup', 'minutes': 60},
                    ],
                },
            }
            
            service = get_calendar_service()
            event = service.events().insert(calendarId='primary', body=event).execute()
            
            # Preparar respuesta de éxito
            respuesta = (f"✅ ¡Cita confirmada!\n\n"
                         f"📅 {estado['fecha'].strftime('%A %d/%m/%Y')}\n"
                         f"⏰ {estado['fecha'].strftime('%H:%M')}\n"
                         f"💈 {estado['servicio'].capitalize()}\n\n"
                         f"Te esperamos en Av. Principal 123. ¡Gracias {estado['nombre']}!")
            
            # Limpiar estado
            del estado_conversacion[from_number]
            
            return respuesta
            
        except Exception as e:
            print(f"Error al agendar: {e}")
            return "❌ Error al agendar. Por favor, llama al 555-1234."
    
    elif mensaje_lower in ["no", "cancelar"]:
        del estado_conversacion[from_number]
        return "Entendido. ¿Quieres comenzar de nuevo?"
    
    else:
        return "No entendí. Responde 'sí' para confirmar o 'no' para cancelar."

def manejar_conversacion(mensaje, from_number):
    """Función principal que maneja el flujo de conversación"""
    global estado_conversacion
    
    # Inicializar estado si no existe
    if from_number not in estado_conversacion:
        estado_conversacion[from_number] = {"estado": "inicio"}
    
    estado = estado_conversacion[from_number]["estado"]
    
    # Manejar según el estado actual
    if estado == "inicio":
        return manejar_inicio(mensaje, from_number)
    elif estado == "seleccion_servicio":
        return manejar_seleccion_servicio(mensaje, from_number)
    elif estado == "confirmacion_servicio":
        estado_conversacion[from_number]["estado"] = "preguntando_nombre"
        return f"¿Cómo te llamas para agendar tu {estado_conversacion[from_number]['servicio']}?"
    elif estado == "preguntando_nombre":
        return manejar_nombre(mensaje, from_number)
    elif estado == "agendando_cita":
        return manejar_cita(mensaje, from_number)
    elif estado == "confirmacion_cita":
        return manejar_confirmacion(mensaje, from_number)
    else:
        return obtener_respuesta_openai(mensaje, from_number)

@app.route('/webhook', methods=['POST'])
def webhook():
    """Endpoint principal para Twilio"""
    global estado_conversacion
    
    mensaje = request.form.get('Body', '').strip()
    from_number = request.form.get('From')
    
    if not mensaje or not from_number:
        return "Mensaje no válido", 400
    
    try:
        respuesta = manejar_conversacion(mensaje, from_number)
        twiml = MessagingResponse()
        twiml.message(respuesta)
        return Response(str(twiml), content_type='text/xml')
    
    except Exception as e:
        print(f"Error en webhook: {e}")
        twiml = MessagingResponse()
        twiml.message("⚠️ Error temporal. Por favor, intenta de nuevo.")
        return Response(str(twiml), content_type='text/xml')

@app.route('/')
def index():
    return "Chatbot Barbería d' Leo - En funcionamiento"

if __name__ == '__main__':
    app.run(debug=True, host="0.0.0.0", port=10000)