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

# Configuración de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("barber_bot.log")
    ]
)
logger = logging.getLogger(__name__)

# Configuración inicial
load_dotenv()
app = Flask(__name__)

# Twilio client para enviar mensajes proactivos
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_PHONE_NUMBER = os.getenv('TWILIO_PHONE_NUMBER')
CALENDAR_ID = os.getenv('CALENDAR_ID', 'primary')

# Registrar las variables de configuración (sin mostrar los valores completos por seguridad)
def log_config_status():
    """Función para registrar el estado de configuración de variables críticas"""
    config_status = {
        "TWILIO_ACCOUNT_SID": TWILIO_ACCOUNT_SID[:5] + "..." if TWILIO_ACCOUNT_SID else None,
        "TWILIO_AUTH_TOKEN": TWILIO_AUTH_TOKEN[:5] + "..." if TWILIO_AUTH_TOKEN else None,
        "TWILIO_PHONE_NUMBER": TWILIO_PHONE_NUMBER,
        "CALENDAR_ID": CALENDAR_ID
    }
    
    for key, value in config_status.items():
        if value:
            logger.info(f"✓ {key} configurado correctamente")
            if key == "TWILIO_PHONE_NUMBER" and not value.startswith('whatsapp:'):
                logger.warning(f"⚠️ {key} no tiene el prefijo 'whatsapp:', podría causar problemas")
        else:
            logger.warning(f"✗ {key} no configurado")

# Inicializar cliente Twilio
def init_twilio_client():
    """Inicializa el cliente de Twilio si las credenciales están disponibles"""
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN):
        logger.warning("✗ No se pudo inicializar el cliente Twilio por falta de credenciales")
        return None
        
    try:
        # Verificar comandos especiales
        if PATRONES['reiniciar'].search(mensaje_lower):
            if remitente in conversaciones:
                del conversaciones[remitente]
            resp.message(MENSAJES["bienvenida"])
            respuesta_str = str(resp)
            logger.info(f"⭐ Respuesta a enviar: {respuesta_str}")
            return Response(respuesta_str, content_type='application/xml')
            
        if PATRONES['cancelar_cita'].search(mensaje_lower):
            actualizar_estado_conversacion(remitente, ESTADOS['solicitud_cancelacion'])
            resp.message("¿Estás seguro que deseas cancelar tu cita? Responde 'SI' para confirmar.")
            respuesta_str = str(resp)
            logger.info(f"⭐ Respuesta a enviar: {respuesta_str}")
            return Response(respuesta_str, content_type='application/xml')
            
        # Manejo de saludos iniciales
        if remitente not in conversaciones or es_saludo(mensaje):
            actualizar_estado_conversacion(remitente, ESTADOS['inicio'])
            resp.message(MENSAJES["bienvenida"])
            respuesta_str = str(resp)
            logger.info(f"⭐ Respuesta a enviar: {respuesta_str}")
            return Response(respuesta_str, content_type='application/xml')
        
        # Actualizar timestamp del último mensaje
        if remitente in conversaciones:
            conversaciones[remitente]['ultimo_mensaje'] = datetime.now(TIMEZONE)
        
        estado_actual = conversaciones.get(remitente, {}).get('estado', ESTADOS['inicio'])
        
        # Flujo principal de conversación
        if estado_actual == ESTADOS['inicio']:
            if PATRONES['servicios'].search(mensaje_lower):
                actualizar_estado_conversacion(remitente, ESTADOS['listando_servicios'])
                resp.message(mostrar_servicios(por_categoria=True))
            elif PATRONES['agendar'].search(mensaje_lower):
                actualizar_estado_conversacion(remitente, ESTADOS['solicitando_nombre'], servicio=None)
                resp.message("✍️ Por favor dime tu nombre para agendar tu cita:")
            else:
                resp.message(
                    "¡Bienvenido a Barbería d' Leo! ✂️\n\n"
                    "Puedes preguntar por:\n"
                    "• 'servicios' para ver opciones\n"
                    "• 'agendar' para reservar cita\n\n"
                    f"{HORARIO_TEXTO}\n\n"
                    "Por favor escribe una de estas opciones."
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
                    # Actualizar estado con la fecha
                    actualizar_estado_conversacion(remitente, ESTADOS['confirmando_cita'], fecha=fecha)
                    
                    # Formato amigable de fecha para mostrar
                    formato_fecha = formato_fecha_español(fecha)
                    
                    resp.message(
                        f"¿Confirmas tu cita para {servicio} el {formato_fecha}?\n\n"
                        f"👤 Nombre: {conversaciones[remitente]['nombre']}\n"
                        f"✂️ Servicio: {servicio}\n"
                        f"💰 Precio: {SERVICIOS[servicio]['precio']}\n"
                        f"⏱️ Duración: {duracion} minutos\n\n"
                        "Responde 'si' para confirmar o 'no' para modificar."
                    )
        
        elif estado_actual == ESTADOS['confirmando_cita']:
            logger.info(f"⭐ Procesando confirmación: '{mensaje_lower}'")
            if PATRONES['si'].search(mensaje_lower):
                logger.info(f"⭐ Respuesta reconocida como confirmación")
                # Crear evento en calendario
                exito, evento_id = crear_evento_calendario(conversaciones[remitente])
                
                if exito:
                    # Actualizar con ID de evento y regresar a inicio
                    actualizar_estado_conversacion(remitente, ESTADOS['inicio'], evento_id=evento_id)
                    
                    servicio = conversaciones[remitente]['servicio']
                    fecha = conversaciones[remitente]['fecha']
                    precio = SERVICIOS[servicio]['precio']
                    
                    # Formato amigable de fecha
                    formato_fecha = formato_fecha_español(fecha)
                    
                    # Programar recordatorio para 24 horas antes si hay teléfono
                    if twilio_client and 'telefono' in conversaciones[remitente]:
                        try:
                            # Programa recordatorio para un día antes (en una implementación real)
                            logger.info(f"✅ Recordatorio programado para {conversaciones[remitente].get('telefono')}")
                        except Exception as e:
                            logger.error(f"❌ Error al programar recordatorio: {e}")
                    
                    resp.message(MENSAJES["confirmacion"].format(
                        fecha=formato_fecha,
                        servicio=servicio,
                        precio=precio
                    ))
                else:
                    resp.message("⚠️ Lo sentimos, hubo un problema al registrar tu cita en nuestro calendario. Por favor contáctanos directamente al teléfono de la barbería para confirmar tu cita.")
            
            elif PATRONES['no'].search(mensaje_lower):
                actualizar_estado_conversacion(remitente, ESTADOS['solicitando_fecha'])
                resp.message("Entendido. Por favor indica otra fecha y hora que te convenga:")
            
            else:
                resp.message("Por favor responde 'si' para confirmar tu cita o 'no' para elegir otro horario.")
                
        elif estado_actual == ESTADOS['solicitud_cancelacion']:
            if PATRONES['si'].search(mensaje_lower):
                exito, mensaje_resultado = cancelar_cita(remitente)
                if exito:
                    # Si se canceló exitosamente, reiniciar conversación
                    if remitente in conversaciones:
                        del conversaciones[remitente]
                    resp.message(f"{mensaje_resultado}\n\nSi deseas agendar una nueva cita, escribe 'agendar'.")
                else:
                    resp.message(mensaje_resultado)
            else:
                actualizar_estado_conversacion(remitente, ESTADOS['inicio'])
                resp.message("Cancelación abortada. ¿En qué más te puedo ayudar?")
        
        # Logging y envío de respuesta
        respuesta_str = str(resp)
        logger.info(f"⭐ Respuesta a enviar: {respuesta_str}")
        
        return Response(respuesta_str, content_type='application/xml')
    
    except Exception as e:
        logger.error(f"❌ Error en webhook: {e}", exc_info=True)
        if remitente in conversaciones:
            del conversaciones[remitente]
        resp.message(MENSAJES["error"])
        respuesta_str = str(resp)
        logger.info(f"⭐ Respuesta de error: {respuesta_str}")
        return Response(respuesta_str, content_type='application/xml')


# Ruta para estadísticas y monitoreo
@app.route('/status', methods=['GET'])
def status():
    """Endpoint para verificar el estado del sistema"""
    try:
        resultado = {
            "status": "healthy",
            "conversations": len(conversaciones),
            "twilio_configured": twilio_client is not None,
            "calendar_configured": get_calendar_service() is not None,
            "version": "2.0.0"
        }
        return Response(json.dumps(resultado), content_type='application/json')
    except Exception as e:
        logger.error(f"Error en status: {e}")
        return Response(json.dumps({"status": "error", "message": str(e)}), 
                      content_type='application/json', status=500)
                
        elif estado_actual == ESTADOS['solicitando_nombre']:
            if len(mensaje) < 3:
                resp.message("Por favor proporciona tu nombre completo.")
            else:
                conversaciones[remitente]['nombre'] = mensaje
                
                if conversaciones[remitente].get('servicio') is None:
                    actualizar_estado_conversacion(remitente, ESTADOS['listando_servicios'], nombre=mensaje)
                    resp.message(f"Gracias {mensaje}. Ahora elige el servicio que deseas:\n\n" + mostrar_servicios())
                else:
                    actualizar_estado_conversacion(remitente, ESTADOS['solicitando_telefono'], nombre=mensaje)
                    resp.message(f"Gracias {mensaje}. Por favor comparte un número de teléfono para contactarte:")
        
        elif estado_actual == ESTADOS['solicitando_telefono']:
            # Verificación simple de teléfono (solo números y espacios)
            telefono_limpio = ''.join(c for c in mensaje if c.isdigit() or c.isspace())
            if len(telefono_limpio) < 8:
                resp.message("Por favor proporciona un número de teléfono válido.")
            else:
                actualizar_estado_conversacion(remitente, ESTADOS['solicitando_fecha'], telefono=telefono_limpio)
                
                servicio = conversaciones[remitente]['servicio']
                duracion = SERVICIOS[servicio]['duracion']
                precio = SERVICIOS[servicio]['precio']
                
                resp.message(
                    f"¿Cuándo te gustaría agendar tu cita para *{servicio}*?\n\n"
                    f"💰 Precio: {precio}\n"
                    f"⏱️ Duración: {duracion} minutos\n"
                    f"📅 Nuestro horario es {HORARIO}\n\n"
                    "Por favor escribe la fecha y hora (por ejemplo: 'mañana a las 10am', 'jueves a las 4pm')"
                )
        
        elif estado_actual == ESTADOS['listando_servicios']:
            servicio_identificado = identificar_servicio(mensaje)
            if servicio_identificado:
                actualizar_estado_conversacion(remitente, ESTADOS['solicitando_nombre'], servicio=servicio_identificado)
                resp.message(f"✍️ Por favor dime tu nombre para agendar tu *{servicio_identificado}*:")
            elif PATRONES['agendar'].search(mensaje_lower):
                resp.message("Por favor elige primero un servicio para tu cita:\n\n" + mostrar_servicios())
            else:
                # Sugerir servicios similares si está cerca pero no exacto
                resp.message(
                    "No reconozco ese servicio. Por favor elige uno de nuestra lista:\n\n" +
                    mostrar_servicios()
                )
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        logger.info("✓ Cliente Twilio inicializado correctamente")
        return client
    except Exception as e:
        logger.error(f"✗ Error al inicializar cliente Twilio: {e}", exc_info=True)
        return None

# Registrar configuración y inicializar Twilio
log_config_status()
twilio_client = init_twilio_client()

# Constantes del negocio
HORARIO = "de lunes a viernes de 10:00 a 20:00, sábado de 10:00 a 17:00"
HORARIO_TEXTO = "🕒 *Horario:*\n" \
               "Lunes a viernes: 10 a 20 horas\n" \
               "Sábados: 10 a 17 horas"
HORA_APERTURA = 10  # 10 am
HORA_CIERRE_LUNES_VIERNES = 20   # 8 pm (lunes a viernes)
HORA_CIERRE_SABADO = 17   # 5 pm (sábado)
TIMEZONE = pytz.timezone('America/Mexico_City')  # Zona horaria de CDMX/Querétaro
DURACION_DEFAULT = 30  # minutos
TIEMPO_EXPIRACION = 30  # minutos para expirar una conversación inactiva

# Separar servicios por categorías para mejor organización
SERVICIOS = {
    # Servicios de corte
    "corte de cabello": {"precio": "250 MXN", "duracion": 30, "categoria": "corte"},
    "corte de barba": {"precio": "250 MXN", "duracion": 30, "categoria": "corte"},
    "paquete corte y barba": {"precio": "420 MXN", "duracion": 60, "categoria": "corte"},
    "barba expres": {"precio": "250 MXN", "duracion": 30, "categoria": "corte"},
    "corte de dama": {"precio": "250 MXN", "duracion": 30, "categoria": "corte"},
    "corte de niño": {"precio": "200 MXN", "duracion": 30, "categoria": "corte"},
    "delineado de corte": {"precio": "110 MXN", "duracion": 15, "categoria": "corte"},
    
    # Servicios de tratamiento
    "exfoliación": {"precio": "120 MXN", "duracion": 30, "categoria": "tratamiento"},
    "mascarilla black": {"precio": "150 MXN", "duracion": 30, "categoria": "tratamiento"},
    "paquete mascarilla y exfoliación": {"precio": "220 MXN", "duracion": 45, "categoria": "tratamiento"},
    "mascarilla de colageno": {"precio": "170 MXN", "duracion": 30, "categoria": "tratamiento"},
    
    # Otros servicios
    "manicure": {"precio": "200 MXN", "duracion": 30, "categoria": "otro"}
}

# Alias de servicios para reconocer diferentes formas de pedirlos
ALIAS_SERVICIOS = {
    "corte": "corte de cabello",
    "corte cabello": "corte de cabello",
    "corte pelo": "corte de cabello",
    "barba": "corte de barba",
    "recorte barba": "corte de barba",
    "barba completa": "corte de barba",
    "combo": "paquete corte y barba",
    "paquete": "paquete corte y barba",
    "combo corte barba": "paquete corte y barba",
    "barba rápida": "barba expres",
    "barba express": "barba expres",
    "corte mujer": "corte de dama",
    "corte dama": "corte de dama",
    "corte niños": "corte de niño",
    "delineado": "delineado de corte",
    "exfoliación facial": "exfoliación",
    "exfoliacion": "exfoliación",
    "mascarilla negra": "mascarilla black",
    "mascarilla colágeno": "mascarilla de colageno",
    "mascarilla colageno": "mascarilla de colageno",
    "uñas": "manicure"
}

# Lista de palabras de saludo para detección más flexible
SALUDOS = ['hola', 'buenos días', 'buenos dias', 'buenas tardes', 'buenas noches', 
           'buen día', 'buen dia', 'saludos', 'hey', 'holi', 'hi', 'quisiera información']

# Mensajes predefinidos
MENSAJES = {
    "bienvenida": "¡Bienvenido a *Barbería d' Leo*! ✂️\n\n"
                 "Puedes preguntar por:\n"
                 "• 'servicios' para ver opciones\n"
                 "• 'agendar' para reservar cita\n\n"
                 f"{HORARIO_TEXTO}",
    "error": "🔧 Ocurrió un error inesperado. Por favor envía 'hola' para comenzar de nuevo.",
    "confirmacion": "✅ ¡Tu cita ha sido confirmada!\n\n"
                   "📆 {fecha}\n"
                   "💇‍♂️ {servicio}\n"
                   "💰 {precio}\n\n"
                   "Te enviaremos un recordatorio 24 horas antes.\n"
                   "Para cancelar, responde con 'cancelar cita'.",
    "recordatorio": "⏰ *RECORDATORIO*\n\nTienes una cita mañana a las {hora} para {servicio}.\n\n"
                    "Si necesitas cancelar, responde 'cancelar cita'.",
    "sin_servicio_seleccionado": "Por favor selecciona primero un servicio de nuestra lista:\n\n{servicios}"
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

# Patrones de expresiones regulares para reconocimiento de entradas
PATRONES = {
    'si': re.compile(r'\b(si|sí|confirmo|aceptar|ok|claro|adelante|procede)\b', re.IGNORECASE),
    'no': re.compile(r'\b(no|cancelar|back|regresar|mejor no|negativo)\b', re.IGNORECASE),
    'agendar': re.compile(r'\b(agendar|cita|reservar|apartar|reservación|quiero una cita)\b', re.IGNORECASE),
    'servicios': re.compile(r'\b(servicio|precio|qué hacen|cuanto cuesta|precios|corte|barba)\b', re.IGNORECASE),
    'reiniciar': re.compile(r'\b(reiniciar|reset|comenzar de nuevo|empezar|otra vez)\b', re.IGNORECASE),
    'cancelar_cita': re.compile(r'\b(cancelar cita|cancelar mi cita|eliminar cita|quitar cita)\b', re.IGNORECASE)
}

# Estados de conversación
conversaciones = {}

def formato_fecha_español(fecha):
    """Devuelve una fecha formateada en español"""
    # Traducción de días de la semana
    dias = {
        'Monday': 'Lunes',
        'Tuesday': 'Martes',
        'Wednesday': 'Miércoles',
        'Thursday': 'Jueves',
        'Friday': 'Viernes',
        'Saturday': 'Sábado',
        'Sunday': 'Domingo'
    }
    
    # Traducción de meses
    meses = {
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
    
    # Formatear la fecha en inglés
    formato_ingles = fecha.strftime('%A %d de %B a las %H:%M')
    
    # Traducir al español
    for ingles, espanol in dias.items():
        formato_ingles = formato_ingles.replace(ingles, espanol)
    
    for ingles, espanol in meses.items():
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
        del conversaciones[remitente]

def get_calendar_service():
    """Obtiene servicio de Google Calendar con manejo mejorado de errores"""
    try:
        cred_json = os.getenv("GOOGLE_CREDENTIALS")
        if cred_json:
            logger.info(f"✓ GOOGLE_CREDENTIALS configurado (longitud: {len(cred_json)} caracteres)")
            try:
                json_data = json.loads(cred_json)
                logger.info(f"✓ GOOGLE_CREDENTIALS parseado correctamente como JSON")
                creds = service_account.Credentials.from_service_account_info(
                    json_data,
                    scopes=['https://www.googleapis.com/auth/calendar']
                )
            except json.JSONDecodeError as e:
                logger.error(f"❌ Error al parsear GOOGLE_CREDENTIALS como JSON: {e}")
                return None
        else:
            logger.warning("⚠️ GOOGLE_CREDENTIALS no configurado, intentando usar archivo local")
            creds_file = os.getenv("GOOGLE_CREDENTIALS_FILE", 'credentials.json')
            try:
                creds = service_account.Credentials.from_service_account_file(
                    creds_file,
                    scopes=['https://www.googleapis.com/auth/calendar']
                )
                logger.info(f"✓ Credenciales cargadas desde archivo local '{creds_file}'")
            except Exception as e:
                logger.error(f"❌ Error al cargar archivo de credenciales: {e}")
                return None
            
        service = build('calendar', 'v3', credentials=creds)
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
            calendarId=CALENDAR_ID,
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
    
    # Determinar hora de cierre para el día específico
    if fecha_inicial.weekday() < 5:  # Lunes a viernes
        hora_cierre = HORA_CIERRE_LUNES_VIERNES
    else:  # Sábado
        hora_cierre = HORA_CIERRE_SABADO
        
    fin_dia = fecha_inicial.replace(hour=hora_cierre, minute=0)
    
    while hora_actual < fin_dia:
        # Avanzar 30 minutos
        hora_actual += timedelta(minutes=30)
        tiempo_fin = hora_actual + timedelta(minutes=duracion_minutos)
        
        # Omitir si ya pasamos el horario de cierre
        if tiempo_fin.hour >= hora_cierre:
            return None
            
        # Verificar si está libre
        eventos = service.events().list(
            calendarId=CALENDAR_ID,
            timeMin=hora_actual.isoformat(),
            timeMax=tiempo_fin.isoformat(),
            singleEvents=True
        ).execute()
        
        if len(eventos.get('items', [])) == 0:
            return hora_actual
    
    return None

def mostrar_servicios(por_categoria=True):
    """Genera texto con los servicios disponibles, opcionalmente agrupados por categoría"""
    if por_categoria:
        # Agrupar servicios por categoría
        servicios_por_categoria = {}
        for servicio, detalles in SERVICIOS.items():
            categoria = detalles.get('categoria', 'otros')
            if categoria not in servicios_por_categoria:
                servicios_por_categoria[categoria] = []
            servicios_por_categoria[categoria].append((servicio, detalles))
        
        # Títulos amigables para las categorías
        titulos_categorias = {
            'corte': '✂️ CORTES',
            'tratamiento': '✨ TRATAMIENTOS',
            'otro': '🛠️ OTROS SERVICIOS'
        }
        
        # Generar texto por categorías
        servicios_texto = "💈 *SERVICIOS DISPONIBLES* 💈\n\n"
        for categoria, servicios in servicios_por_categoria.items():
            servicios_texto += f"*{titulos_categorias.get(categoria, categoria.upper())}*\n"
            for servicio, detalles in servicios:
                servicios_texto += f"• {servicio.capitalize()}: {detalles['precio']} ({detalles['duracion']} min)\n"
            servicios_texto += "\n"
    else:
        # Formato simple sin categorías
        servicios_texto = "💈 *Servicios disponibles* 💈\n\n"
        for servicio, detalles in SERVICIOS.items():
            servicios_texto += f"• ✂️ {servicio.capitalize()}: {detalles['precio']} ({detalles['duracion']} min)\n"
    
    servicios_texto += "\n_Responde con el nombre del servicio que deseas_"
    return servicios_texto

def crear_evento_calendario(datos_cita):
    """Crea un evento en Google Calendar"""
    service = get_calendar_service()
    if not service:
        logger.error("❌ No se pudo obtener el servicio de Google Calendar")
        return True, "sin-calendario"  # Simulamos éxito para no bloquear al usuario
    
    try:
        logger.info(f"🔍 Intentando crear evento para {datos_cita['nombre']} el {datos_cita['fecha']}")
        
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
        
        logger.info(f"🔍 Datos del evento: {evento}")
        
        evento_creado = service.events().insert(
            calendarId=CALENDAR_ID,
            body=evento,
            sendUpdates='all'
        ).execute()
        
        logger.info(f"✅ Evento creado con ID: {evento_creado.get('id')}")
        return True, evento_creado.get('id')
    except HttpError as e:
        logger.error(f"❌ Error de Google API al crear evento: {e}", exc_info=True)
        return True, "error-http"  # Simulamos éxito para no bloquear al usuario
    except Exception as e:
        logger.error(f"❌ Error desconocido al crear evento: {e}", exc_info=True)
        return True, "error-desconocido"  # Simulamos éxito para no bloquear al usuario