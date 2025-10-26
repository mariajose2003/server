import os
from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from uuid import uuid4
from datetime import datetime, timedelta

# Inicialización de la aplicación Flask
app = Flask(__name__)

# --- CONFIGURACIÓN DE LA BASE DE DATOS ---
# 1. Ahora buscamos la variable personalizada 'DB_FINAL_URL'
database_url = os.environ.get('DB_FINAL_URL')

# Si la variable NO se encuentra, ¡EL SERVIDOR NO DEBE ARRANCAR!
if not database_url:
    # Esto es un error crítico si la app se está ejecutando en Railway
    raise RuntimeError("ERROR CRÍTICO: La variable DB_FINAL_URL no está configurada. La aplicación no puede conectarse a la base de datos.")

# 2. Corregir el prefijo (Driver psyscopg2)
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql+psycopg2://", 1)
elif database_url.startswith("postgresql://"):
    database_url = database_url.replace("postgresql://", "postgresql+psycopg2://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = database_url

# Configuraciones adicionales
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Inicializar SQLAlchemy
db = SQLAlchemy(app)

# --- MODELO DE LA BASE DE DATOS ---
class Licencia(db.Model):
    # ... (El resto del modelo es el mismo)
    id = db.Column(db.Integer, primary_key=True)
    codigo_licencia = db.Column(db.String(36), unique=True, nullable=False) # UUID
    hwid_activacion = db.Column(db.String(100), nullable=True, default=None) # Hardware ID
    fecha_activacion = db.Column(db.DateTime, nullable=True, default=None)
    token_sesion = db.Column(db.String(32), unique=True, nullable=True, default=None)
    fecha_expiracion = db.Column(db.DateTime, nullable=True, default=None)

    def __repr__(self):
        return f'<Licencia {self.codigo_licencia}>'

# --- RUTAS DE LA API ---

# 1. RUTA DE SALUD (HEALTH CHECK / INDEX)
@app.route('/', methods=['GET'])
def index():
    """Ruta para verificar que la API está viva y funcionando."""
    try:
        with app.app_context():
            db.create_all() # Intenta crear las tablas, prueba la conexión
        return jsonify({
            "status": "API Activa",
            "message": "Conexión DB OK. Usa POST para las rutas de /admin y /api.",
            "endpoints_disponibles": {
                "/admin/generar_claves/<cantidad>": "POST (para crear licencias nuevas)",
                "/api/activar": "POST (para activar o revalidar una licencia)"
            }
        }), 200
    except Exception as e:
        # Si falló la conexión o db.create_all()
        return jsonify({
            "status": "API Activa, pero DB Falló",
            "error": str(e)
        }), 500

# 2. RUTA DE ADMINISTRACIÓN (GENERAR CLAVES)
@app.route('/admin/generar_claves/<int:cantidad>', methods=['POST'])
def generar_claves(cantidad):
    """Genera un número específico de licencias únicas y las guarda en la DB."""
    if cantidad <= 0 or cantidad > 100:
        return jsonify({"success": False, "mensaje": "Cantidad inválida (1-100)."}), 400

    licencias_generadas = []
    try:
        for _ in range(cantidad):
            codigo = str(uuid4())
            nueva_licencia = Licencia(codigo_licencia=codigo)
            db.session.add(nueva_licencia)

        db.session.commit()
        return jsonify({
            "success": True,
            "mensaje": f"Se generaron y guardaron {cantidad} licencias únicas."
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "mensaje": f"Error al generar claves: {str(e)}"}), 500

# 3. RUTA DE ACTIVACIÓN DE LICENCIAS
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
    # (Comprobamos esto primero)
    if licencia.hwid_activacion is None:
        licencia.hwid_activacion = hwid_cliente
        licencia.fecha_activacion = datetime.utcnow()
        nuevo_token = uuid4().hex[:32]
        licencia.token_sesion = nuevo_token
        
        # ¡AQUÍ SE GRABA LA EXPIRACIÓN FINAL UNA ÚNICA VEZ!
        licencia.fecha_expiracion = datetime.utcnow() + timedelta(days=365) 
        
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
    
    # Si llegamos aquí, el HWID coincide.
    # Ahora SÍ comprobamos la expiración final.
    
    # CASO 3: LICENCIA EXPIRADA (¡NUEVO!)
    if datetime.utcnow() > licencia.fecha_expiracion:
        return jsonify({
            "success": False,
            "mensaje": f"Tu licencia expiró el {licencia.fecha_expiracion.strftime('%Y-%m-%d')}. Adquiere una nueva."
        }), 403 # 403 Forbidden
            
    # CASO 4: LICENCIA VÁLIDA (Revalidación / Heartbeat)
    # (HWID coincide Y NO está expirada)
    nuevo_token = uuid4().hex[:32]
    licencia.token_sesion = nuevo_token
    
    # ¡¡MUY IMPORTANTE: YA NO TOCAMOS la fecha_expiracion!!
    
    db.session.commit()
    return jsonify({
        "success": True,
        "mensaje": "Licencia revalidada exitosamente.",
        "token": nuevo_token,
        "expiracion": licencia.fecha_expiracion.isoformat() # Devolvemos la fecha final
    }), 200


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
