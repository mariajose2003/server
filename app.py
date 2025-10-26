import os
import json
from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from uuid import uuid4
from datetime import datetime, timedelta
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

# --- INICIALIZACIÓN Y CONFIGURACIÓN ---
app = Flask(__name__)

# 1. Variables de Entorno (Railway las provee)
database_url = os.environ.get('DB_FINAL_URL')
SENDGRID_API_KEY = os.environ.get('SENDGRID_API_KEY')
MY_EMAIL = os.environ.get('MY_EMAIL') # El email desde el que envías

# 2. Validar Configuración Crítica
if not database_url or not SENDGRID_API_KEY or not MY_EMAIL:
    raise RuntimeError("ERROR CRÍTICO: Faltan variables de entorno (DB_FINAL_URL, SENDGRID_API_KEY, o MY_EMAIL).")

# 3. Corregir el prefijo de la DB para SQLAlchemy
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql+psycopg2://", 1)
elif database_url.startswith("postgresql://"):
    database_url = database_url.replace("postgresql://", "postgresql+psycopg2://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# --- MODELO DE LA BASE DE DATOS ---
class Licencia(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    codigo_licencia = db.Column(db.String(36), unique=True, nullable=False) # UUID
    hwid_activacion = db.Column(db.String(100), nullable=True, default=None) # Hardware ID
    fecha_activacion = db.Column(db.DateTime, nullable=True, default=None)
    token_sesion = db.Column(db.String(32), unique=True, nullable=True, default=None)
    fecha_expiracion = db.Column(db.DateTime, nullable=True, default=None)
    # ¡AÑADIDO! Guardamos el email del comprador para referencia
    buyer_email = db.Column(db.String(100), nullable=True, default=None)

    def __repr__(self):
        return f'<Licencia {self.codigo_licencia}>'

# --- FUNCIÓN HELPER PARA ENVIAR EMAIL ---
def send_key_to_buyer(key, email):
    """Usa SendGrid para enviar la clave al comprador."""
    message = Mail(
        from_email=MY_EMAIL,
        to_emails=email,
        subject='¡Tu clave de producto para MagicDrive PRO!',
        html_content=f'¡Gracias por tu compra! <br><br>Tu clave de activación es: <strong>{key}</strong><br><br>Guarda este email.'
    )
    try:
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        response = sg.send(message)
        return response.status_code == 202
    except Exception as e:
        print(f"Error al enviar email: {e}")
        return False

# --- RUTAS DE LA API ---

# 1. RUTA DE SALUD (HEALTH CHECK)
@app.route('/', methods=['GET'])
def index():
    """Ruta para verificar que la API está viva y funcionando."""
    try:
        with app.app_context():
            db.create_all() # Asegura que las tablas existan
        return jsonify({"status": "API Activa", "message": "Conexión DB OK."}), 200
    except Exception as e:
        return jsonify({"status": "API Activa, pero DB Falló", "error": str(e)}), 500

# 2. RUTA DEL WEBHOOK DE KO-FI (¡NUEVA!)
@app.route('/kofi-webhook', methods=['POST'])
def handle_kofi_payment():
    try:
        data_string = request.form.get('data')
        if not data_string:
            print("Webhook recibido pero sin campo 'data'.")
            return "Error: No data field", 400
        
        payment_data = json.loads(data_string)
        
    except Exception as e:
        print(f"Error al decodificar JSON de Ko-fi: {e}")
        return "Error", 400

    # Verificamos que sea una orden de la tienda
    if payment_data.get('type') == 'Shop Order':
        
        buyer_email = payment_data.get('email')
        if not buyer_email:
            print("Shop Order recibido, pero sin email.")
            return "Error: No email", 400

        try:
            # 1. Generar 1 nueva clave
            nueva_clave_str = str(uuid4())
            
            # 2. Guardarla en la base de datos
            nueva_licencia = Licencia(
                codigo_licencia=nueva_clave_str,
                buyer_email=buyer_email
            )
            db.session.add(nueva_licencia)
            db.session.commit()

            # 3. Enviar la clave por email al comprador
            if send_key_to_buyer(nueva_clave_str, buyer_email):
                print(f"Clave {nueva_clave_str} generada y enviada a {buyer_email}")
                return "OK", 200 # ¡Éxito!
            else:
                print(f"Error al ENVIAR email a {buyer_email}")
                return "Error interno de email", 500
                        
        except Exception as e:
            print(f"Error de base de datos o email: {e}")
            db.session.rollback()
            return "Error de servidor", 500
            
    # Si no es un "Shop Order", lo ignoramos
    return "OK (Ignorado)", 200

# 3. RUTA DE ACTIVACIÓN DE LICENCIAS (Tu código original, sin cambios)
# (Solo quité el duplicado)
@app.route('/api/activar', methods=['POST'])
def activar_licencia():
    data = request.get_json()
    codigo_licencia = data.get('codigo')
    hwid_cliente = data.get('hwid')

    if not codigo_licencia or not hwid_cliente:
        return jsonify({"success": False, "mensaje": "Faltan datos de código o HWID."}), 400

    licencia = Licencia.query.filter_by(codigo_licencia=codigo_licencia).first()

    if not licencia:
        return jsonify({"success": False, "mensaje": "Licencia inválida o no encontrada."}), 404

    # CASO 1: LICENCIA VIRGEN (Primera Activación)
    if licencia.hwid_activacion is None:
        licencia.hwid_activacion = hwid_cliente
        licencia.fecha_activacion = datetime.utcnow()
        nuevo_token = uuid4().hex[:32]
        licencia.token_sesion = nuevo_token
        licencia.fecha_expiracion = datetime.utcnow() + timedelta(days=365) # 1 año de licencia
        
        db.session.commit()
        return jsonify({
            "success": True,
            "mensaje": f"Licencia activada. Válida hasta {licencia.fecha_expiracion.strftime('%Y-%m-%d')}.",
            "token": nuevo_token,
            "expiracion": licencia.fecha_expiracion.isoformat()
        }), 201 # 201 Created

    # CASO 2: LICENCIA ACTIVADA EN OTRO HWID (Bloqueo)
    if licencia.hwid_activacion != hwid_cliente:
        return jsonify({
            "success": False,
            "mensaje": "Licencia ya está vinculada a otro dispositivo."
        }), 403 # 403 Forbidden
    
    # CASO 3: LICENCIA EXPIRADA
    if datetime.utcnow() > licencia.fecha_expiracion:
        return jsonify({
            "success": False,
            "mensaje": f"Tu licencia expiró el {licencia.fecha_expiracion.strftime('%Y-%m-%d')}. Adquiere una nueva."
        }), 403 # 403 Forbidden
            
    # CASO 4: LICENCIA VÁLIDA (Revalidación / Heartbeat)
    nuevo_token = uuid4().hex[:32]
    licencia.token_sesion = nuevo_token
    db.session.commit()
    return jsonify({
        "success": True,
        "mensaje": "Licencia revalidada exitosamente.",
        "token": nuevo_token,
        "expiracion": licencia.fecha_expiracion.isoformat()
    }), 200

# --- ARRANQUE DE LA APLICACIÓN ---
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    # Railway necesita que escuches en 0.0.0.0 y el puerto que él te da
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)