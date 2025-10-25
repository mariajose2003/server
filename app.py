# app.py
import os
import uuid
import secrets
from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy

# --- Configuración de la Aplicación ---
app = Flask(__name__)

# La URL de la DB la obtendrá de las variables de entorno de Railway
# app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
# app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# --- Modelo de la Base de Datos ---
class Licencia(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    codigo_licencia = db.Column(db.String(36), unique=True, nullable=False)
    esta_activada = db.Column(db.Boolean, default=False)
    hwid_vinculado = db.Column(db.String(100), nullable=True)
    token_local = db.Column(db.String(64), nullable=True)

# --- Funciones de Utilidad ---
def generar_codigo_licencia():
    """Genera una clave única (UUID) para vender."""
    # Puedes modificar esto para tener el formato XXXXX-XXXXX-... si lo deseas
    return str(uuid.uuid4()).replace('-', '').upper()

def generar_token_activacion():
    """Genera el token de verificación seguro para guardar en el config.json del cliente."""
    return secrets.token_hex(32)

# --- Endpoint de Activación (Lo llama el .exe del cliente) ---
@app.route('/api/activar', methods=['POST'])
def activar_licencia():
    data = request.get_json()
    codigo_recibido = data.get('codigo')
    hwid_cliente = data.get('hwid')

    if not codigo_recibido or not hwid_cliente:
        return jsonify({"success": False, "mensaje": "Datos incompletos."}), 400

    # Buscar la licencia
    licencia = Licencia.query.filter_by(codigo_licencia=codigo_recibido).first()

    if not licencia:
        return jsonify({"success": False, "mensaje": "Código de activación no válido."}), 404

    if licencia.esta_activada:
        # A. Licencia ya activada: Verificar HWID
        if licencia.hwid_vinculado == hwid_cliente:
            # Misma máquina: revalidar token
            return jsonify({
                "success": True,
                "mensaje": "Licencia revalidada.",
                "token": licencia.token_local
            }), 200
        else:
            # Intento de copia en otra máquina
            return jsonify({"success": False, "mensaje": "Licencia ya en uso en otra máquina."}), 403
    else:
        # B. Primera activación: Registrar HWID y generar token
        nuevo_token = generar_token_activacion()
        
        licencia.esta_activada = True
        licencia.hwid_vinculado = hwid_cliente
        licencia.token_local = nuevo_token
        
        db.session.commit()
        
        return jsonify({
            "success": True,
            "mensaje": "Activación exitosa.",
            "token": nuevo_token
        }), 200

# --- Endpoint Administrativo para GENERAR Claves Iniciales ---
@app.route('/admin/generar_claves/<int:cantidad>', methods=['POST'])
def generar_claves(cantidad):
    """Ejecutar solo UNA VEZ para pre-cargar claves en la DB."""
    if os.environ.get('RAILWAY_ENVIRONMENT') != 'production':
        return jsonify({"success": False, "mensaje": "Acceso denegado."}), 403 # Solo para seguridad
        
    try:
        for _ in range(cantidad):
            codigo = generar_codigo_licencia()
            nueva_licencia = Licencia(codigo_licencia=codigo)
            db.session.add(nueva_licencia)
        db.session.commit()
        return jsonify({"success": True, "mensaje": f"Se generaron y guardaron {cantidad} licencias únicas."}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "mensaje": f"Error: {str(e)}"}), 500


# --- Inicialización y Ejecución ---
if __name__ == '__main__':
    with app.app_context():
        # Crea las tablas de la DB si no existen
        db.create_all()
    # En local (para pruebas)
    app.run(debug=True)