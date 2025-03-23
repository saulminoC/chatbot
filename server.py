import os
import openai
from flask import Flask, request, jsonify
from dotenv import load_dotenv
import dateparser

# Cargar las variables de entorno
load_dotenv()

# Inicializar la aplicación Flask
app = Flask(__name__)

# Configurar la clave de la API de OpenAI
openai.api_key = os.getenv('OPENAI_API_KEY')

# Función para obtener la respuesta de OpenAI
def obtener_respuesta_openai(mensaje):
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",  # Puedes cambiar a otro modelo como "gpt-4" si lo deseas
            messages=[
                {"role": "system", "content": "Eres un asistente de chatbot."},
                {"role": "user", "content": mensaje}
            ]
        )
        # Obtener la respuesta del modelo
        respuesta = response['choices'][0]['message']['content']
        return respuesta
    except Exception as e:
        print(f"Error al obtener respuesta de OpenAI: {e}")
        return "Lo siento, hubo un error al procesar tu solicitud."

# Función para manejar la creación de citas
def procesar_cita(mensaje):
    # Usar dateparser para intentar detectar la fecha y hora del mensaje
    fecha = dateparser.parse(mensaje)
    if fecha:
        # Aquí deberías verificar si el horario está disponible en tu base de datos
        # Si la cita está disponible, guardar en la base de datos
        # Para este ejemplo solo retornamos la cita
        return f"Tu cita ha sido agendada para {fecha.strftime('%d/%m/%Y %H:%M')}."
    else:
        return "Lo siento, no pude entender la fecha y hora de tu cita. Por favor, intenta de nuevo."

# Ruta para el webhook
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    if 'Body' in data:
        mensaje = data['Body']
        
        # Procesar el mensaje para ver si es una solicitud de cita
        if "cita" in mensaje.lower():
            respuesta = procesar_cita(mensaje)
        else:
            # Si no es una solicitud de cita, obtener respuesta de OpenAI
            respuesta = obtener_respuesta_openai(mensaje)
        
        return jsonify({'message': respuesta})
    else:
        return jsonify({'error': 'Mensaje no encontrado en el request'}), 400

# Ruta para el endpoint raíz
@app.route('/')
def index():
    return "Chatbot en funcionamiento. Enviar mensaje a /webhook."

# Ejecutar la aplicación
if __name__ == '__main__':
    app.run(debug=True, host="0.0.0.0", port=10000)
