import os
from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from uuid import uuid4
from datetime import datetime, timedelta

# Inicialización de la aplicación Flask
app = Flask(__name__)

# --- CONFIGURACIÓN DE LA BASE DE DATOS ---
# 1. Intentar cargar la URL de conexión de las variables de entorno (Railway, Render, etc.).
#    Se busca 'DATABASE_URL', que es el estándar de Railway.
database_url = os.environ.get('DATABASE_URL')

# Si la variable de entorno no se encuentra, usar una base de datos local (SQLite)
if not database_url:
    print("WARNING: Using local SQLite database (not recommended for production).")
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///licencias.db'
else:
    # 2. Reemplazar el prefijo 'postgresql://' con 'postgresql+psycopg2://'
    #    Esto resuelve el error 'Could not parse SQLAlchemy URL'.
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
    """Modelo para almacenar las licencias y su estado de activación."""
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
    # Creamos las tablas solo si estamos en la primera petición y no existen.
    try:
        with app.app_context():
            db.create_all()
        return jsonify({
            "status": "API Activa",
            "message": "El servidor está funcionando y la conexión a la base de datos es exitosa. Usa POST para las otras rutas.",
            "endpoints_disponibles": {
                "/admin/generar_claves/<cantidad>": "POST (para crear licencias nuevas)",
                "/api/activar": "POST (para activar o revalidar una licencia)"
            }
        }), 200
    except Exception as e:
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
            # Generar un UUID único como código de licencia
            codigo = str(uuid4())
            nueva_licencia = Licencia(codigo_licencia=codigo)
            db.session.add(nueva_licencia)
            licencias_generadas.append(codigo)

        db.session.commit()
        return jsonify({
            "success": True,
            "mensaje": f"Se generaron y guardaron {cantidad} licencias únicas.",
            "claves": licencias_generadas
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "mensaje": f"Error al generar claves: {str(e)}"}), 500

# 3. RUTA DE ACTIVACIÓN DE LICENCIAS
@app.route('/api/activar', methods=['POST'])
def activar_licencia():
    """Activa una licencia por primera vez o revalida una existente."""
    data = request.get_json()
    codigo_licencia = data.get('codigo')
    hwid_cliente = data.get('hwid')

    if not codigo_licencia or not hwid_cliente:
        return jsonify({"success": False, "mensaje": "Faltan datos de código o HWID."}), 400

    licencia = Licencia.query.filter_by(codigo_licencia=codigo_licencia).first()

    if not licencia:
        return jsonify({"success": False, "mensaje": "Licencia inválida o no encontrada."}), 404

    # CASO 1: LICENCIA YA ACTIVADA EN ESTE HWID (Revalidación)
    if licencia.hwid_activacion == hwid_cliente:
        # Generar un nuevo token de sesión que expira en 7 días
        nuevo_token = uuid4().hex[:32]
        licencia.token_sesion = nuevo_token
        licencia.fecha_expiracion = datetime.utcnow() + timedelta(days=7)
        db.session.commit()
        return jsonify({
            "success": True,
            "mensaje": "Licencia revalidada exitosamente.",
            "token": nuevo_token,
            "expiracion": licencia.fecha_expiracion.isoformat()
        }), 200

    # CASO 2: LICENCIA ACTIVADA EN OTRO HWID (Bloqueo)
    elif licencia.hwid_activacion is not None and licencia.hwid_activacion != hwid_cliente:
        return jsonify({
            "success": False,
            "mensaje": "Licencia ya está vinculada a otro dispositivo."
        }), 403 # 403 Forbidden

    # CASO 3: LICENCIA VIRGEN (Primera Activación)
    else:
        # Vinculación de la licencia al HWID
        licencia.hwid_activacion = hwid_cliente
        licencia.fecha_activacion = datetime.utcnow()
        # Generar token de sesión
        nuevo_token = uuid4().hex[:32]
        licencia.token_sesion = nuevo_token
        licencia.fecha_expiracion = datetime.utcnow() + timedelta(days=7)
        db.session.commit()
        return jsonify({
            "success": True,
            "mensaje": "Licencia activada y vinculada exitosamente.",
            "token": nuevo_token,
            "expiracion": licencia.fecha_expiracion.isoformat()
        }), 201 # 201 Created

# Esto se ejecuta en el entorno local (desarrollo)
if __name__ == '__main__':
    # Crear tablas si no existen al iniciar la app localmente
    with app.app_context():
        db.create_all()
    app.run(debug=True)
