import os
import random
import threading
import time
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import requests
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)
app.secret_key = "mecatroapuestas_secret_key"

ESP32_IP = "http://192.168.18.100"

# Mapa exacto de colores del tablero (0-23)
NUMEROS_ROJOS = {1, 3, 5, 6, 8, 10, 13, 15, 17, 18, 20, 22}

SALA_ESTADO = {
    "sistema_activo": False,
    "activa": False,
    "tiempo_restante": 0,
    "apuestas": [],
    "ultimo_resultado": None,
    "sonido": True,
    "luces": True,
    "numero_ronda": 0
}

def get_db_connection():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        database_url = "postgresql://postgres:apuestafijas2A@db.voyfoiqionnheakpoint.supabase.co:6543/postgres"
    return psycopg2.connect(database_url, cursor_factory=RealDictCursor)

def init_db():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS usuarios (
                id VARCHAR(50) PRIMARY KEY,
                username VARCHAR(100) UNIQUE,
                password VARCHAR(100),
                saldo REAL,
                fecha_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS historial (
                id SERIAL PRIMARY KEY,
                usuario_id VARCHAR(50),
                username VARCHAR(100),
                monto REAL,
                numero_elegido INTEGER,
                color_elegido VARCHAR(50),
                numero_ganador INTEGER,
                color_ganador VARCHAR(50),
                resultado VARCHAR(50),
                monto_ganado REAL DEFAULT 0.0,
                fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute("SELECT * FROM usuarios WHERE username = 'admin'")
        if not cursor.fetchone():
            cursor.execute(
                "INSERT INTO usuarios (id, username, password, saldo) VALUES (%s, %s, %s, %s)",
                ('M000', 'admin', 'admin123', 5000.0)
            )
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"[DB Init Error] {e}")

init_db()

def es_admin_autorizado(username):
    return username in ['Capi admin', 'El diavlo', 'admin']

def generar_siguiente_id():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM usuarios")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    if not rows:
        return "M001"

    max_num = 0
    for r in rows:
        uid = r['id']
        if uid.startswith('M') and uid[1:].isdigit():
            num = int(uid[1:])
            if num > max_num:
                max_num = num
    return f"M{max_num + 1:03d}"

def obtener_color(numero):
    if numero == 0:
        return "Verde"
    return "Rojo" if numero in NUMEROS_ROJOS else "Negro"

def enviar_a_esp32_async(numero_ganador, sonido=1, luces=1):
    def tarea():
        try:
            requests.get(f"{ESP32_IP}/girar?ganador={numero_ganador}&sonido={sonido}&luces={luces}", timeout=2)
        except Exception as e:
            print(f"[ESP32 Comms Warning] {e}")
    threading.Thread(target=tarea, daemon=True).start()

def obtener_resultado_ruleta():
    return random.randint(0, 23)

# --- RUTAS PRINCIPALES ---

@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login_view'))

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM usuarios WHERE id = %s', (session['user_id'],))
    user = cursor.fetchone()
    cursor.execute('SELECT * FROM historial WHERE usuario_id = %s ORDER BY id DESC LIMIT 15', (session['user_id'],))
    historial = cursor.fetchall()
    cursor.close()
    conn.close()

    if not user:
        session.clear()
        return redirect(url_for('login_view'))

    return render_template(
        'index.html',
        usuario=user['username'],
        user_id=user['id'],
        saldo=user['saldo'],
        historial=historial,
        es_admin=es_admin_autorizado(session.get('username'))
    )

@app.route('/login', methods=['GET', 'POST'])
def login_view():
    if request.method == 'GET':
        return render_template('login.html')

    data = request.json
    username = data.get('username')
    password = data.get('password')
    accion = data.get('action', 'login')

    conn = get_db_connection()
    cursor = conn.cursor()

    if accion == 'register':
        cursor.execute('SELECT * FROM usuarios WHERE username = %s', (username,))
        if cursor.fetchone():
            cursor.close()
            conn.close()
            return jsonify({'status': 'error', 'message': 'El nombre de usuario ya existe'}), 400

        nuevo_id = generar_siguiente_id()
        cursor.execute(
            "INSERT INTO usuarios (id, username, password, saldo) VALUES (%s, %s, %s, %s)",
            (nuevo_id, username, password, 200.0)
        )
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({'status': 'ok', 'user_id': nuevo_id})
    else:
        cursor.execute('SELECT * FROM usuarios WHERE username = %s AND password = %s', (username, password))
        user = cursor.fetchone()
        cursor.close()
        conn.close()

        if user:
            session.clear()
            session['user_id'] = user['id']
            session['username'] = user['username']
            return jsonify({'status': 'ok', 'user_id': user['id']})
        else:
            return jsonify({'status': 'error', 'message': 'Credenciales incorrectas'}), 400

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login_view'))

@app.route('/cambiar_password', methods=['POST'])
def cambiar_password():
    if 'user_id' not in session:
        return jsonify({'status': 'error', 'message': 'No autorizado'}), 401
    
    data = request.json
    actual = data.get('actual')
    nueva = data.get('nueva')

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT password FROM usuarios WHERE id = %s', (session['user_id'],))
    user = cursor.fetchone()

    if not user or user['password'] != actual:
        cursor.close()
        conn.close()
        return jsonify({'status': 'error', 'message': 'La contraseña actual es incorrecta'}), 400

    cursor.execute('UPDATE usuarios SET password = %s WHERE id = %s', (nueva, session['user_id']))
    conn.commit()
    cursor.close()
    conn.close()

    return jsonify({'status': 'ok', 'message': 'Contraseña actualizada correctamente'})

# --- PANEL DE ADMINISTRACIÓN Y CONTROL ---

@app.route('/admin', methods=['GET', 'POST'])
def admin_panel():
    if not es_admin_autorizado(session.get('username')):
        return "Acceso denegado.", 403

    conn = get_db_connection()
    cursor = conn.cursor()

    if request.method == 'POST':
        usuario_id = request.form.get('usuario_id')
        nuevo_saldo = float(request.form.get('nuevo_saldo', 0))
        cursor.execute('UPDATE usuarios SET saldo = %s WHERE id = %s', (nuevo_saldo, usuario_id))
        conn.commit()

    cursor.execute('SELECT * FROM usuarios ORDER BY id')
    usuarios = cursor.fetchall()
    cursor.close()
    conn.close()

    return render_template(
        'admin.html', 
        usuarios=usuarios,
        sonido=SALA_ESTADO["sonido"], 
        luces=SALA_ESTADO["luces"],
        sistema_activo=SALA_ESTADO["sistema_activo"]
    )

def bucle_ciclo_continuo():
    """Ejecución continua e ininterrumpida de rondas"""
    while SALA_ESTADO["sistema_activo"]:
        try:
            SALA_ESTADO["activa"] = True
            SALA_ESTADO["tiempo_restante"] = 20
            SALA_ESTADO["apuestas"] = []
            SALA_ESTADO["numero_ronda"] += 1
            ronda_actual = SALA_ESTADO["numero_ronda"]

            # 1. Tiempo de apuestas
            while SALA_ESTADO["tiempo_restante"] > 0 and SALA_ESTADO["sistema_activo"]:
                time.sleep(1)
                SALA_ESTADO["tiempo_restante"] -= 1

            if not SALA_ESTADO["sistema_activo"]:
                break

            # 2. Cierre de apuestas y giro
            SALA_ESTADO["activa"] = False
            numero_ganador = obtener_resultado_ruleta()
            color_ganador = obtener_color(numero_ganador)

            conn = get_db_connection()
            cursor = conn.cursor()

            ganadores_list = []
            total_repartido = 0.0

            for ap in SALA_ESTADO["apuestas"]:
                gano = (ap['numero'] == numero_ganador)
                monto_ganado = (ap['monto'] * 24.0) if gano else 0.0

                cursor.execute('SELECT saldo FROM usuarios WHERE id = %s', (ap['user_id'],))
                user = cursor.fetchone()

                if user:
                    nuevo_saldo = user['saldo'] + monto_ganado
                    resultado_str = "GANASTE" if gano else "PERDISTE"

                    cursor.execute('UPDATE usuarios SET saldo = %s WHERE id = %s', (nuevo_saldo, ap['user_id']))
                    cursor.execute('''
                        INSERT INTO historial (usuario_id, username, monto, numero_elegido, color_elegido, numero_ganador, color_ganador, resultado, monto_ganado)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ''', (ap['user_id'], ap['username'], ap['monto'], ap['numero'], ap['color'], numero_ganador, color_ganador, resultado_str, monto_ganado))

                    if gano:
                        ganadores_list.append({
                            'username': ap['username'],
                            'monto_ganado': monto_ganado
                        })
                        total_repartido += monto_ganado

            conn.commit()
            cursor.close()
            conn.close()

            SALA_ESTADO["ultimo_resultado"] = {
                "numero_ganador": numero_ganador,
                "color_ganador": color_ganador,
                "ganadores": ganadores_list,
                "total_repartido": total_repartido,
                "numero_ronda": ronda_actual
            }

            enviar_a_esp32_async(numero_ganador, 1 if SALA_ESTADO["sonido"] else 0, 1 if SALA_ESTADO["luces"] else 0)

            # Pausa para mostrar animación del resultado
            tiempo_pausa = 7
            while tiempo_pausa > 0 and SALA_ESTADO["sistema_activo"]:
                time.sleep(1)
                tiempo_pausa -= 1

        except Exception as e:
            print(f"[Error en Bucle de Sala] {e}")
            time.sleep(2)

# --- NUEVOS ENDPOINTS API PARA EL FRONTEND ADMIN Y JUEGO ---

@app.route('/api/sala/estado', methods=['GET', 'POST'])
def api_sala_estado():
    """Endpoint flexible para consultar o actualizar estado desde JS (Fetch)"""
    if request.method == 'POST':
        if not es_admin_autorizado(session.get('username')):
            return jsonify({'status': 'error', 'message': 'No autorizado'}), 403

        data = request.json or {}
        # Acepta tanto "estado": "ABIERTA" como "abierta": True
        abrir = data.get('abierta')
        if abrir is None and 'estado' in data:
            abrir = (data['estado'] == 'ABIERTA')

        if abrir:
            if not SALA_ESTADO["sistema_activo"]:
                SALA_ESTADO["sistema_activo"] = True
                threading.Thread(target=bucle_ciclo_continuo, daemon=True).start()
        else:
            SALA_ESTADO["sistema_activo"] = False
            SALA_ESTADO["activa"] = False
            SALA_ESTADO["tiempo_restante"] = 0

        return jsonify({'status': 'ok', 'abierta': SALA_ESTADO["sistema_activo"]})

    return jsonify({'abierta': SALA_ESTADO["sistema_activo"], 'activa': SALA_ESTADO["activa"]})

@app.route('/admin/abrir_sala', methods=['POST'])
def abrir_sala_admin():
    if not es_admin_autorizado(session.get('username')):
        return jsonify({'status': 'error', 'message': 'No autorizado'}), 403

    if SALA_ESTADO["sistema_activo"]:
        return jsonify({'status': 'error', 'message': 'La sala ya está en funcionamiento'}), 400

    SALA_ESTADO["sistema_activo"] = True
    threading.Thread(target=bucle_ciclo_continuo, daemon=True).start()
    return jsonify({'status': 'ok', 'message': 'Sala abierta correctamente'})

@app.route('/admin/cerrar_sala', methods=['POST'])
def cerrar_sala_admin():
    if not es_admin_autorizado(session.get('username')):
        return jsonify({'status': 'error', 'message': 'No autorizado'}), 403

    SALA_ESTADO["sistema_activo"] = False
    SALA_ESTADO["activa"] = False
    SALA_ESTADO["tiempo_restante"] = 0
    return jsonify({'status': 'ok', 'message': 'Sala cerrada correctamente'})

@app.route('/api/esp32/efectos', methods=['POST'])
@app.route('/admin/efectos', methods=['POST'])
def configurar_efectos():
    if not es_admin_autorizado(session.get('username')):
        return jsonify({'status': 'error', 'message': 'No autorizado'}), 403

    data = request.json or {}
    if 'tipo' in data and 'estado' in data:
        if data['tipo'] == 'Sonido':
            SALA_ESTADO["sonido"] = bool(data['estado'])
        elif data['tipo'] == 'Luces LED':
            SALA_ESTADO["luces"] = bool(data['estado'])
    
    if 'sonido' in data:
        SALA_ESTADO["sonido"] = bool(data["sonido"])
    if 'luces' in data:
        SALA_ESTADO["luces"] = bool(data["luces"])

    return jsonify({'status': 'ok', 'sonido': SALA_ESTADO["sonido"], 'luces': SALA_ESTADO["luces"]})

@app.route('/api/usuarios/saldo', methods=['POST'])
def api_actualizar_saldo():
    if not es_admin_autorizado(session.get('username')):
        return jsonify({'status': 'error', 'message': 'No autorizado'}), 403

    data = request.json or {}
    usuario_id = data.get('id')
    nuevo_saldo = float(data.get('saldo', 0))

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('UPDATE usuarios SET saldo = %s WHERE id = %s', (nuevo_saldo, usuario_id))
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({'status': 'ok', 'saldo': nuevo_saldo})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/estado_sala', methods=['GET'])
def estado_sala():
    saldo_actual = 0.0
    if 'user_id' in session:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT saldo FROM usuarios WHERE id = %s', (session['user_id'],))
            row = cursor.fetchone()
            if row:
                saldo_actual = row['saldo']
            cursor.close()
            conn.close()
        except Exception:
            pass

    return jsonify({
        "sistema_activo": SALA_ESTADO["sistema_activo"],
        "activa": SALA_ESTADO["activa"],
        "tiempo_restante": SALA_ESTADO["tiempo_restante"],
        "apuestas": SALA_ESTADO["apuestas"],
        "ultimo_resultado": SALA_ESTADO["ultimo_resultado"],
        "sonido": SALA_ESTADO["sonido"],
        "luces": SALA_ESTADO["luces"],
        "numero_ronda": SALA_ESTADO["numero_ronda"],
        "saldo_usuario": saldo_actual
    })

@app.route('/apostar_sala', methods=['POST'])
def apostar_sala():
    if 'user_id' not in session:
        return jsonify({'status': 'error', 'message': 'Inicia sesión para apostar'}), 401

    if not SALA_ESTADO["activa"]:
        return jsonify({'status': 'error', 'message': 'Las apuestas están cerradas'}), 400

    data = request.json
    monto = float(data.get('monto', 0))
    numero = int(data.get('numero', 0))
    color = data.get('color')

    if not (0 <= numero <= 23) or monto <= 0:
        return jsonify({'status': 'error', 'message': 'Apuesta no válida'}), 400

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM usuarios WHERE id = %s', (session['user_id'],))
    user = cursor.fetchone()

    if not user or monto > user['saldo']:
        cursor.close()
        conn.close()
        return jsonify({'status': 'error', 'message': 'Saldo insuficiente'}), 400

    for ap in SALA_ESTADO["apuestas"]:
        if ap['user_id'] == session['user_id']:
            cursor.close()
            conn.close()
            return jsonify({'status': 'error', 'message': 'Ya apostaste en esta ronda'}), 400

    nuevo_saldo = user['saldo'] - monto
    cursor.execute('UPDATE usuarios SET saldo = %s WHERE id = %s', (nuevo_saldo, session['user_id']))
    conn.commit()
    cursor.close()
    conn.close()

    SALA_ESTADO["apuestas"].append({
        'user_id': session['user_id'],
        'username': session['username'],
        'monto': monto,
        'numero': numero,
        'color': color
    })
    return jsonify({'status': 'ok', 'nuevo_saldo': nuevo_saldo})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)