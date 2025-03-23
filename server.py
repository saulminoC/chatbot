import os
from datetime import timedelta
from openai import OpenAI
from flask import Flask, request, Response
from dotenv import load_dotenv
import dateparser
from twilio.twiml.messaging_response import MessagingResponse
from google.oauth2 import service_account
from googleapiclient.discovery import build

# Cargar las variables de entorno
load_dotenv()

# Inicializar la aplicación Flask
app = Flask(__name__)

# Configurar el cliente de OpenAI
client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

# Configura las credenciales de Google Calendar
SCOPES = ['https://www.googleapis.com/auth/calendar']
SERVICE_ACCOUNT_FILE = 'credentials.json'  # Ruta al archivo JSON de credenciales

def get_calendar_service():
    """
    Obtiene el servicio de Google Calendar usando las credenciales de la cuenta de servicio.
    """
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    service = build('calendar', 'v3', credentials=creds)
    return service

def obtener_respuesta_openai(mensaje):
    """
    Obtiene una respuesta del modelo de OpenAI.
    """
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Eres un asistente de chatbot."},
                {"role": "user", "content": mensaje}
            ]
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"Error al obtener respuesta de OpenAI: {e}")
        return "Lo siento, hubo un error al procesar tu solicitud."

def procesar_cita(mensaje):
    """
    Procesa una solicitud de cita y la guarda en Google Calendar.
    """
    # Usar dateparser para detectar la fecha y hora del mensaje
    fecha = dateparser.parse(mensaje)
    if fecha:
        try:
            # Crear el evento en Google Calendar
            service = get_calendar_service()
            event = {
                'summary': 'Cita agendada',
                'description': 'Cita agendada a través del chatbot.',
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

            return f"Tu cita ha sido agendada para {fecha.strftime('%d/%m/%Y %H:%M')}."
        except Exception as e:
            print(f"Error al agendar la cita en Google Calendar: {e}")
            return "Lo siento, hubo un error al agendar tu cita. Por favor, intenta de nuevo."
    else:
        return "Lo siento, no pude entender la fecha y hora de tu cita. Por favor, intenta de nuevo."

# Ruta para el webhook
@app.route('/webhook', methods=['POST'])
def webhook():
    """
    Maneja las solicitudes entrantes de Twilio.
    """
    # Twilio envía los datos en request.form
    mensaje = request.form.get('Body')
    from_number = request.form.get('From')

    if mensaje:
        # Convertir el mensaje a minúsculas para facilitar la comparación
        mensaje_lower = mensaje.lower()

        # Procesar el mensaje para ver si es una solicitud de cita
        if "cita" in mensaje_lower or "agendar" in mensaje_lower or "reservar" in mensaje_lower:
            respuesta = procesar_cita(mensaje)
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