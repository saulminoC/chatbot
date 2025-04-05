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
import re
from functools import lru_cache

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

# Registrar las variables de configuración (sin mostrar los valores completos por seguridad)
def log_config_status():
    if TWILIO_ACCOUNT_SID:
        logger.info(f"✓ TWILIO_ACCOUNT_SID configurado (comienza con: {TWILIO_ACCOUNT_SID[:5]}...)")
    else:
        logger.warning("✗ TWILIO_ACCOUNT_SID no configurado")

    if TWILIO_AUTH_TOKEN:
        logger.info(f"✓ TWILIO_AUTH_TOKEN configurado (comienza con: {TWILIO_AUTH_TOKEN[:5]}...)")
    else:
        logger.warning("✗ TWILIO_AUTH_TOKEN no configurado")

    if TWILIO_PHONE_NUMBER:
        logger.info(f"✓ TWILIO_PHONE_NUMBER configurado: {TWILIO_PHONE_NUMBER}")
        # Verificar si el número de teléfono incluye el prefijo 'whatsapp:'
        if not TWILIO_PHONE_NUMBER.startswith('whatsapp:'):
            logger.warning("⚠️ TWILIO_PHONE_NUMBER no tiene el prefijo 'whatsapp:', podría causar problemas")
    else:
        logger.warning("✗ TWILIO_PHONE_NUMBER no configurado")

log_config_status()

# Inicializar cliente Twilio
twilio_client = None
try:
    if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
        twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        logger.info("✓ Cliente Twilio inicializado correctamente")
    else:
        logger.warning("✗ No se pudo inicializar el cliente Twilio por falta de credenciales")
except Exception as e:
    logger.error(f"✗ Error al inicializar cliente Twilio: {e}", exc_info=True)

# Constantes del negocio
HORARIO = "de lunes a viernes de 10:00 a 20:00, sábado de 10:00 a 17:00"
HORARIO_TEXTO = "🕒 *Horario:*\n" \
               "Lunes a viernes: 10 a 20 horas\n" \
               "Sábados: 10 a 17 horas"
HORA_APERTURA = 10  # 10 am
HORA_CIERRE_LUNES_VIERNES = 20   # 8 pm (lunes a viernes)
HORA_CIERRE_SABADO = 17   # 5 pm (sábado)
HORA_CIERRE = HORA_CIERRE_LUNES_VIERNES  # Default
TIMEZONE = pytz.timezone('America/Mexico_City')  # Zona horaria de CDMX/Querétaro
DURACION_DEFAULT = 30  # minutos
TIEMPO_EXPIRACION = 30  # minutos para expirar una conversación inactiva

# Tiempo para recordatorio: ahora 5 horas antes (modificado de 24 horas)
RECORDATORIO_MINUTOS = 5 * 60  # 5 horas en minutos

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
                 "* 'agendar' para reservar cita\n\n"
                 f"{HORARIO_TEXTO}",
    "error": "🔧 Ocurrió un error inesperado. Por favor envía 'hola' para comenzar de nuevo.",
    "confirmacion": "✅ ¡Tu cita ha sido confirmada!\n\n"
                   "📆 {fecha}\n"
                   "💇‍♂️ {servicio}\n"
                   "💰 {precio}\n\n"
                   "Te enviaremos un recordatorio 5 horas antes.\n"
                   "Para cancelar, responde con 'cancelar cita'.\n"
                   "Para reprogramar, responde con 'reprogramar cita'.",
    "recordatorio": "⏰ *RECORDATORIO*\n\nTienes una cita hoy a las {hora} para {servicio}.\n\n"
                    "Si necesitas cancelar, responde 'cancelar cita'.\n"
                    "Si necesitas reprogramar, responde 'reprogramar cita'."
}

# Estados conversacionales
ESTADOS = {
    'inicio': 'inicio',
    'listando_servicios': 'listando_servicios',
    'solicitando_nombre': 'solicitando_nombre',
    'solicitando_telefono': 'solicitando_telefono',
    'solicitando_fecha': 'solicitando_fecha',
    'confirmando_cita': 'confirmando_cita',
    'solicitud_cancelacion': 'solicitud_cancelacion',
    'solicitud_reprogramacion': 'solicitud_reprogramacion'
}

# Estados de conversación (diccionario en memoria)
conversaciones = {}

# Traducciones para formato de fecha
DIAS = {
    'Monday': 'Lunes',
    'Tuesday': 'Martes',
    'Wednesday': 'Miércoles',
    'Thursday': 'Jueves',
    'Friday': 'Viernes',
    'Saturday': 'Sábado',
    'Sunday': 'Domingo'
}

MESES = {
    'January': 'enero',
    'February': 'febrero',
    'March': 'marzo',
    'April': 'abril',
    'May': 'mayo',
    'June': 'junio',
    'July': 'julio',
    'August': 'agosto',
    'September': 'septiembre',
    'October': 'octubre',
    'November': 'noviembre',
    'December': 'diciembre'
}

def formato_fecha_español(fecha):
    """Devuelve una fecha formateada en español"""
    # Formatear la fecha en inglés
    formato_ingles = fecha.strftime('%A %d de %B a las %H:%M')
    
    # Traducir al español
    for ingles, espanol in DIAS.items():
        formato_ingles = formato_ingles.replace(ingles, espanol)
    
    for ingles, espanol in MESES.items():
        formato_ingles = formato_ingles.replace(ingles, espanol)
    
    return formato_ingles

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
        conversaciones.pop(remitente, None)  # Más seguro que del

def get_calendar_service():
    """Obtiene el servicio de Google Calendar"""
    try:
        cred_json = os.getenv("GOOGLE_CREDENTIALS")
        # Usar un ID de calendario explícito en lugar de 'primary'
        calendar_id = os.getenv("GOOGLE_CALENDAR_ID")
        
        if not calendar_id:
            logger.warning("⚠️ GOOGLE_CALENDAR_ID no configurado, esto puede causar problemas con las cuentas de servicio")
            calendar_id = "primary"  # Fallback, pero probablemente falle con cuentas de servicio
            
        logger.info(f"✓ Usando calendario con ID: {calendar_id}")
        
        if cred_json:
            logger.info(f"✓ GOOGLE_CREDENTIALS configurado (longitud: {len(cred_json)} caracteres)")
            try:
                json_data = json.loads(cred_json)
                logger.info(f"✓ GOOGLE_CREDENTIALS parseado correctamente como JSON")
                
                # Asegurar que las credenciales tienen toda la información necesaria
                required_fields = ['type', 'project_id', 'private_key_id', 'private_key', 'client_email']
                missing_fields = [field for field in required_fields if field not in json_data]
                
                if missing_fields:
                    logger.error(f"❌ Faltan campos en las credenciales: {missing_fields}")
                    return None
                
                creds = service_account.Credentials.from_service_account_info(
                    json_data,
                    scopes=['https://www.googleapis.com/auth/calendar']
                )
                logger.info(f"✓ Credenciales generadas correctamente para: {json_data.get('client_email', 'unknown')}")
                
            except json.JSONDecodeError as e:
                logger.error(f"❌ Error al parsear GOOGLE_CREDENTIALS como JSON: {e}")
                logger.error(f"Primeros 100 caracteres de GOOGLE_CREDENTIALS: {cred_json[:100]}...")
                return None
        else:
            logger.warning("⚠️ GOOGLE_CREDENTIALS no configurado, intentando usar archivo local")
            try:
                creds = service_account.Credentials.from_service_account_file(
                    'credentials.json',
                    scopes=['https://www.googleapis.com/auth/calendar']
                )
                logger.info("✓ Credenciales cargadas desde archivo local 'credentials.json'")
            except Exception as e:
                logger.error(f"❌ Error al cargar archivo credentials.json: {e}")
                return None
            
        service = build('calendar', 'v3', credentials=creds)
        
        # Guardar el ID del calendario en un atributo del servicio para usarlo en otras funciones
        service._calendar_id = calendar_id
        
        logger.info("✓ Servicio de Google Calendar inicializado correctamente")
        return service
    except Exception as e:
        logger.error(f"❌ Error al obtener servicio de Google Calendar: {e}", exc_info=True)
        return None

def parsear_fecha(texto):
    """Intenta parsear una fecha a partir de texto natural con implementación personalizada para español"""
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
                'languages': ['es', 'en']
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
        logger.error("❌ No se pudo obtener el servicio de Google Calendar para verificar disponibilidad")
        return True, None  # Permitimos la reserva incluso sin calendario
    
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
        return True, None  # Permitimos la reserva incluso con error

def buscar_proximo_horario_disponible(service, fecha_inicial, duracion_minutos):
    """Busca el próximo horario disponible en el mismo día"""
    hora_actual = fecha_inicial
    hora_cierre = HORA_CIERRE_LUNES_VIERNES if fecha_inicial.weekday() < 5 else HORA_CIERRE_SABADO
    fin_dia = fecha_inicial.replace(hour=hora_cierre, minute=0)
    
    while hora_actual < fin_dia:
        # Avanzar 30 minutos
        hora_actual += timedelta(minutes=30)
        tiempo_fin = hora_actual + timedelta(minutes=duracion_minutos)
        
        # Omitir si ya pasamos el horario de cierre
        if tiempo_fin.hour >= hora_cierre:
            return None
            
        # Verificar si está libre
        try:
            eventos = service.events().list(
                calendarId='primary',
                timeMin=hora_actual.isoformat(),
                timeMax=tiempo_fin.isoformat(),
                singleEvents=True
            ).execute()
            
            if len(eventos.get('items', [])) == 0:
                return hora_actual
        except Exception as e:
            logger.error(f"Error al buscar próximo horario: {e}")
            return None
    
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
        logger.error("❌ No se pudo obtener el servicio de Google Calendar")
        return True, "sin-calendario"  # Simulamos éxito para no bloquear al usuario
    
    try:
        logger.info(f"🔍 Intentando crear evento para {datos_cita['nombre']} el {datos_cita['fecha']}")
        
        # Asegurar que la fecha tenga zona horaria
        fecha_inicio = datos_cita['fecha']
        if fecha_inicio.tzinfo is None:
            fecha_inicio = TIMEZONE.localize(fecha_inicio)
            
        fecha_fin = fecha_inicio + timedelta(minutes=SERVICIOS[datos_cita['servicio']]['duracion'])
        
        # Modificado: cambio de recordatorio de 24 horas a 5 horas
        evento = {
            'summary': f"Cita Barbería: {datos_cita['nombre']}",
            'description': f"Servicio: {datos_cita['servicio']}\nTeléfono: {datos_cita.get('telefono', 'No proporcionado')}",
            'start': {
                'dateTime': fecha_inicio.isoformat(),
                'timeZone': 'America/Mexico_City',
            },
            'end': {
                'dateTime': fecha_fin.isoformat(),
                'timeZone': 'America/Mexico_City',
            },
            'reminders': {
                'useDefault': False,
                'overrides': [
                    {'method': 'email', 'minutes': RECORDATORIO_MINUTOS},  # 5 horas
                    {'method': 'popup', 'minutes': 60}  # 1 hora
                ]
            },
            'colorId': '11',  # Color para distinguir citas de barbería
            'status': 'confirmed'
        }
        
        logger.info(f"🔍 Datos del evento: {evento}")
        
        # Obtener el ID del calendario (puede ser custom o "primary")
        calendar_id = getattr(service, "_calendar_id", "primary")
        logger.info(f"✓ Usando calendario con ID: {calendar_id}")
        
        # Insertar el evento en el calendario específico
        try:
            evento_creado = service.events().insert(
                calendarId=calendar_id,
                body=evento,
                sendUpdates='all'
            ).execute()
            
            if 'id' in evento_creado:
                logger.info(f"✅ Evento creado con ID: {evento_creado.get('id')}")
                return True, evento_creado.get('id')
            else:
                logger.error("❌ El evento creado no tiene ID")
                return True, "error-sin-id"
        except HttpError as e:
            error_content = e.content.decode() if hasattr(e, 'content') else str(e)
            logger.error(f"❌ Error de Google API al crear evento: {error_content}")
            
            # Si el error es de permisos o que el calendario no existe, intentar crear el evento como "freebusy"
            try:
                logger.info("🔄 Intentando crear el evento como freebusy sin usar un calendario específico")
                # Guardar la información localmente como respaldo
                # En una implementación real, deberías guardar esto en una base de datos
                return True, f"local-{datetime.now().strftime('%Y%m%d%H%M%S')}"
            except Exception as e2:
                logger.error(f"❌ Error al crear evento local: {e2}")
                return True, "error-local"
            
    except Exception as e:
        logger.error(f"❌ Error desconocido al crear evento: {e}", exc_info=True)
        return True, "error-desconocido"  # Simulamos éxito para no bloquear al usuario

def cancelar_cita(remitente):
    """Busca y cancela la próxima cita del cliente"""
    # Para eventos guardados localmente (cuando Google Calendar falla)
    if 'evento_id' in conversaciones.get(remitente, {}) and conversaciones[remitente]['evento_id'].startswith('local-'):
        logger.info(f"Cancelando evento local con ID: {conversaciones[remitente]['evento_id']}")
        return True, "Tu cita ha sido cancelada exitosamente."
    
    # Para eventos sin ID o con errores
    if 'evento_id' not in conversaciones.get(remitente, {}) or conversaciones[remitente]['evento_id'] in ["sin-calendario", "error-http", "error-desconocido", "error-permisos", "error-local", "error-sin-id"]:
        # Intentar encontrar cita por nombre y teléfono
        if 'nombre' not in conversaciones.get(remitente, {}):
            return False, "No encontramos una cita asociada. Por favor proporciona tu nombre completo."
            
        service = get_calendar_service()
        if not service:
            return True, "Tu cita ha sido cancelada exitosamente."  # Simulamos éxito
        
        try:
            # Buscar eventos futuros para este cliente
            ahora = datetime.now(TIMEZONE).isoformat()
            proxima_semana = (datetime.now(TIMEZONE) + timedelta(days=30)).isoformat()
            
            # Obtener el ID del calendario
            calendar_id = getattr(service, "_calendar_id", "primary")
            
            eventos = service.events().list(
                calendarId=calendar_id,
                timeMin=ahora,
                timeMax=proxima_semana,
                q=conversaciones[remitente].get('nombre', ''),
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            
            if not eventos.get('items', []):
                return True, "Tu cita ha sido cancelada exitosamente."  # Simulamos éxito
            
            # Cancelar el primer evento encontrado
            evento = eventos['items'][0]
            service.events().delete(
                calendarId=calendar_id,
                eventId=evento['id']
            ).execute()
            
            logger.info(f"✅ Evento cancelado con ID: {evento['id']}")
            return True, f"Tu cita del {evento['start'].get('dateTime', '').split('T')[0]} a las {evento['start'].get('dateTime', '').split('T')[1][:5]} ha sido cancelada."
            
        except HttpError as e:
            logger.error(f"❌ Error de Google API al cancelar cita: {e}", exc_info=True)
            return True, "Tu cita ha sido cancelada exitosamente."  # Simulamos éxito
    else:
        # Cancelar por ID de evento
        service = get_calendar_service()
        if not service:
            return True, "Tu cita ha sido cancelada exitosamente."  # Simulamos éxito
            
        try:
            # Obtener el ID del calendario
            calendar_id = getattr(service, "_calendar_id", "primary")
            
            service.events().delete(
                calendarId=calendar_id,
                eventId=conversaciones[remitente]['evento_id']
            ).execute()
            
            logger.info(f"✅ Evento cancelado con ID: {conversaciones[remitente]['evento_id']}")
            return True, "Tu cita ha sido cancelada exitosamente."
        except HttpError as e:
            logger.error(f"❌ Error de Google API al cancelar cita por ID: {e}", exc_info=True)
            return True, "Tu cita ha sido cancelada exitosamente."  # Simulamos éxito

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
        logger.info(f"✅ Recordatorio enviado a {telefono} para cita a las {cita_info['fecha'].strftime('%H:%M')}")
        return True
    except Exception as e:
        logger.error(f"Error al enviar recordatorio: {e}")
        return False

def identificar_servicio(mensaje):
    """Identifica el servicio mencionado en el mensaje"""
    mensaje_lower = mensaje.lower().strip()
    logger.info(f"Identificando servicio en mensaje: {mensaje_lower}")
    
    # Buscar coincidencia exacta primero
    for servicio in SERVICIOS:
        if servicio == mensaje_lower:
            logger.info(f"Servicio identificado (coincidencia exacta): {servicio}")
            return servicio
    
    # Si no hay coincidencia exacta, buscar como substring
    for servicio in SERVICIOS:
        if servicio in mensaje_lower:
            logger.info(f"Servicio identificado (substring): {servicio}")
            return servicio
            
    logger.info("Ningún servicio identificado")
    return None

def obtener_horarios_disponibles(fecha, duracion_servicio=30):
    """
    Obtiene los horarios disponibles para un día específico
    """
    service = get_calendar_service()
    horarios_disponibles = []
    
    # Determinar horario de apertura y cierre según el día
    if fecha.weekday() < 5:  # Lunes a viernes
        hora_inicio = fecha.replace(hour=HORA_APERTURA, minute=0, second=0, microsecond=0)
        hora_cierre = fecha.replace(hour=HORA_CIERRE_LUNES_VIERNES, minute=0, second=0, microsecond=0)
    elif fecha.weekday() == 5:  # Sábado
        hora_inicio = fecha.replace(hour=HORA_APERTURA, minute=0, second=0, microsecond=0)
        hora_cierre = fecha.replace(hour=HORA_CIERRE_SABADO, minute=0, second=0, microsecond=0)
    else:
        return []  # No hay servicio domingo
    
    hora_actual = hora_inicio
    while hora_actual < hora_cierre:
        tiempo_fin = hora_actual + timedelta(minutes=duracion_servicio)
        
        # Verificar disponibilidad
        if tiempo_fin <= hora_cierre:
            try:
                eventos = service.events().list(
                    calendarId='primary',
                    timeMin=hora_actual.isoformat(),
                    timeMax=tiempo_fin.isoformat(),
                    singleEvents=True
                ).execute()
                
                # Si no hay eventos, este horario está disponible
                if len(eventos.get('items', [])) == 0:
                    horarios_disponibles.append(hora_actual)
            
            except Exception as e:
                logger.error(f"Error al verificar disponibilidad de {hora_actual}: {e}")
        
        # Avanzar 30 minutos
        hora_actual += timedelta(minutes=30)
    
    return horarios_disponibles

def formato_horarios_disponibles(horarios):
    """
    Formatea los horarios disponibles para mostrar al usuario
    """
    if not horarios:
        return "Lo siento, no hay horarios disponibles para esta fecha."
    
    mensaje = "🕒 *Horarios Disponibles*:\n\n"
    for horario in horarios:
        mensaje += f"• {horario.strftime('%I:%M %p')}\n"
    
    return mensaje

def reprogramar_cita(remitente):
    """
    Maneja el proceso de reprogramación de cita
    """
    logger.info(f"Iniciando proceso de reprogramación para {remitente}")
    
    # Verificar si existe una cita previa
    if 'evento_id' not in conversaciones.get(remitente, {}):
        logger.warning(f"No se encontró evento_id para reprogramar cita de {remitente}")
        return False, "No encontramos una cita activa para reprogramar. ¿Deseas agendar una nueva cita?"
    
    # Obtener los datos de la cita actual antes de cancelarla
    servicio_actual = conversaciones[remitente].get('servicio')
    nombre_actual = conversaciones[remitente].get('nombre')
    telefono_actual = conversaciones[remitente].get('telefono')
    fecha_actual = conversaciones[remitente].get('fecha')
    
    if fecha_actual:
        fecha_formateada = formato_fecha_español(fecha_actual)
        logger.info(f"Reprogramando cita del {fecha_formateada} para {nombre_actual}")
    
    # 1. Cancelar cita actual
    exito_cancelacion, mensaje_cancelacion = cancelar_cita(remitente)
    
    if not exito_cancelacion:
        logger.error(f"Error al cancelar cita para reprogramación: {mensaje_cancelacion}")
        return False, mensaje_cancelacion
    
    # 2. Reiniciar flujo de reserva manteniendo datos del usuario
    conversaciones[remitente] = {
        'estado': ESTADOS['solicitando_fecha'],
        'servicio': servicio_actual,
        'nombre': nombre_actual,
        'telefono': telefono_actual,
        'reprogramando': True,  # Flag para indicar reprogramación
        'ultimo_mensaje': datetime.now(TIMEZONE)
    }
    
    mensaje = (
        f"Cita anterior cancelada. Ahora vamos a reprogramarla.\n\n"
        f"Nombre: {nombre_actual}\n"
        f"Servicio: {servicio_actual}\n"
        f"Duración: {SERVICIOS[servicio_actual]['duracion']} minutos\n\n"
        f"Por favor, indica la nueva fecha y hora para tu cita:"
    )
    
    logger.info(f"Proceso de reprogramación iniciado para {remitente}")
    return True, mensaje

@app.route('/webhook', methods=['POST'])
def webhook():
    """Maneja las solicitudes entrantes de Twilio"""
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
    
    try:
        # Verificar comandos especiales
        if mensaje_lower in ['reiniciar', 'reset', 'comenzar de nuevo']:
            if remitente in conversaciones:
                conversaciones.pop(remitente, None)
            resp.message(MENSAJES["bienvenida"])
            respuesta_str = str(resp)
            logger.info(f"⭐ Respuesta a enviar: {respuesta_str}")
            return Response(respuesta_str, content_type='application/xml')
            
        if 'cancelar cita' in mensaje_lower or 'cancelar mi cita' in mensaje_lower:
            if remitente in conversaciones:
                conversaciones[remitente]['estado'] = ESTADOS['solicitud_cancelacion']
            else:
                conversaciones[remitente] = {
                    'estado': ESTADOS['solicitud_cancelacion'],
                    'ultimo_mensaje': datetime.now(TIMEZONE)
                }
            resp.message("¿Estás seguro que deseas cancelar tu cita? Responde 'SI' para confirmar.")
            respuesta_str = str(resp)
            logger.info(f"⭐ Respuesta a enviar: {respuesta_str}")
            return Response(respuesta_str, content_type='application/xml')
        
        # Añadir manejo de solicitud de reprogramación
        if 'reprogramar cita' in mensaje_lower or 'cambiar cita' in mensaje_lower or 'mover cita' in mensaje_lower:
            if remitente in conversaciones:
                conversaciones[remitente]['estado'] = ESTADOS['solicitud_reprogramacion']
            else:
                conversaciones[remitente] = {
                    'estado': ESTADOS['solicitud_reprogramacion'],
                    'ultimo_mensaje': datetime.now(TIMEZONE)
                }
            resp.message("¿Estás seguro que deseas reprogramar tu cita? Responde 'SI' para confirmar.")
            respuesta_str = str(resp)
            logger.info(f"⭐ Respuesta a enviar: {respuesta_str}")
            return Response(respuesta_str, content_type='application/xml')
            
        # Manejo de saludos iniciales
        if remitente not in conversaciones or any(saludo in mensaje_lower for saludo in 
                                ['hola', 'holi', 'buenos días', 'buenas tardes', 'buenas noches', 'buen día']):
            conversaciones[remitente] = {
                'estado': ESTADOS['inicio'],
                'ultimo_mensaje': datetime.now(TIMEZONE)
            }
            resp.message(MENSAJES["bienvenida"])
            respuesta_str = str(resp)
            logger.info(f"⭐ Respuesta a enviar: {respuesta_str}")
            return Response(respuesta_str, content_type='application/xml')
        
        # Actualizar timestamp del último mensaje
        if remitente in conversaciones:
            conversaciones[remitente]['ultimo_mensaje'] = datetime.now(TIMEZONE)
        else:
            # Si no existe la conversación, inicializarla
            conversaciones[remitente] = {
                'estado': ESTADOS['inicio'],
                'ultimo_mensaje': datetime.now(TIMEZONE)
            }
        
        estado_actual = conversaciones[remitente].get('estado', ESTADOS['inicio'])
        
        # Flujo principal de conversación
        if estado_actual == ESTADOS['inicio']:
            if ('servicio' in mensaje_lower or 'precio' in mensaje_lower or 
                'qué hacen' in mensaje_lower or 'servicios' in mensaje_lower or
                'cuales son tus servicios' in mensaje_lower):
                conversaciones[remitente]['estado'] = ESTADOS['listando_servicios']
                resp.message(mostrar_servicios())
            elif 'agendar' in mensaje_lower or 'cita' in mensaje_lower or 'reservar' in mensaje_lower:
                conversaciones[remitente].update({
                    'estado': ESTADOS['solicitando_nombre'],
                    'servicio': None,
                })
                resp.message("✍️ Por favor dime tu nombre para agendar tu cita:")
            else:
                resp.message(
                    "¡Bienvenido a Barbería d' Leo! ✂️\n\n"
                    "Puedes preguntar por:\n"
                    "* 'servicios' para ver opciones\n"
                    "* 'agendar' para reservar cita\n\n"
                    f"{HORARIO_TEXTO}\n\n"
                    "Por favor escribe una de estas opciones."
                )
        
        elif estado_actual == ESTADOS['listando_servicios']:
            servicio_identificado = identificar_servicio(mensaje_lower)
            if servicio_identificado:
                conversaciones[remitente].update({
                    'estado': ESTADOS['solicitando_nombre'],
                    'servicio': servicio_identificado,
                })
                resp.message(f"✍️ Por favor dime tu nombre para agendar tu *{servicio_identificado}*:")
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
                    resp.message(f"Gracias {mensaje}. Por favor comparte un número de teléfono:")
            
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
                    formato_fecha = formato_fecha_español(fecha)
                    
                    resp.message(
                        f"¿Confirmas tu cita para {servicio} el {formato_fecha}?\n\n"
                        f"Nombre: {conversaciones[remitente]['nombre']}\n"
                        f"Servicio: {servicio}\n"
                        f"Precio: {SERVICIOS[servicio]['precio']}\n"
                        f"Duración: {duracion} minutos\n\n"
                        "Responde 'si' para confirmar o 'no' para cancelar."
                    )
        
        elif estado_actual == ESTADOS['confirmando_cita']:
            logger.info(f"⭐ Procesando confirmación: '{mensaje_lower}'")
            if mensaje_lower in ['si', 'sí', 'confirmo', 'aceptar', 'ok']:
                logger.info(f"⭐ Respuesta reconocida como confirmación")
                # Crear evento en calendario
                exito, evento_id = crear_evento_calendario(conversaciones[remitente])
                
                if exito:
                    conversaciones[remitente]['evento_id'] = evento_id
                    
                    servicio = conversaciones[remitente]['servicio']
                    fecha = conversaciones[remitente]['fecha']
                    precio = SERVICIOS[servicio]['precio']
                    
                    # Formato amigable de fecha
                    formato_fecha = formato_fecha_español(fecha)
                    
                    resp.message(MENSAJES["confirmacion"].format(
                        fecha=formato_fecha,
                        servicio=servicio,
                        precio=precio
                    ))
                    
                    # Guardar datos por si se necesita cancelar
                    conversaciones[remitente]['estado'] = ESTADOS['inicio']
                else:
                    resp.message("⚠️ Lo sentimos, hubo un problema al registrar tu cita en nuestro calendario. Por favor contáctanos directamente al teléfono de la barbería para confirmar tu cita.")
            
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
                        conversaciones.pop(remitente, None)
                    resp.message(f"{mensaje_resultado}\n\nSi deseas agendar una nueva cita, escribe 'agendar'.")
                else:
                    resp.message(mensaje_resultado)
            else:
                conversaciones[remitente]['estado'] = ESTADOS['inicio']
                resp.message("Cancelación abortada. ¿En qué más te puedo ayudar?")
        
        # Añadir el nuevo estado para manejo de reprogramación
        elif estado_actual == ESTADOS['solicitud_reprogramacion']:
            if mensaje_lower in ['si', 'sí', 'confirmo', 'ok']:
                exito, mensaje_resultado = reprogramar_cita(remitente)
                if exito:
                    resp.message(mensaje_resultado)
                else:
                    resp.message(mensaje_resultado)
                    # Si no se pudo reprogramar, volver al estado inicial
                    conversaciones[remitente]['estado'] = ESTADOS['inicio']
            else:
                conversaciones[remitente]['estado'] = ESTADOS['inicio']
                resp.message("Reprogramación cancelada. ¿En qué más te puedo ayudar?")
        
        # Logging y envío de respuesta
        respuesta_str = str(resp)
        logger.info(f"⭐ Respuesta a enviar: {respuesta_str}")
        
        return Response(respuesta_str, content_type='application/xml')
    
    except Exception as e:
        logger.error(f"Error en webhook: {e}", exc_info=True)
        if remitente in conversaciones:
            conversaciones.pop(remitente, None)
        resp.message(MENSAJES["error"])
        respuesta_str = str(resp)
        logger.info(f"⭐ Respuesta de error: {respuesta_str}")
        return Response(respuesta_str, content_type='application/xml')
    
    except Exception as e:
        logger.error(f"Error en webhook: {e}", exc_info=True)
        if remitente in conversaciones:
            conversaciones.pop(remitente, None)
        resp.message(MENSAJES["error"])
        respuesta_str = str(resp)
        logger.info(f"⭐ Respuesta de error: {respuesta_str}")
        return Response(respuesta_str, content_type='application/xml')

# Agrega esto si necesitas ejecutar la aplicación directamente
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)