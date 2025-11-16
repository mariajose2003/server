import os
import json
import eventlet # Necesario para SocketIO en Railway
from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit # NUEVOS IMPORTS
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
socketio = SocketIO(app, async_mode='eventlet', message_queue=database_url) 
# -----------------------------


# --- MODELO DE LA BASE DE DATOS ---
class Licencia(db.Model):
    # ... (Modelo omitido por ser idéntico al anterior)
    id = db.Column(db.Integer, primary_key=True)
    codigo_licencia = db.Column(db.String(36), unique=True, nullable=False)
    hwid_activacion = db.Column(db.String(100), nullable=True, default=None)
    fecha_activacion = db.Column(db.DateTime(timezone=True), nullable=True, default=None)
    token_sesion = db.Column(db.String(32), unique=True, nullable=True, default=None)
    fecha_expiracion = db.Column(db.DateTime(timezone=True), nullable=True, default=None)
    buyer_email = db.Column(db.String(100), nullable=True, default=None)
    socket_id = db.Column(db.String(50), nullable=True, default=None) # NUEVO: Para enviar mensajes directos
    # ...

# --- RUTAS Y FUNCIONES DE ASISTENCIA ---

# ... (send_key_to_buyer, index, handle_kofi_payment, generar_claves son idénticos) ...
# ... (Por favor, inserta todas esas funciones, incluyendo send_key_to_buyer)

# --- FUNCIÓN HELPER PARA ENVIAR EMAIL ---
# (Asumo que esta función fue pegada aquí en el código final del usuario)
def send_key_to_buyer(key, email, is_renovating, was_active_and_extended):
    # ... (código de SendGrid) ...
    # NOTA: Debes pegar toda tu función send_key_to_buyer aquí.
    return True # Placeholder

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
# (La función handle_kofi_payment es idéntica, pero debe actualizar el socket_id si el usuario renueva)
@app.route('/kofi-webhook', methods=['POST'])
def handle_kofi_payment():
    # NOTA: Dentro de esta función, la lógica de renovación debe actualizar el campo 'socket_id = None'
    # ... (Pega tu código de handle_kofi_payment aquí) ...
    return "OK (Ignorado)", 200

# 3. RUTA PARA GENERAR CLAVES - (Se mantiene HTTP)
@app.route('/admin/generar_claves/<int:cantidad>', methods=['POST'])
def generar_claves(cantidad):
    # ... (Pega tu código de generar_claves aquí) ...
    return jsonify({"success": True, "mensaje": f"Se generaron {cantidad} licencias."}), 200


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
                "expiracion": licencia.fecha_expiracion.isoformat()
            })
            
            # (Inicias aquí la comprobación de expiración en segundo plano)
            # socketio.start_background_task(target=check_license_expiration, license_id=licencia.id) 
            
        elif licencia.hwid_activacion != hwid_cliente:
            # CASO 2: BLOQUEO POR HWID
            emit('license_response', {"success": False, "mensaje": "Licencia vinculada a otro dispositivo."})

        elif ahora.date() > licencia.fecha_expiracion.date():
            # CASO 3: EXPIRADA
            emit('license_response', {"success": False, "mensaje": "Licencia expirada. Renueve."})

        else:
            # CASO 4: REVALIDACIÓN EXITOSA
            licencia.token_sesion = str(uuid4().hex[:32])
            licencia.socket_id = session_id # ACTUALIZAMOS EL ID DE SESIÓN
            db.session.commit()
            emit('license_response', {"success": True, "mensaje": "Revalidación exitosa."})


# --- ARRANQUE DE LA APLICACIÓN (Importante para SocketIO) ---
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    
    # En lugar de app.run(), usamos socketio.run() con eventlet
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, allow_unsafe_werkzeug=True)