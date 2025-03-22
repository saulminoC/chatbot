from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from google.oauth2 import service_account
from googleapiclient.discovery import build
from datetime import datetime, timedelta
import os
import json
from dotenv import load_dotenv  # Importar dotenv para cargar variables de entorno

import openai  # Importar la librería de OpenAI

# Cargar las variables de entorno desde el archivo .env
load_dotenv()

# Configurar la clave de API de OpenAI
openai.api_key = os.getenv('OPENAI_API_KEY')

app = Flask(__name__)

# Configura las credenciales de Google Calendar
SCOPES = ["https://www.googleapis.com/auth/calendar"]
credentials_json = os.getenv("GOOGLE_CREDENTIALS")
credentials_info = json.loads(credentials_json)
credentials = service_account.Credentials.from_service_account_info(
    credentials_info, scopes=SCOPES
)
calendar_service = build("calendar", "v3", credentials=credentials)

# Datos del negocio
SERVICIOS = {
    "corte de cabello": 200,
    "afeitado": 150,
    "corte y barba": 300,
    "tinte": 250,
}
PROMOCIONES = {
    "lunes": "¡Los lunes tienes 20% de descuento en todos los servicios!",
    "martes": "¡Los martes, lleva un amigo y ambos obtienen 15% de descuento!",
}

# Estado de la conversación
conversacion = {}

# Función para interactuar con OpenAI
def obtener_respuesta_openai(mensaje_usuario):
    response = openai.Completion.create(
        engine="text-davinci-003",  # Usando el modelo GPT-3 más avanzado
        prompt=mensaje_usuario,     # El mensaje que el usuario envía
        max_tokens=150,             # Límite de tokens para la respuesta
        temperature=0.7,            # Controla la aleatoriedad
    )
    return response.choices[0].text.strip()

@app.route("/webhook", methods=["POST"])
def webhook():
    global conversacion
    incoming_message = request.form.get("Body").strip().lower()
    from_number = request.form.get("From")
    response_message = ""

    if from_number not in conversacion:
        conversacion[from_number] = {"paso": "inicio"}

    paso_actual = conversacion[from_number]["paso"]

    # Usar OpenAI para generar una respuesta dinámica basada en el mensaje del usuario
    if paso_actual == "inicio":
        response_message = obtener_respuesta_openai("Hola, soy tu asistente de barbería. ¿En qué puedo ayudarte?")
        conversacion[from_number]["paso"] = "preguntar_servicio"
    
    elif paso_actual == "preguntar_nombre":
        conversacion[from_number]["nombre"] = incoming_message
        response_message = (
            f"¡Hola {incoming_message.capitalize()}! Estos son nuestros servicios:\n"
        )
        for servicio, precio in SERVICIOS.items():
            response_message += f"- {servicio.capitalize()}: ${precio}\n"
        response_message += "¿Qué servicio te gustaría agendar?"
        conversacion[from_number]["paso"] = "preguntar_servicio"

    elif paso_actual == "preguntar_servicio":
        if incoming_message in SERVICIOS:
            conversacion[from_number]["servicio"] = incoming_message
            response_message = "¿Qué fecha te gustaría para tu cita? (Por ejemplo: 25/10/2023)"
            conversacion[from_number]["paso"] = "preguntar_fecha"
        else:
            response_message = "Servicio no válido. Por favor, elige uno de la lista."

    elif paso_actual == "preguntar_fecha":
        try:
            fecha = datetime.strptime(incoming_message, "%d/%m/%Y")
            conversacion[from_number]["fecha"] = fecha
            response_message = "¿A qué hora te gustaría tu cita? (Por ejemplo: 15:00)"
            conversacion[from_number]["paso"] = "preguntar_hora"
        except ValueError:
            response_message = "Formato de fecha incorrecto. Por favor, usa el formato DD/MM/AAAA."

    elif paso_actual == "preguntar_hora":
        try:
            hora = datetime.strptime(incoming_message, "%H:%M").time()
            fecha_completa = datetime.combine(conversacion[from_number]["fecha"], hora)
            conversacion[from_number]["hora"] = fecha_completa

            # Verificar promociones
            dia_semana = fecha_completa.strftime("%A").lower()
            if dia_semana in PROMOCIONES:
                response_message = f"{PROMOCIONES[dia_semana]}\n"
            else:
                response_message = ""

            response_message += (
                f"Resumen de tu cita:\n"
                f"Nombre: {conversacion[from_number]['nombre'].capitalize()}\n"
                f"Servicio: {conversacion[from_number]['servicio']}\n"
                f"Fecha y hora: {fecha_completa.strftime('%d/%m/%Y a las %H:%M')}\n"
            )
            if dia_semana in PROMOCIONES:
                response_message += f"Promoción: {PROMOCIONES[dia_semana]}\n"
            
            response_message += "¿Confirmas la cita? Responde 'sí' para confirmar o 'no' para modificar la cita."
            conversacion[from_number]["paso"] = "confirmar_cita"
        except ValueError:
            response_message = "Formato de hora incorrecto. Por favor, usa el formato HH:MM."

    elif paso_actual == "confirmar_cita":
        if incoming_message.strip() == "sí":
            evento_creado = crear_evento(
                conversacion[from_number]["nombre"],
                conversacion[from_number]["servicio"],
                conversacion[from_number]["hora"]
            )
            if evento_creado:
                response_message = (
                    f"¡Cita confirmada! Puedes ver los detalles aquí: {evento_creado.get('htmlLink')}"
                )
                conversacion[from_number] = {"paso": "inicio"}
            else:
                response_message = "Hubo un error al agendar tu cita. Por favor, inténtalo de nuevo más tarde."
        elif incoming_message.strip() == "no":
            response_message = "¿Qué te gustaría cambiar? Puedes responder con un nuevo servicio, fecha o hora."
            conversacion[from_number]["paso"] = "preguntar_servicio"
        else:
            response_message = "Cita cancelada. Si deseas agendar otra cita, escribe 'agendar cita'."
            conversacion[from_number] = {"paso": "inicio"}

    # Soporte para cancelación
    elif incoming_message.strip() == "cancelar cita":
        response_message = "Tu cita ha sido cancelada. Si deseas agendar otra cita, escribe 'agendar cita'."
        conversacion[from_number] = {"paso": "inicio"}

    # Crear la respuesta para Twilio
    twiml_response = MessagingResponse()
    twiml_response.message(response_message)
    return str(twiml_response)

# Mejorada la creación de eventos en Google Calendar
def crear_evento(nombre_cliente, servicio, fecha_hora):
    evento = {
        "summary": f"Cita de {nombre_cliente} - {servicio}",
        "start": {
            "dateTime": fecha_hora.isoformat(),
            "timeZone": "America/Mexico_City",
        },
        "end": {
            "dateTime": (fecha_hora + timedelta(hours=1)).isoformat(),
            "timeZone": "America/Mexico_City",
        },
        "description": f"Servicio: {servicio}\nCliente: {nombre_cliente}",
    }
    try:
        evento_creado = calendar_service.events().insert(
            calendarId="primary", body=evento
        ).execute()
        return evento_creado
    except Exception as e:
        return None

if __name__ == "__main__":
    app.run(debug=True)
