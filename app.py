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
database_url = os.environ.get('DATABASE_URL')
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

# 2. RUTA DEL WEBHOOK DE KO-FI (¡LÓGICA MEJORADA!)
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
            # --- INICIO DE LA NUEVA LÓGICA ---
            clave_a_enviar_str = None
            
            # 1. Buscar una clave disponible (sin dueño)
            # Usamos "with_for_update()" para bloquear la fila y evitar que dos compradores agarren la misma
            licencia_disponible = db.session.query(Licencia).filter(Licencia.buyer_email == None).with_for_update().first()
            
            if licencia_disponible:
                # 2A. Si encontramos una, la asignamos al comprador
                licencia_disponible.buyer_email = buyer_email
                clave_a_enviar_str = licencia_disponible.codigo_licencia
                print(f"Clave existente {clave_a_enviar_str} asignada a {buyer_email}")
            else:
                # 2B. Si no hay, creamos una nueva
                clave_a_enviar_str = str(uuid4())
                nueva_licencia = Licencia(
                    codigo_licencia=clave_a_enviar_str,
                    buyer_email=buyer_email
                )
                db.session.add(nueva_licencia)
                print(f"No hay claves disponibles. Nueva clave {clave_a_enviar_str} generada para {buyer_email}")

            # 3. Guardar los cambios en la DB (sea update o insert)
            db.session.commit()
            # --- FIN DE LA NUEVA LÓGICA ---

            # 4. Enviar la clave por email al comprador
            if send_key_to_buyer(clave_a_enviar_str, buyer_email):
                print(f"Clave enviada exitosamente a {buyer_email}")
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
# ¡PROTEGE ESTA RUTA! (ej. /admin/generar_claves_super_secreto/10)
@app.route('/admin/generar_claves/<int:cantidad>', methods=['POST'])
def generar_claves(cantidad):
    """Genera un número específico de licencias únicas y las guarda en la DB."""
    if cantidad <= 0 or cantidad > 100:
        return jsonify({"success": False, "mensaje": "Cantidad inválida (1-100)."}), 400

    try:
        for _ in range(cantidad):
            codigo = str(uuid4())
            # Esto crea la clave con buyer_email = NULL (disponible)
            nueva_licencia = Licencia(codigo_licencia=codigo) 
            db.session.add(nueva_licencia)

        db.session.commit()
        return jsonify({
            "success": True,
            "mensaje": f"Se generaron y guardaron {cantidad} licencias 'disponibles'."
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "mensaje": f"Error al generar claves: {str(e)}"}), 500


# --- ARRANQUE DE LA APLICACIÓN ---
if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    # Railway necesita que escuches en 0.0.0.0 y el puerto que él te da
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)