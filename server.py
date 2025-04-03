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
import logging
from twilio.rest import Client
import json

# Configuración de logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Configuración inicial
load_dotenv()
app = Flask(__name__)

# Twilio client para enviar mensajes proactivos
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_PHONE_NUMBER = os.getenv('TWILIO_PHONE_NUMBER')
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN) if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN else None

# Constantes del negocio
HORARIO = "de lunes a viernes de 10:00 a 20:00, sábado de 10:00 a 17:00"
HORA_APERTURA = 10  # 10 am
HORA_CIERRE_LUNES_VIERNES = 20   # 8 pm (lunes a viernes)
HORA_CIERRE_SABADO = 17   # 5 pm (sábado)
TIMEZONE = pytz.timezone('America/Mexico_City')  # Zona horaria de CDMX/Querétaro
DURACION_DEFAULT = 30  # minutos
TIEMPO_EXPIRACION = 30  # minutos para expirar una conversación inactiva

SERVICIOS = {
    "corte de cabello": {"precio": "250 MXN", "duracion": 30},
    "corte de barba": {"precio": "250 MXN", "duracion": 30},
    "paquete corte y barba": {"precio": "420 MXN", "duracion": 60},
    "barba expres": {"precio": "250 MXN", "duracion": 30},
    "corte de dama": {"precio": "250 MXN", "duracion": 30},
    "corte de niño": {"precio": "200 MXN", "duracion": 30},
    "delineado de corte": {"precio": "110 MXN", "duracion": 15},
    "exfoliación": {"precio": "120 MXN", "duracion": 30},
    "mascarilla black": {"precio": "150 MXN", "duracion": 30},
    "paquete mascarilla y exfoliación": {"precio": "220 MXN", "duracion": 45},
    "mascarilla de colageno": {"precio": "170 MXN", "duracion": 30},
    "manicure": {"precio": "200 MXN", "duracion": 30}
}


# Mensajes predefinidos
MENSAJES = {
    "bienvenida": "¡Bienvenido a Barbería d' Leo! ✂️\n\n"
                 "Puedes preguntar por:\n"
                 "* 'servicios' para ver opciones\n"
                 "* 'agendar' para reservar cita",
    "error": "🔧 Ocurrió un error inesperado. Por favor envía 'hola' para comenzar de nuevo.",
    "confirmacion": "✅ ¡Tu cita ha sido confirmada!\n\n"
                   "📆 {fecha}\n"
                   "💇‍♂️ {servicio}\n"
                   "💰 {precio}\n\n"
                   "Te enviaremos un recordatorio 24 horas antes.\n"
                   "Para cancelar, responde con 'cancelar cita'.",
    "recordatorio": "⏰ *RECORDATORIO*\n\nTienes una cita mañana a las {hora} para {servicio}.\n\n"
                    "Si necesitas cancelar, responde 'cancelar cita'."
}

# Estados conversacionales
ESTADOS = {
    'inicio': 'inicio',
    'listando_servicios': 'listando_servicios',
    'solicitando_nombre': 'solicitando_nombre',
    'solicitando_telefono': 'solicitando_telefono',
    'solicitando_fecha': 'solicitando_fecha',
    'confirmando_cita': 'confirmando_cita',
    'solicitud_cancelacion': 'solicitud_cancelacion'
}

# Estados de conversación
conversaciones = {}

def limpiar_conversaciones_expiradas():
    """Elimina conversaciones inactivas"""
    ahora = datetime.now(TIMEZONE)
    expiradas = []
    
    for remitente, datos in conversaciones.items():
        ultimo_mensaje = datos.get('ultimo_mensaje')
        if ultimo_mensaje and (ahora - ultimo_mensaje) > timedelta(minutes=TIEMPO_EXPIRACION):
            expiradas.append(remitente)
    
    for remitente in expiradas:
        logger.info(f"Expirando conversación de {remitente}")
        del conversaciones[remitente]

def get_calendar_service():
    try:
        cred_json = os.getenv("GOOGLE_CREDENTIALS")  # Ahora obtenemos la variable
        if cred_json:
            creds = service_account.Credentials.from_service_account_info(
                json.loads(cred_json),
                scopes=['https://www.googleapis.com/auth/calendar']
            )
        else:
            creds = service_account.Credentials.from_service_account_file(
                'credentials.json',  # En caso de que uses el archivo local
                scopes=['https://www.googleapis.com/auth/calendar']
            )
        return build('calendar', 'v3', credentials=creds)
    except Exception as e:
        logger.error(f"Error al obtener servicio de Google Calendar: {e}")
        return None

def parsear_fecha(texto):
    """Intenta parsear una fecha a partir de texto natural con implementación personalizada para español"""
    from datetime import datetime, timedelta
    import re
    import calendar
    
    logger.info(f"Intentando parsear fecha: '{texto}'")
    
    texto = texto.lower().strip()
    ahora = datetime.now(TIMEZONE)
    resultado = None
    
    try:
        # 1. Patrones comunes en formato específico
        
        # Patrón: "mañana a las X(am/pm)"
        patron_manana = r'ma[ñn]ana (?:a las?\s+)?(\d{1,2})(?::(\d{1,2}))?\s*(am|pm)?'
        match = re.search(patron_manana, texto)
        if match:
            hora, minuto, ampm = match.groups()
            hora = int(hora)
            minuto = int(minuto) if minuto else 0
            
            if ampm and ampm.lower() == 'pm' and hora < 12:
                hora += 12
            
            resultado = ahora + timedelta(days=1)
            resultado = resultado.replace(hour=hora, minute=minuto, second=0, microsecond=0)
            logger.info(f"🔍 Fecha parseada usando patrón 'mañana': {resultado}")
            return resultado
        
        # Patrón: "día de la semana a las X(am/pm)" - ej: "jueves a las 4pm"
        dias_semana = {
            'lunes': 0, 'martes': 1, 'miercoles': 2, 'miércoles': 2, 
            'jueves': 3, 'viernes': 4, 'sabado': 5, 'sábado': 5, 'domingo': 6
        }
        
        for dia, num_dia in dias_semana.items():
            patron_dia = f"(?:el\s+)?{dia}\s+(?:a las?\s+)?(\d{{1,2}})(?::(\d{{1,2}}))?\s*(am|pm|de la tarde|de la mañana)?"
            match = re.search(patron_dia, texto)
            if match:
                hora, minuto, periodo = match.groups()
                hora = int(hora)
                minuto = int(minuto) if minuto else 0
                
                # Determinar AM/PM
                if periodo:
                    periodo = periodo.lower()
                    if any(p in periodo for p in ['pm', 'tarde']) and hora < 12:
                        hora += 12
                
                # Calcular el próximo día de la semana que coincida
                dias_hasta = (num_dia - ahora.weekday()) % 7
                # Si es el mismo día pero ya pasó la hora, ir a la próxima semana
                if dias_hasta == 0 and (hora < ahora.hour or (hora == ahora.hour and minuto <= ahora.minute)):
                    dias_hasta = 7
                # Si es 0, significa hoy pero queremos ir al próximo
                if dias_hasta == 0:
                    dias_hasta = 7
                
                resultado = ahora + timedelta(days=dias_hasta)
                resultado = resultado.replace(hour=hora, minute=minuto, second=0, microsecond=0)
                logger.info(f"🔍 Fecha parseada usando patrón 'día de semana': {resultado}")
                return resultado
        
        # Patrón: "hoy a las X(am/pm)"
        patron_hoy = r'hoy (?:a las?\s+)?(\d{1,2})(?::(\d{1,2}))?\s*(am|pm|de la tarde|de la mañana)?'
        match = re.search(patron_hoy, texto)
        if match:
            hora, minuto, periodo = match.groups()
            hora = int(hora)
            minuto = int(minuto) if minuto else 0
            
            # Determinar AM/PM
            if periodo:
                periodo = periodo.lower()
                if any(p in periodo for p in ['pm', 'tarde']) and hora < 12:
                    hora += 12
            
            resultado = ahora.replace(hour=hora, minute=minuto, second=0, microsecond=0)
            
            # Si la hora ya pasó, sugerir para mañana
            if resultado < ahora:
                logger.info(f"La hora de hoy {resultado} ya pasó, ajustando para mañana")
                resultado = resultado + timedelta(days=1)
            
            logger.info(f"🔍 Fecha parseada usando patrón 'hoy': {resultado}")
            return resultado
        
        # Patrón: "DD/MM(/YY) a las X(am/pm)" - ej: "04/04/25 a las 3pm"
        patron_fecha = r'(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?\s+(?:a las?\s+)?(\d{1,2})(?::(\d{1,2}))?\s*(am|pm|de la tarde|de la mañana)?'
        match = re.search(patron_fecha, texto)
        if match:
            dia, mes, anio, hora, minuto, periodo = match.groups()
            dia = int(dia)
            mes = int(mes)
            hora = int(hora)
            minuto = int(minuto) if minuto else 0
            
            # Validar mes y día
            if mes < 1 or mes > 12 or dia < 1 or dia > 31:
                return None
            
            # Determinar año
            if anio:
                anio = int(anio)
                if anio < 100:  # Asumimos 20XX para años de dos dígitos
                    anio += 2000
            else:
                anio = ahora.year
            
            # Determinar AM/PM
            if periodo:
                periodo = periodo.lower()
                if any(p in periodo for p in ['pm', 'tarde']) and hora < 12:
                    hora += 12
            
            try:
                # Crear fecha y validar
                resultado = ahora.replace(year=anio, month=mes, day=dia, 
                                          hour=hora, minute=minuto, second=0, microsecond=0)
                logger.info(f"🔍 Fecha parseada usando patrón 'DD/MM': {resultado}")
                return resultado
            except ValueError:
                # Manejar errores como 30/02/2025
                logger.warning(f"Fecha inválida: {dia}/{mes}/{anio}")
                return None
        
        # Si todos los patrones fallan, intentar con dateparser como fallback
        logger.info("Intentando parsear con dateparser como último recurso")
        
        # Traducir algunas palabras clave para ayudar a dateparser
        reemplazos = {
            'mañana': 'tomorrow',
            'próximo': 'next',
            'proximo': 'next',
            'siguiente': 'next',
            'de la tarde': 'pm',
            'de la mañana': 'am',
        }
        
        texto_traducido = texto
        for esp, eng in reemplazos.items():
            texto_traducido = texto_traducido.replace(esp, eng)
        
        resultado = dateparser.parse(
            texto_traducido,
            settings={
                'PREFER_DATES_FROM': 'future',
                'RELATIVE_BASE': ahora,
                'TIMEZONE': 'America/Mexico_City',
                'RETURN_AS_TIMEZONE_AWARE': True,
                'LANGUAGES': ['es', 'en']
            }
        )
        
        if resultado and resultado.tzinfo is None:
            resultado = TIMEZONE.localize(resultado)
        
        if resultado:
            logger.info(f"🔍 Fecha parseada con dateparser: {resultado}")
        else:
            logger.warning(f"❌ No se pudo parsear la fecha: '{texto}'")
        
        return resultado
        
    except Exception as e:
        logger.error(f"Error al parsear fecha '{texto}': {e}", exc_info=True)
        return None

def validar_fecha(fecha):
    """Valida si una fecha es adecuada para agendar cita"""
    ahora = datetime.now(TIMEZONE)
    
    if not fecha:
        return False, "No entendí la fecha. Por favor escribe algo como:\n'Mañana a las 10am'\n'Jueves a las 4pm'"
    
    if fecha < ahora - timedelta(minutes=30):
        return False, "⚠️ Esa hora ya pasó. ¿Quieres agendar para otro momento?"
    
    if fecha.weekday() < 5:  # Lunes a viernes
        if fecha.hour < HORA_APERTURA or fecha.hour >= HORA_CIERRE_LUNES_VIERNES:
            return False, f"⏰ Nuestro horario es de {HORA_APERTURA}am a {HORA_CIERRE_LUNES_VIERNES-12}pm de lunes a viernes. ¿Qué hora te viene bien?"
    elif fecha.weekday() == 5:  # Sábado
        if fecha.hour < HORA_APERTURA or fecha.hour >= HORA_CIERRE_SABADO:
            return False, f"⏰ Nuestro horario el sábado es de {HORA_APERTURA}am a {HORA_CIERRE_SABADO-12}pm. ¿Qué hora te viene bien?"
    else:
        return False, "🔒 Solo trabajamos de lunes a sábado. ¿Qué otro día te gustaría?"
    
    # Verificar que las citas sean a horas o medias horas
    if fecha.minute != 0 and fecha.minute != 30:
        hora_redondeada = fecha.replace(minute=0 if fecha.minute < 30 else 30)
        return False, f"Programamos citas a horas exactas o medias horas. ¿Te gustaría a las {hora_redondeada.strftime('%H:%M')}?"
    
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
        
        if len(eventos.get('items', [])) > 0:
            # Sugerir horario alternativo
            hora_siguiente = buscar_proximo_horario_disponible(service, fecha, duracion_minutos)
            if hora_siguiente:
                return False, f"Ese horario ya está ocupado. ¿Te gustaría a las {hora_siguiente.strftime('%H:%M')} del mismo día o prefieres otro día?"
            else:
                return False, "Ese horario ya está ocupado. ¿Prefieres otro día?"
        
        return True, None
    except HttpError as e:
        logger.error(f"Error al verificar disponibilidad: {e}")
        return False, "Error al verificar disponibilidad en el calendario"

def buscar_proximo_horario_disponible(service, fecha_inicial, duracion_minutos):
    """Busca el próximo horario disponible en el mismo día"""
    hora_actual = fecha_inicial
    fin_dia = fecha_inicial.replace(hour=HORA_CIERRE, minute=0)
    
    while hora_actual < fin_dia:
        # Avanzar 30 minutos
        hora_actual += timedelta(minutes=30)
        tiempo_fin = hora_actual + timedelta(minutes=duracion_minutos)
        
        # Omitir si ya pasamos el horario de cierre
        if tiempo_fin.hour >= HORA_CIERRE:
            return None
            
        # Verificar si está libre
        eventos = service.events().list(
            calendarId='primary',
            timeMin=hora_actual.isoformat(),
            timeMax=tiempo_fin.isoformat(),
            singleEvents=True
        ).execute()
        
        if len(eventos.get('items', [])) == 0:
            return hora_actual
    
    return None

def mostrar_servicios():
    """Genera texto con los servicios disponibles"""
    servicios_texto = "💈 *Servicios disponibles* 💈\n\n"
    for servicio, detalles in SERVICIOS.items():
        servicios_texto += f"• ✂️ {servicio.capitalize()}: {detalles['precio']} ({detalles['duracion']} min)\n"
    servicios_texto += "\n_Responde con el nombre exacto del servicio que deseas_"
    return servicios_texto

def crear_evento_calendario(datos_cita):
    """Crea un evento en Google Calendar"""
    service = get_calendar_service()
    if not service:
        return False, None
    
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
                'useDefault': False,
                'overrides': [
                    {'method': 'email', 'minutes': 24 * 60},
                    {'method': 'popup', 'minutes': 60}
                ]
            }
        }
        
        evento_creado = service.events().insert(
            calendarId='primary',
            body=evento,
            sendUpdates='all'
        ).execute()
        
        return True, evento_creado.get('id')
    except HttpError as e:
        logger.error(f"Error al crear evento: {e}")
        return False, None

def cancelar_cita(remitente):
    """Busca y cancela la próxima cita del cliente"""
    if 'evento_id' not in conversaciones.get(remitente, {}):
        # Intentar encontrar cita por nombre y teléfono
        if 'nombre' not in conversaciones.get(remitente, {}):
            return False, "No encontramos una cita asociada. Por favor proporciona tu nombre completo."
            
        service = get_calendar_service()
        if not service:
            return False, "No podemos conectar con el sistema de citas en este momento."
        
        try:
            # Buscar eventos futuros para este cliente
            ahora = datetime.now(TIMEZONE).isoformat()
            proxima_semana = (datetime.now(TIMEZONE) + timedelta(days=30)).isoformat()
            
            eventos = service.events().list(
                calendarId='primary',
                timeMin=ahora,
                timeMax=proxima_semana,
                q=conversaciones[remitente].get('nombre', ''),
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            
            if not eventos.get('items', []):
                return False, "No encontramos citas futuras a tu nombre."
            
            # Cancelar el primer evento encontrado
            evento = eventos['items'][0]
            service.events().delete(
                calendarId='primary',
                eventId=evento['id']
            ).execute()
            
            return True, f"Tu cita del {evento['start'].get('dateTime', '').split('T')[0]} a las {evento['start'].get('dateTime', '').split('T')[1][:5]} ha sido cancelada."
            
        except HttpError as e:
            logger.error(f"Error al cancelar cita: {e}")
            return False, "Ocurrió un error al cancelar la cita."
    else:
        # Cancelar por ID de evento
        service = get_calendar_service()
        if not service:
            return False, "No podemos conectar con el sistema de citas en este momento."
            
        try:
            service.events().delete(
                calendarId='primary',
                eventId=conversaciones[remitente]['evento_id']
            ).execute()
            
            return True, "Tu cita ha sido cancelada exitosamente."
        except HttpError as e:
            logger.error(f"Error al cancelar cita por ID: {e}")
            return False, "Ocurrió un error al cancelar la cita."

def enviar_recordatorio(telefono, cita_info):
    """Envía un recordatorio de cita por WhatsApp"""
    if not twilio_client:
        logger.warning("Cliente Twilio no configurado para enviar recordatorios")
        return False
        
    try:
        mensaje = MENSAJES["recordatorio"].format(
            hora=cita_info['fecha'].strftime('%H:%M'),
            servicio=cita_info['servicio']
        )
        
        twilio_client.messages.create(
            body=mensaje,
            from_=TWILIO_PHONE_NUMBER,
            to=telefono
        )
        return True
    except Exception as e:
        logger.error(f"Error al enviar recordatorio: {e}")
        return False

def identificar_servicio(mensaje):
    """Identifica un servicio a partir del mensaje del usuario"""
    mensaje = mensaje.lower()
    
    # Coincidencia exacta
    if mensaje in SERVICIOS:
        return mensaje
    
    # Coincidencia parcial
    for servicio in SERVICIOS:
        if servicio in mensaje:
            return servicio
    
    return None

@app.route('/webhook', methods=['POST'])
def webhook():
    # Verificar que la solicitud viene de Twilio
    if request.method != 'POST':
        return Response("Método no permitido", status=405)
    
    # Limpiar conversaciones expiradas
    limpiar_conversaciones_expiradas()
    
    # Obtener datos del mensaje
    mensaje = request.values.get('Body', '').strip()
    mensaje_lower = mensaje.lower()
    remitente = request.values.get('From', '')
    
    logger.info(f"Mensaje recibido de {remitente}: {mensaje}")
    
    # Inicializar respuesta Twilio
    resp = MessagingResponse()
    
    # Verificar comandos especiales
    if mensaje_lower in ['reiniciar', 'reset', 'comenzar de nuevo']:
        if remitente in conversaciones:
            del conversaciones[remitente]
        resp.message(MENSAJES["bienvenida"])
        return Response(str(resp), content_type='application/xml')
        
    if 'cancelar cita' in mensaje_lower or 'cancelar mi cita' in mensaje_lower:
        if remitente in conversaciones:
            conversaciones[remitente]['estado'] = ESTADOS['solicitud_cancelacion']
        else:
            conversaciones[remitente] = {
                'estado': ESTADOS['solicitud_cancelacion'],
                'ultimo_mensaje': datetime.now(TIMEZONE)
            }
        resp.message("¿Estás seguro que deseas cancelar tu cita? Responde 'SI' para confirmar.")
        return Response(str(resp), content_type='application/xml')
    
    # Manejo de saludos iniciales
    if remitente not in conversaciones or any(saludo in mensaje_lower for saludo in 
                             ['hola', 'holi', 'buenos días', 'buenas tardes', 'buenas noches', 'buen día']):
        conversaciones[remitente] = {
            'estado': ESTADOS['inicio'],
            'ultimo_mensaje': datetime.now(TIMEZONE)
        }
        resp.message(MENSAJES["bienvenida"])
        return Response(str(resp), content_type='application/xml')
    
    # Actualizar timestamp del último mensaje
    conversaciones[remitente]['ultimo_mensaje'] = datetime.now(TIMEZONE)
    
    estado_actual = conversaciones[remitente].get('estado', ESTADOS['inicio'])
    
    try:
        # Flujo principal de conversación
        if estado_actual == ESTADOS['inicio']:
            if ('servicio' in mensaje_lower or 'precio' in mensaje_lower or 
                'qué hacen' in mensaje_lower or 'servicios' in mensaje_lower or
                'cuales son tus servicios' in mensaje_lower):
                conversaciones[remitente]['estado'] = ESTADOS['listando_servicios']
                resp.message(mostrar_servicios())
            elif 'agendar' in mensaje_lower or 'cita' in mensaje_lower or 'reservar' in mensaje_lower:
                conversaciones[remitente] = {
                    'estado': ESTADOS['solicitando_nombre'],
                    'servicio': None,
                    'ultimo_mensaje': datetime.now(TIMEZONE)
                }
                resp.message("✍️ Por favor dime tu nombre para agendar tu cita:")
            else:
                resp.message(
                    "¡Bienvenido a Barbería d' Leo! ✂️\n\n"
                    "Puedes preguntar por:\n"
                    "* 'servicios' para ver opciones\n"
                    "* 'agendar' para reservar cita\n\n"
                    "Por favor escribe una de estas opciones."
                )
        
        elif estado_actual == ESTADOS['listando_servicios']:
            servicio_identificado = identificar_servicio(mensaje_lower)
            if servicio_identificado:
                conversaciones[remitente] = {
                    'estado': ESTADOS['solicitando_nombre'],
                    'servicio': servicio_identificado,
                    'ultimo_mensaje': datetime.now(TIMEZONE)
                }
                resp.message(f"✍️ Por favor dime tu nombre para agendar tu *{servicio_identificado}*:")
            elif 'servicio' in mensaje_lower or 'precio' in mensaje_lower or 'servicios' in mensaje_lower:
                resp.message(mostrar_servicios())
            elif 'agendar' in mensaje_lower or 'cita' in mensaje_lower:
                resp.message("Por favor elige primero un servicio:\n\n" + mostrar_servicios())
            else:
                resp.message(
                    "No reconozco ese servicio. Por favor elige uno de nuestra lista:\n\n" +
                    mostrar_servicios()
                )
        
        elif estado_actual == ESTADOS['solicitando_nombre']:
            if len(mensaje) < 3:
                resp.message("Por favor proporciona tu nombre completo.")
            else:
                conversaciones[remitente]['nombre'] = mensaje
                
                if conversaciones[remitente].get('servicio') is None:
                    conversaciones[remitente]['estado'] = ESTADOS['listando_servicios']
                    resp.message(f"Gracias {mensaje}. Ahora elige el servicio que deseas:\n\n" + mostrar_servicios())
                else:
                    conversaciones[remitente]['estado'] = ESTADOS['solicitando_telefono']
                    resp.message(f"Gracias {mensaje}. Por favor comparte un número de teléfono para contactarte:")
            
        elif estado_actual == ESTADOS['solicitando_telefono']:
            # Verificación simple de teléfono (solo números y espacios)
            telefono_limpio = ''.join(c for c in mensaje if c.isdigit() or c.isspace())
            if len(telefono_limpio) < 8:
                resp.message("Por favor proporciona un número de teléfono válido.")
            else:
                conversaciones[remitente]['telefono'] = telefono_limpio
                conversaciones[remitente]['estado'] = ESTADOS['solicitando_fecha']
                
                servicio = conversaciones[remitente]['servicio']
                duracion = SERVICIOS[servicio]['duracion']
                
                resp.message(
                    f"¿Cuándo te gustaría agendar tu cita para *{servicio}*?\n\n"
                    f"📅 Nuestro horario es {HORARIO}\n"
                    f"⏱️ Duración: {duracion} minutos\n\n"
                    "Por favor escribe la fecha y hora (por ejemplo: 'mañana a las 10am', 'jueves a las 4pm')"
                )
                
        elif estado_actual == ESTADOS['solicitando_fecha']:
            # Parsear fecha del mensaje
            fecha = parsear_fecha(mensaje)
            valido, mensaje_error = validar_fecha(fecha)
            
            if not valido:
                resp.message(mensaje_error)
            else:
                servicio = conversaciones[remitente]['servicio']
                duracion = SERVICIOS[servicio]['duracion']
                
                # Verificar disponibilidad
                disponible, mensaje_error = verificar_disponibilidad(fecha, duracion)
                
                if not disponible:
                    resp.message(mensaje_error)
                else:
                    # Guardar fecha en la conversación
                    conversaciones[remitente]['fecha'] = fecha
                    conversaciones[remitente]['estado'] = ESTADOS['confirmando_cita']
                    
                    # Formato amigable de fecha para mostrar
                    formato_fecha = fecha.strftime('%A %d de %B a las %H:%M').capitalize()
                    
                    resp.message(
                        f"¿Confirmas tu cita para {servicio} el {formato_fecha}?\n\n"
                        f"Nombre: {conversaciones[remitente]['nombre']}\n"
                        f"Servicio: {servicio}\n"
                        f"Precio: {SERVICIOS[servicio]['precio']}\n"
                        f"Duración: {duracion} minutos\n\n"
                        "Responde 'si' para confirmar o 'no' para cancelar."
                    )
        
        elif estado_actual == ESTADOS['confirmando_cita']:
            if mensaje_lower in ['si', 'sí', 'confirmo', 'aceptar', 'ok']:
                # Crear evento en calendario
                exito, evento_id = crear_evento_calendario(conversaciones[remitente])
                
                if exito:
                    conversaciones[remitente]['evento_id'] = evento_id
                    
                    servicio = conversaciones[remitente]['servicio']
                    fecha = conversaciones[remitente]['fecha']
                    precio = SERVICIOS[servicio]['precio']
                    
                    # Formato amigable de fecha
                    formato_fecha = fecha.strftime('%A %d de %B a las %H:%M').capitalize()
                    
                    resp.message(MENSAJES["confirmacion"].format(
                        fecha=formato_fecha,
                        servicio=servicio,
                        precio=precio
                    ))
                    
                    # Guardar datos por si se necesita cancelar
                    conversaciones[remitente]['estado'] = ESTADOS['inicio']
                else:
                    resp.message("⚠️ Lo sentimos, hubo un problema al crear tu cita. Por favor intenta de nuevo o contáctanos directamente.")
            
            elif mensaje_lower in ['no', 'cancelar', 'back', 'regresar']:
                conversaciones[remitente]['estado'] = ESTADOS['solicitando_fecha']
                resp.message("Entendido. Por favor indica otra fecha y hora que te convenga:")
            
            else:
                resp.message("Por favor responde 'si' para confirmar tu cita o 'no' para elegir otro horario.")
        
        elif estado_actual == ESTADOS['solicitud_cancelacion']:
            if mensaje_lower in ['si', 'sí', 'confirmo', 'ok']:
                exito, mensaje_resultado = cancelar_cita(remitente)
                if exito:
                    # Si se canceló exitosamente, reiniciar conversación
                    if remitente in conversaciones:
                        del conversaciones[remitente]
                    resp.message(f"{mensaje_resultado}\n\nSi deseas agendar una nueva cita, escribe 'agendar'.")
                else:
                    resp.message(mensaje_resultado)
            else:
                conversaciones[remitente]['estado'] = ESTADOS['inicio']
                resp.message("Cancelación abortada. ¿En qué más te puedo ayudar?")
        
        logger.info(f"Respuesta a enviar: {str(resp)}")
        return Response(str(resp), content_type='application/xml')
    
    except Exception as e:
        logger.error(f"Error en webhook: {e}", exc_info=True)
        if remitente in conversaciones:
            del conversaciones[remitente]
        resp.message(MENSAJES["error"])
        return Response(str(resp), content_type='application/xml')

@app.route('/enviar-recordatorios', methods=['GET'])
def enviar_recordatorios():
    """Endpoint para enviar recordatorios de citas del día siguiente"""
    if not twilio_client:
        return "Cliente Twilio no configurado", 500
    
    service = get_calendar_service()
    if not service:
        return "No se pudo conectar con Google Calendar", 500
    
    # Calcular fechas para mañana
    manana_inicio = datetime.now(TIMEZONE).replace(hour=0, minute=0, second=0) + timedelta(days=1)
    manana_fin = manana_inicio + timedelta(days=1)
    
    try:
        eventos = service.events().list(
            calendarId='primary',
            timeMin=manana_inicio.isoformat(),
            timeMax=manana_fin.isoformat(),
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        enviados = 0
        for evento in eventos.get('items', []):
            # Extraer teléfono de la descripción
            descripcion = evento.get('description', '')
            telefono = None
            
            for linea in descripcion.split('\n'):
                if 'teléfono:' in linea.lower():
                    telefono = linea.split(':', 1)[1].strip()
                    break
            
            if telefono:
                # Preparar datos para el recordatorio
                nombre = evento.get('summary', '').replace('Cita: ', '')
                servicio = ''
                for linea in descripcion.split('\n'):
                    if 'servicio:' in linea.lower():
                        servicio = linea.split(':', 1)[1].strip()
                        break
                
                hora_inicio = evento.get('start', {}).get('dateTime', '')
                if hora_inicio:
                    hora_inicio = dateparser.parse(hora_inicio)
                    
                    cita_info = {
                        'nombre': nombre,
                        'servicio': servicio,
                        'fecha': hora_inicio
                    }
                    
                    exito = enviar_recordatorio(telefono, cita_info)
                    if exito:
                        enviados += 1
        
        return f"Recordatorios enviados: {enviados}", 200
    except HttpError as e:
        logger.error(f"Error al enviar recordatorios: {e}")
        return "Error al consultar eventos", 500

@app.route('/')
def home():
    return "Chatbot Barbería d' Leo - Servicio activo"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))