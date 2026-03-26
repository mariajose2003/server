import os
import json
from dotenv import load_dotenv
load_dotenv()  # ← Esto carga el .env
#import eventlet
from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit
from uuid import uuid4
from datetime import datetime, timedelta, timezone 
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

# --- INICIALIZACIÓN Y CONFIGURACIÓN ---
app = Flask(__name__)

# 1. Variables de Entorno
database_url = os.environ.get('DATABASE_URL')
SENDGRID_API_KEY = os.environ.get('SENDGRID_API_KEY')
MY_EMAIL = os.environ.get('MY_EMAIL')

# 2. Validar Configuración Crítica
if not database_url or not SENDGRID_API_KEY or not MY_EMAIL:
    raise RuntimeError("ERROR CRÍTICO: Faltan variables de entorno (DATABASE_URL, SENDGRID_API_KEY, o MY_EMAIL).")

# 3. Corregir el prefijo de la DB para SQLAlchemy
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql+psycopg2://", 1)
elif database_url.startswith("postgresql://"):
    database_url = database_url.replace("postgresql://", "postgresql+psycopg2://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# --- INICIALIZAR SOCKETIO ---
# Usamos message_queue para que los workers puedan comunicarse entre sí (necesario en Railway)
socketio = SocketIO(app, async_mode='threading', cors_allowed_origins='*') 
# -----------------------------


# --- MODELO DE LA BASE DE DATOS ---
class Licencia(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    codigo_licencia = db.Column(db.String(36), unique=True, nullable=False)
    hwid_activacion = db.Column(db.String(100), nullable=True, default=None)
    fecha_activacion = db.Column(db.DateTime(timezone=True), nullable=True, default=None)
    token_sesion = db.Column(db.String(32), unique=True, nullable=True, default=None)
    fecha_expiracion = db.Column(db.DateTime(timezone=True), nullable=True, default=None)
    buyer_email = db.Column(db.String(100), nullable=True, default=None)
    socket_id = db.Column(db.String(50), nullable=True, default=None) # NUEVO: Para enviar mensajes directos

# --- RUTAS Y FUNCIONES DE ASISTENCIA ---

# ... (send_key_to_buyer, index, handle_kofi_payment, generar_claves son idénticos) ...
# ... (Por favor, inserta todas esas funciones, incluyendo send_key_to_buyer)

# --- FUNCIÓN HELPER PARA ENVIAR EMAIL ---
def send_key_to_buyer(key, email, is_renovating, was_active_and_extended):
    try:
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        from_email = MY_EMAIL
        to_email = email
        subject = "Tu Licencia de Software" if not is_renovating else "Renovación de Licencia de Software"
        content = f"Hola,\n\nTu clave de licencia es: {key}\n\n"
        if is_renovating:
            content += "Tu licencia ha sido renovada exitosamente.\n"
            if was_active_and_extended:
                content += "Como tu licencia estaba activa, se ha extendido la fecha de expiración.\n"
        else:
            content += "Gracias por tu compra.\n"
        content += "\nSaludos,\nEquipo de Soporte"
        
        message = Mail(from_email=from_email, to_emails=to_email, subject=subject, plain_text_content=content)
        sg.send(message)
        print(f"Email enviado a {email} con clave {key}")
        return True
    except Exception as e:
        print(f"Error enviando email: {e}")
        return False

# 1. RUTA DE SALUD (HEALTH CHECK) - (Se mantiene HTTP)
@app.route('/', methods=['GET'])
def index():
    try:
        with app.app_context():
            db.create_all() 
        return jsonify({"status": "API Activa", "message": "Conexión DB OK."}), 200
    except Exception as e:
        return jsonify({"status": "API Activa, pero DB Falló", "error": str(e)}), 500

# 2. RUTA DEL WEBHOOK DE KO-FI - (Se mantiene HTTP)
@app.route('/kofi-webhook', methods=['POST'])
def handle_kofi_payment():
    try:
        data = request.get_json()
        if not data:
            return "No data", 400
        
        # Verificar token de verificación si es necesario (opcional)
        # verification_token = data.get('verification_token')
        # if verification_token != os.environ.get('KOFI_VERIFICATION_TOKEN'):
        #     return "Invalid token", 403
        
        buyer_email = data.get('email')
        amount = data.get('amount')
        message = data.get('message', '')  # Mensaje del comprador
        
        if not buyer_email:
            return "Email faltante", 400
        
        with app.app_context():
            # Verificar si es renovación
            if "RENOVAR:" in message.upper():
                # Es renovación
                codigo_licencia = message.split("RENOVAR:")[1].strip()
                licencia = Licencia.query.filter_by(codigo_licencia=codigo_licencia, buyer_email=buyer_email).first()
                if licencia:
                    # Extender expiración
                    licencia.fecha_expiracion += timedelta(days=365)
                    licencia.socket_id = None  # Resetear socket_id para forzar revalidación
                    db.session.commit()
                    # Enviar confirmación
                    send_key_to_buyer(codigo_licencia, buyer_email, True, True)
                    print(f"Licencia renovada: {codigo_licencia}")
                else:
                    print(f"Licencia no encontrada para renovación: {codigo_licencia}")
                    return "Licencia no encontrada", 400
            else:
                # Nueva compra
                nueva_licencia = Licencia(
                    codigo_licencia=str(uuid4()),
                    buyer_email=buyer_email,
                    fecha_expiracion=datetime.now(timezone.utc) + timedelta(days=365)
                )
                db.session.add(nueva_licencia)
                db.session.commit()
                # Enviar clave
                send_key_to_buyer(nueva_licencia.codigo_licencia, buyer_email, False, False)
                print(f"Nueva licencia generada: {nueva_licencia.codigo_licencia}")
        
        return "OK", 200
    except Exception as e:
        print(f"Error en webhook: {e}")
        return "Error", 500

# 3. RUTA PARA GENERAR CLAVES - (Se mantiene HTTP)
@app.route('/admin/generar_claves/<int:cantidad>', methods=['POST'])
def generar_claves(cantidad):
    if cantidad <= 0 or cantidad > 100:
        return jsonify({"success": False, "mensaje": "Cantidad inválida (1-100)."}), 400
    
    try:
        with app.app_context():
            for _ in range(cantidad):
                nueva_licencia = Licencia(codigo_licencia=str(uuid4()))
                db.session.add(nueva_licencia)
            db.session.commit()
        return jsonify({"success": True, "mensaje": f"Se generaron {cantidad} licencias."}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# --- NUEVOS EVENTOS SOCKETIO (REEMPLAZA /api/activar) ---

@socketio.on('connect')
def handle_connect():
    """Evento que se dispara al abrir la conexión Socket."""
    print(f"SOCKET: Cliente conectado. SID: {request.sid}")
    # Podemos usar request.sid como ID temporal de la sesión

@socketio.on('activar')
def handle_activacion(data):
    """
    Recibe la clave y el HWID del cliente y realiza la activación o revalidación.
    """
    # Esta función reemplaza la ruta HTTP /api/activar
    with app.app_context():
        codigo_licencia = data.get('codigo')
        hwid_cliente = data.get('hwid')
        session_id = request.sid

        if not codigo_licencia or not hwid_cliente:
            emit('license_response', {"success": False, "mensaje": "Faltan datos."})
            return

        licencia = Licencia.query.filter_by(codigo_licencia=codigo_licencia).first()

        if not licencia:
            emit('license_response', {"success": False, "mensaje": "Licencia no encontrada."})
            return
        
        ahora = datetime.now(timezone.utc)
        
        # --- LÓGICA DE ACTIVACIÓN ---
        if licencia.hwid_activacion is None:
            # CASO 1: ACTIVACIÓN VIRGEN
            licencia.hwid_activacion = hwid_cliente
            licencia.fecha_activacion = ahora
            licencia.token_sesion = str(uuid4().hex[:32])
            licencia.fecha_expiracion = ahora + timedelta(days=365)
            licencia.socket_id = session_id # GUARDAMOS EL ID DE LA SESIÓN ACTIVA

            db.session.commit()
            emit('license_response', {
                "success": True,
                "mensaje": "Activación exitosa.",
                "expiracion": licencia.fecha_expiracion.isoformat(),
                "token_sesion": licencia.token_sesion
            })
            
            # (Inicias aquí la comprobación de expiración en segundo plano)
            socketio.start_background_task(target=check_license_expiration, license_id=licencia.id) 
            
        elif licencia.hwid_activacion != hwid_cliente:
            # CASO 2: BLOQUEO POR HWID
            emit('license_response', {"success": False, "mensaje": "Licencia vinculada a otro dispositivo."})

        elif ahora > licencia.fecha_expiracion:
            emit('license_response', {
                "success": False,
                "mensaje": "Licencia expirada. Renueve."
            })
            return
        
        else:
    # CASO 4: REVALIDACIÓN
            token_cliente = data.get('token_sesion')

        # Si el HWID coincide
        if licencia.hwid_activacion == hwid_cliente:
            # Validar token solo si el cliente lo envió
            if licencia.token_sesion and token_cliente:
                if token_cliente != licencia.token_sesion:
                    emit('license_response', {"success": False, "mensaje": "Token inválido. Ingresa tu clave nuevamente."})
                    return

            # Generar nuevo token y revalidar
            licencia.token_sesion = str(uuid4().hex[:32])
            licencia.socket_id = session_id
            db.session.commit()
            emit('license_response', {
                "success": True,
                "mensaje": "Revalidación exitosa.",
                "expiracion": licencia.fecha_expiracion.isoformat(),
                "token_sesion": licencia.token_sesion
            })
        else:
            # HWID distinto → bloqueo inmediato
            emit('license_response', {"success": False, "mensaje": "Licencia vinculada a otro dispositivo."})

# --- FUNCIÓN PARA COMPROBAR EXPIRACIÓN EN SEGUNDO PLANO ---
def check_license_expiration(license_id):
    while True:
        socketio.sleep(3600)  # Chequear cada hora
        with app.app_context():
            licencia = Licencia.query.get(license_id)
            if licencia and licencia.socket_id and datetime.now(timezone.utc) > licencia.fecha_expiracion:
                socketio.emit('license_expired', {"mensaje": "Tu licencia ha expirado. Por favor, renueva."}, to=licencia.socket_id)
                break


# --- ARRANQUE DE LA APLICACIÓN (Importante para SocketIO) ---
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    
    # En lugar de app.run(), usamos socketio.run() con eventlet
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, allow_unsafe_werkzeug=True)
