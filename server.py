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
SERVICE_ACCOUNT_FILE = os.getenv('GOOGLE_CREDENTIALS_FILE', 'credentials.json')  # Ruta al archivo JSON de credenciales

# Horario de atención
HORARIO_ATENCION = "de lunes a sábado de 8:00 a 17:00"

# Servicios y precios
SERVICIOS = {
    "corte de cabello": "100 MXN",
    "afeitado": "500 MXN",
    "diseño de barba": "150 MXN",
    "tratamiento capilar": "200 MXN",
}

# Estado de la conversación
estado_conversacion = {}

def get_calendar_service():
    """
    Obtiene el servicio de Google Calendar usando las credenciales de la cuenta de servicio.
    """
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    service = build('calendar', 'v3', credentials=creds)
    return service

def obtener_respuesta_openai(mensaje, contexto=None):
    """
    Obtiene una respuesta del modelo de OpenAI.
    """
    try:
        messages = [{"role": "system", "content": "Eres un asistente de chatbot amigable y servicial."}]
        if contexto:
            messages.append({"role": "user", "content": contexto})
        messages.append({"role": "user", "content": mensaje})
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=messages
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"Error al obtener respuesta de OpenAI: {e}")
        return "Lo siento, hubo un error al procesar tu solicitud."

def extraer_fecha_hora(mensaje):
    """
    Usa OpenAI para extraer la fecha y hora del mensaje.
    """
    try:
        # Pedirle a OpenAI que extraiga la fecha y hora
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Extrae la fecha y hora del siguiente mensaje. Responde solo con la fecha y hora en formato ISO 8601 (YYYY-MM-DDTHH:MM:SS). Si no hay fecha y hora, responde 'No'."},
                {"role": "user", "content": mensaje}
            ]
        )
        fecha_hora = response.choices[0].message.content.strip()

        if fecha_hora.lower() == "no":
            return None
        else:
            # Convertir la fecha y hora a un objeto datetime
            return dateparser.parse(fecha_hora)
    except Exception as e:
        print(f"Error al extraer fecha y hora con OpenAI: {e}")
        return None

def validar_fecha_hora(fecha):
    """
    Valida que la fecha y hora estén dentro del horario de atención.
    """
    if fecha.weekday() >= 6:  # Domingo es 6
        return False, "Lo siento, no trabajamos los domingos."
    if fecha.hour < 8 or fecha.hour >= 17:
        return False, "Lo siento, nuestro horario de atención es de 8:00 a 17:00."
    return True, None

def procesar_cita(mensaje, from_number):
    """
    Procesa una solicitud de cita y la guarda en Google Calendar.
    """
    global estado_conversacion

    # Verificar si ya tenemos el nombre del cliente
    if "nombre" not in estado_conversacion[from_number]:
        estado_conversacion[from_number]["nombre"] = mensaje
        return "¡Gracias! Ahora, ¿podrías decirme para qué día y hora te gustaría agendar tu cita?\nRecuerda que estamos disponibles " + HORARIO_ATENCION + "."

    # Intentar extraer la fecha y hora usando OpenAI
    fecha = extraer_fecha_hora(mensaje)

    # Si OpenAI no pudo extraer la fecha, usar dateparser como respaldo
    if not fecha:
        fecha = dateparser.parse(mensaje)

    if fecha:
        # Validar la fecha y hora
        es_valida, mensaje_error = validar_fecha_hora(fecha)
        if not es_valida:
            return mensaje_error

        try:
            # Crear el evento en Google Calendar
            service = get_calendar_service()
            event = {
                'summary': f"Cita agendada para {estado_conversacion[from_number]['nombre']}",
                'description': f"Cita agendada a través del chatbot para {estado_conversacion[from_number]['nombre']}. Servicio: {estado_conversacion[from_number].get('servicio', 'No especificado')}.",
                'start': {
                    'dateTime': fecha.isoformat(),
                    'timeZone': 'America/Mexico_City',  # Ajusta la zona horaria
                },
                'end': {
                    'dateTime': (fecha + timedelta(hours=1)).isoformat(),  # Duración de 1 hora
                    'timeZone': 'America/Mexico_City',
                },
            }

            # Insertar el evento en el calendario
            calendar_id = 'primary'  # Usa el calendario principal
            event = service.events().insert(calendarId=calendar_id, body=event).execute()

            # Limpiar el estado de la conversación
            if from_number in estado_conversacion:
                del estado_conversacion[from_number]

            return f"¡Listo! Tu cita está agendada para el día {fecha.strftime('%d/%m/%Y a las %H:%M')}.\nTe enviaré un recordatorio 24 horas antes de la cita. Si necesitas reprogramar o cancelar, no dudes en contactarnos.\n\n¿Hay algo más en lo que pueda ayudarte?"
        except HttpError as e:
            print(f"Error al agendar la cita en Google Calendar: {e}")
            return "Lo siento, hubo un error al agendar tu cita. Por favor, intenta de nuevo."
    else:
        return "Lo siento, no pude entender la fecha y hora de tu cita. Por favor, intenta de nuevo."

def listar_servicios():
    """
    Devuelve una lista formateada de los servicios y precios.
    """
    servicios_texto = "Claro, te comparto los servicios que ofrecemos:\n\n"
    for servicio, precio in SERVICIOS.items():
        servicios_texto += f"{servicio.capitalize()}: {precio}\n"
    servicios_texto += "\n¿Qué servicio te gustaría agendar? Responde con el nombre del servicio."
    return servicios_texto

# Ruta para el webhook
@app.route('/webhook', methods=['POST'])
def webhook():
    """
    Maneja las solicitudes entrantes de Twilio.
    """
    global estado_conversacion

    # Twilio envía los datos en request.form
    mensaje = request.form.get('Body')
    from_number = request.form.get('From')

    if mensaje:
        # Convertir el mensaje a minúsculas para facilitar la comparación
        mensaje_lower = mensaje.lower()

        # Respuesta inicial del bot
        if any(palabra in mensaje_lower for palabra in ["hola", "buenos días", "buenas tardes", "buenas noches"]):
            respuesta = f"¡Hola! Soy de la barbería d' Leo. ¿En qué puedo ayudarte hoy? Puedes preguntar sobre nuestros servicios, precios, promociones, productos disponibles en la sucursal, o incluso agendar una cita.\n\nNuestro horario de atención es {HORARIO_ATENCION}."
        elif any(palabra in mensaje_lower for palabra in ["servicios", "precios", "qué servicios", "qué ofrecen", "cuáles son tus servicios"]):
            respuesta = listar_servicios()
        elif mensaje_lower in SERVICIOS:
            estado_conversacion[from_number] = {"estado": "preguntando_nombre", "servicio": mensaje_lower}
            respuesta = "¡Perfecto! Para agendar tu cita, ¿podrías decirme tu nombre?"
        elif from_number in estado_conversacion and estado_conversacion[from_number]["estado"] == "preguntando_nombre":
            estado_conversacion[from_number]["estado"] = "agendando_cita"
            respuesta = procesar_cita(mensaje, from_number)
        elif from_number in estado_conversacion and estado_conversacion[from_number]["estado"] == "agendando_cita":
            respuesta = procesar_cita(mensaje, from_number)
        else:
            # Si no es una solicitud de cita, obtener respuesta de OpenAI
            respuesta = obtener_respuesta_openai(mensaje)

        # Crear una respuesta en formato TwiML
        twiml_response = MessagingResponse()
        twiml_response.message(respuesta)

        # Devolver la respuesta en formato XML
        return Response(str(twiml_response), content_type='text/xml')
    else:
        return "Mensaje no encontrado en el request", 400

# Ruta para el endpoint raíz
@app.route('/')
def index():
    return "Chatbot en funcionamiento. Enviar mensaje a /webhook."

# Ejecutar la aplicación
if __name__ == '__main__':
    app.run(debug=True, host="0.0.0.0", port=10000)