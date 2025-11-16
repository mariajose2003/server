import os
import json
from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from uuid import uuid4
from datetime import datetime, timedelta, timezone # Importar timezone
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
    raise RuntimeError("ERROR CRÍTICO: Faltan variables de entorno (DATABASE_URL, SENDGRID_API_KEY, o MY_EMAIL).")

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
    fecha_activacion = db.Column(db.DateTime(timezone=True), nullable=True, default=None) # Añadido timezone=True
    token_sesion = db.Column(db.String(32), unique=True, nullable=True, default=None)
    fecha_expiracion = db.Column(db.DateTime(timezone=True), nullable=True, default=None) # Añadido timezone=True
    buyer_email = db.Column(db.String(100), nullable=True, default=None)

    def __repr__(self):
        return f'<Licencia {self.codigo_licencia}>'

# --- FUNCIÓN HELPER PARA ENVIAR EMAIL (¡MODIFICADA CON DETECCIÓN!) ---
def send_key_to_buyer(key, email, is_renovating, was_active_and_extended):
    """Usa SendGrid para enviar la clave al comprador."""

    # ¡IMPORTANTE!
    URL_DEL_INSTALADOR_ZIP = "https://drive.google.com/file/d/TU-ENLACE-AQUI/view?usp=sharing" # <- ¡CAMBIA ESTO!

    # --- INICIO DE LA LÓGICA DE EMAIL DINÁMICO ---
    
    seccion_nuevos = f"""
    <div class="section">
        <h2>¿Eres un usuario nuevo?</h2>
        <p>Sigue estos 4 pasos para empezar:</p>
        <ol>
            <li><b>Descarga el archivo:</b> Haz clic en el enlace para descargar el instalador (es un archivo .zip):<br>
                <a href="{URL_DEL_INSTALADOR_ZIP}"><b>Descargar MagicDrive PRO (.zip)</b></a>
            </li>
            <li>
                <b>Descomprime el archivo:</b> Ve a tus Descargas, busca el archivo .zip, haz clic derecho sobre él y selecciona "<b>Extraer todo...</b>" o "<b>Unzip</b>".
            </li>
            <li>
                <b>Ejecuta la aplicación:</b> Abre la nueva carpeta que se creó y haz doble clic en <b>MagicDrivePRO.exe</b>.
            </li>
            <li>
                <b>Activa el producto:</b> La aplicación te pedirá una llave. Copia y pega la llave que está arriba en este email.
            </li>
        </ol>
    </div>
    """
    
    seccion_renovacion_expirada = """
    <div class="section">
        <h2>¿Estás renovando tu licencia?</h2>
        <p>¡Gracias por continuar con nosotros! El proceso es muy sencillo:</p>
        <ol>
            <li><b>Ignora el enlace de descarga.</b> (Ya tienes la aplicación instalada).</li>
            <li>Abre tu app MagicDrive PRO. Verás la ventana de "Licencia Expirada".</li>
            <li>Haz clic en el botón <b>"🔑 Ya tengo una llave (Activar)"</b>.</li>
            <li>Aparecerá la ventana de "Activación". Pega allí tu <b>nueva llave</b> (la de este correo).</li>
            <li>Haz clic en el botón <b>"Activar"</b> (o presiona la tecla <b>Enter</b>).</li>
        </ol>
        <p style="margin-top: 10px;">¡Y listo! Tu acceso se renovará automáticamente por un año más.</p>
    </div>
    """
    
    seccion_renovacion_extendida = """
    <div class="section">
        <h2>¡Licencia Extendida!</h2>
        <p>¡Gracias por renovar antes de tiempo! Hemos <b>sumado 365 días</b> a tu licencia actual.</p>
        <p><b>No necesitas hacer nada.</b> Tu aplicación se actualizará automáticamente con la nueva fecha de expiración.</p>
        <p>Tu llave sigue siendo la misma:</p>
    </div>
    """
    
    instrucciones_html = ""
    titulo_principal = "¡Gracias por tu compra de MagicDrive PRO!"
    
    if is_renovating and was_active_and_extended:
        instrucciones_html = seccion_renovacion_extendida
        titulo_principal = "¡Tu licencia de MagicDrive PRO ha sido extendida!"
    elif is_renovating and not was_active_and_extended:
        instrucciones_html = seccion_renovacion_expirada
    else:
        instrucciones_html = seccion_nuevos

    html_content = f"""
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; }}
            .container {{ width: 90%; margin: auto; padding: 20px; }}
            .key {{
                font-size: 20px;
                font-weight: bold;
                color: #007bff;
                background-color: #f4f4f4;
                padding: 10px;
                border-radius: 5px;
                text-align: center;
                font-family: 'Courier New', Courier, monospace;
            }}
            .section {{ margin-top: 30px; border-top: 1px solid #ddd; padding-top: 20px; }}
            h2 {{ color: #333; }}
            li {{ margin-bottom: 10px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>{titulo_principal}</h1>
            
            <p>Tu llave de licencia única es:</p>
            <div class="key">{key}</div>

            <!-- Aquí se insertan las instrucciones correctas -->
            {instrucciones_html}

            <p style="margin-top: 30px; font-size: 12px; color: #777;">
                Si tienes algún problema, contacta a soporte: {MY_EMAIL}
            </p>
        </div>
    </body>
    </html>
    """

    message = Mail(
        from_email=MY_EMAIL,
        to_emails=email,
        subject='¡Tu clave de producto para MagicDrive PRO!',
        html_content=html_content
    )
    try:
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        response = sg.send(message)
        return response.status_code == 202
    except Exception as e:
        print(f"Error al enviar email: {e}")
        return False

# --- RUTAS DE LA API ---

@app.route('/', methods=['GET'])
def index():
    """Ruta para verificar que la API está viva y funcionando."""
    try:
        with app.app_context():
            db.create_all()
        return jsonify({"status": "API Activa", "message": "Conexión DB OK."}), 200
    except Exception as e:
        return jsonify({"status": "API Activa, pero DB Falló", "error": str(e)}), 500

# 2. RUTA DEL WEBHOOK DE KO-FI (¡LÓGICA MEJORADA POR TI!)
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

    if payment_data.get('type') == 'Shop Order':
        
        buyer_email = payment_data.get('email')
        if not buyer_email:
            print("Shop Order recibido, pero sin email.")
            return "Error: No email", 400

        try:
            clave_a_enviar_str = None
            was_active_and_extended = False # Flag para el email
            
            licencia_previa = db.session.query(Licencia).filter_by(buyer_email=buyer_email).first()
            is_renovating = (licencia_previa is not None)
            
            if is_renovating:
                print(f"Detectado usuario de renovación: {buyer_email}")
                licencia_a_renovar = db.session.query(Licencia).filter_by(buyer_email=buyer_email).order_by(Licencia.fecha_expiracion.desc()).first()
                clave_a_enviar_str = licencia_a_renovar.codigo_licencia

                # --- ¡NUEVA LÓGICA DE "STACKING"! ---
                if licencia_a_renovar.fecha_expiracion and datetime.now(timezone.utc) < licencia_a_renovar.fecha_expiracion:
                    # --- CASO 1: AÚN ACTIVA (Sumar días) ---
                    print(f"Licencia {clave_a_enviar_str} aún está activa. Sumando 365 días.")
                    licencia_a_renovar.fecha_expiracion = licencia_a_renovar.fecha_expiracion + timedelta(days=365)
                    licencia_a_renovar.token_sesion = None
                    was_active_and_extended = True
                    print(f"Nueva expiración: {licencia_a_renovar.fecha_expiracion}")

                else:
                    # --- CASO 2: EXPIRADA (Resetear) ---
                    print(f"Licencia {clave_a_enviar_str} está expirada. Reseteando para reactivación.")
                    licencia_a_renovar.hwid_activacion = None
                    licencia_a_renovar.fecha_activacion = None
                    licencia_a_renovar.token_sesion = None
                    licencia_a_renovar.fecha_expiracion = None # Se pondrá en la API

            else:
                # --- LÓGICA DE USUARIO NUEVO (Crear renglón) ---
                print(f"Detectado usuario nuevo: {buyer_email}")
                clave_a_enviar_str = str(uuid4())
                nueva_licencia = Licencia(
                    codigo_licencia=clave_a_enviar_str,
                    buyer_email=buyer_email
                )
                db.session.add(nueva_licencia)
                print(f"Nueva clave {clave_a_enviar_str} generada para {buyer_email}")

            db.session.commit()

            if send_key_to_buyer(clave_a_enviar_str, buyer_email, is_renovating, was_active_and_extended):
                print(f"Clave enviada exitosamente a {buyer_email}")
                return "OK", 200
            else:
                print(f"Error al ENVIAR email a {buyer_email}")
                return "Error interno de email", 500
                    
        except Exception as e:
            print(f"Error de base de datos o email: {e}")
            db.session.rollback()
            return "Error de servidor", 500
            
    return "OK (Ignorado)", 200


# 3. RUTA PARA GENERAR CLAVES
@app.route('/admin/generar_claves/<int:cantidad>', methods=['POST'])
def generar_claves(cantidad):
    """Genera un número específico de licencias únicas y las guarda en la DB."""
    if cantidad <= 0 or cantidad > 100:
        return jsonify({"success": False, "mensaje": "Cantidad inválida (1-100)."}), 400

    try:
        for _ in range(cantidad):
            codigo = str(uuid4())
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

# 4. RUTA DE ACTIVACIÓN DE LICENCIAS
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

    ahora = datetime.now(timezone.utc)

    # CASO 1: LICENCIA VIRGEN (Primera Activación O Renovación Reseteada)
    if licencia.hwid_activacion is None:
        licencia.hwid_activacion = hwid_cliente
        licencia.fecha_activacion = ahora
        nuevo_token = str(uuid4().hex[:32])
        licencia.token_sesion = nuevo_token
        
        # Si la fecha ya está en el futuro (por "stacking"), no la sobrescribas
        if not licencia.fecha_expiracion or licencia.fecha_expiracion < ahora:
            licencia.fecha_expiracion = ahora + timedelta(days=365) # 1 año
        
        db.session.commit()
        return jsonify({
            "success": True,
            "mensaje": f"Licencia activada. Válida hasta {licencia.fecha_expiracion.strftime('%Y-%m-%d')}.",
            "token": nuevo_token,
            "expiracion": licencia.fecha_expiracion.isoformat()
        }), 201

    # CASO 2: LICENCIA ACTIVADA EN OTRO HWID (Bloqueo)
    if licencia.hwid_activacion != hwid_cliente:
        return jsonify({
            "success": False,
            "mensaje": "Licencia ya está vinculada a otro dispositivo."
        }), 403
    
    # --- ¡CAMBIO AQUÍ! ---
    # Comparamos solo la .date() (el día), ignorando la hora UTC.
    # Así, la licencia es válida HASTA el último segundo del día de expiración.
    #
    # CASO 3: LICENCIA EXPIRADA
    if ahora.date() > licencia.fecha_expiracion.date():
        return jsonify({
            "success": False,
            "mensaje": f"Tu licencia expiró el {licencia.fecha_expiracion.strftime('%Y-%m-%d')}. Adquiere una nueva."
        }), 403
            
    # CASO 4: LICENCIA VÁLIDA (Revalidación / Heartbeat)
    nuevo_token = str(uuid4().hex[:32])
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
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)