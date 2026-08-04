import os
import json
import random
import threading
import time
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import requests
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)
app.secret_key = "mecatroapuestas_secret_key"

ESP32_IP = "http://192.168.18.100"

# --- DISTRIBUCIÓN DE COLORES CORREGIDA (según el tablero de referencia) ---
# 0 = Verde | Pares (2,4,6...22) = Rojo | Impares (1,3,5...23) = Negro
NUMEROS_ROJOS = {2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22}

DURACION_APUESTAS = 20   # segundos para apostar
DURACION_PAUSA = 5       # segundos que dura el popup de resultado antes de la siguiente ronda

# --- CONFIGURACIÓN DE BASE DE DATOS ---
# IMPORTANTE: el estado "en vivo" de la sala (ronda actual, cuenta regresiva, apuestas
# de la ronda, último resultado) vive en la BASE DE DATOS, no en memoria del proceso.
# Esto es clave: si Render corre la app con más de un proceso/worker, cada uno tendría
# su propia copia en memoria y quedarían desincronizados entre sí (rondas saltando,
# el popup de resultado sin aparecer, etc). Guardándolo en la BD, todos los procesos
# ven siempre el mismo estado real.

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

        # Estado "en vivo" de la sala - una sola fila (id = 1), fuente única de verdad
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sala_live (
                id INTEGER PRIMARY KEY,
                sistema_activo BOOLEAN DEFAULT FALSE,
                activa BOOLEAN DEFAULT FALSE,
                hilo_activo BOOLEAN DEFAULT FALSE,
                numero_ronda INTEGER DEFAULT 0,
                fase_termina_en TIMESTAMP,
                sonido BOOLEAN DEFAULT TRUE,
                luces BOOLEAN DEFAULT TRUE,
                ultima_ronda_resuelta INTEGER DEFAULT 0,
                ultimo_numero_ganador INTEGER,
                ultimo_color_ganador VARCHAR(50),
                ultimo_total_repartido REAL DEFAULT 0,
                ultimo_ganadores TEXT
            )
        ''')

        # Apuestas de la ronda en curso (se van insertando y se limpian entre rondas)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS apuestas_ronda (
                id SERIAL PRIMARY KEY,
                numero_ronda INTEGER,
                usuario_id VARCHAR(50),
                username VARCHAR(100),
                monto REAL,
                numero INTEGER,
                color VARCHAR(50)
            )
        ''')

        cursor.execute("SELECT * FROM usuarios WHERE username = 'admin'")
        if not cursor.fetchone():
            cursor.execute(
                "INSERT INTO usuarios (id, username, password, saldo) VALUES (%s, %s, %s, %s)",
                ('M000', 'admin', 'admin123', 5000.0)
            )

        # Agregar usuarios de prueba si no existen
        usuarios_prueba = [
            ('M001', 'Capi admin', 'admin123', 200.0),
            ('M002', 'DejameApostar', 'admin123', 200.0)
        ]
        for uid, uname, pwd, saldo in usuarios_prueba:
            cursor.execute("SELECT * FROM usuarios WHERE id = %s", (uid,))
            if not cursor.fetchone():
                cursor.execute(
                    "INSERT INTO usuarios (id, username, password, saldo) VALUES (%s, %s, %s, %s)",
                    (uid, uname, pwd, saldo)
                )

        # Crear la fila única de estado si no existe
        cursor.execute("SELECT id FROM sala_live WHERE id = 1")
        if not cursor.fetchone():
            cursor.execute("INSERT INTO sala_live (id) VALUES (1)")

        # Al arrancar el proceso, siempre reseteamos las banderas "en vivo".
        # Si el proceso se reinició, ningún hilo real sigue corriendo, así que
        # dejar "hilo_activo" o "sistema_activo" en TRUE dejaría la sala bloqueada
        # para siempre (nadie podría volver a abrirla).
        cursor.execute('''
            UPDATE sala_live SET sistema_activo = FALSE, activa = FALSE, hilo_activo = FALSE WHERE id = 1
        ''')

        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"[DB Init Error] {e}")

init_db()

# --- FUNCIONES AUXILIARES ---

def es_admin_autorizado(username):
    # Solo estos dos usuarios pueden ver el panel de administración
    return username in ['admin', 'Capi admin']

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

# --- LOGICA DE VICTORIA (60% probabilidad de ganar para jugadores) ---
def obtener_resultado_ruleta(apuestas_actuales):
    if apuestas_actuales and random.random() <= 0.60:
        apuesta_suertuda = random.choice(apuestas_actuales)
        print(f"[MODO SUERTE ACTIVADO] Forzando el número {apuesta_suertuda['numero']} para ayudar a los jugadores.")
        return apuesta_suertuda['numero']

    return random.randint(0, 23)

def sala_sigue_activa(cursor):
    cursor.execute("SELECT sistema_activo FROM sala_live WHERE id = 1")
    row = cursor.fetchone()
    return bool(row and row['sistema_activo'])

# --- BUCLE DE JUEGO PRINCIPAL (corre solo en el proceso "líder") ---

def bucle_ciclo_continuo():
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        while True:
            if not sala_sigue_activa(cursor):
                break

            # --- Iniciar nueva ronda de apuestas ---
            cursor.execute("SELECT numero_ronda FROM sala_live WHERE id = 1")
            ronda_actual = cursor.fetchone()['numero_ronda'] + 1
            fin_apuestas = datetime.utcnow() + timedelta(seconds=DURACION_APUESTAS)

            cursor.execute('''
                UPDATE sala_live
                SET activa = TRUE, numero_ronda = %s, fase_termina_en = %s
                WHERE id = 1
            ''', (ronda_actual, fin_apuestas))
            conn.commit()

            # Esperar la ventana de apuestas, comprobando cada segundo si la sala sigue abierta
            for _ in range(DURACION_APUESTAS):
                time.sleep(1)
                if not sala_sigue_activa(cursor):
                    break

            if not sala_sigue_activa(cursor):
                break

            # --- Cerrar apuestas y girar ---
            cursor.execute("UPDATE sala_live SET activa = FALSE WHERE id = 1")
            conn.commit()

            cursor.execute("SELECT * FROM apuestas_ronda WHERE numero_ronda = %s", (ronda_actual,))
            apuestas_actuales = cursor.fetchall()

            numero_ganador = obtener_resultado_ruleta(apuestas_actuales)
            color_ganador = obtener_color(numero_ganador)

            ganadores_list = []
            total_repartido = 0.0

            for ap in apuestas_actuales:
                gano = (ap['numero'] == numero_ganador)
                monto_ganado = (ap['monto'] * 24.0) if gano else 0.0

                cursor.execute('SELECT saldo FROM usuarios WHERE id = %s', (ap['usuario_id'],))
                user = cursor.fetchone()

                if user:
                    nuevo_saldo = user['saldo'] + monto_ganado
                    resultado_str = "GANASTE" if gano else "PERDISTE"

                    cursor.execute('UPDATE usuarios SET saldo = %s WHERE id = %s', (nuevo_saldo, ap['usuario_id']))
                    cursor.execute('''
                        INSERT INTO historial (usuario_id, username, monto, numero_elegido, color_elegido, numero_ganador, color_ganador, resultado, monto_ganado)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ''', (ap['usuario_id'], ap['username'], ap['monto'], ap['numero'], ap['color'], numero_ganador, color_ganador, resultado_str, monto_ganado))

                    if gano:
                        ganadores_list.append({'username': ap['username'], 'monto_ganado': monto_ganado})
                        total_repartido += monto_ganado

            cursor.execute("DELETE FROM apuestas_ronda WHERE numero_ronda = %s", (ronda_actual,))

            pausa_fin = datetime.utcnow() + timedelta(seconds=DURACION_PAUSA)
            cursor.execute('''
                UPDATE sala_live SET
                    ultima_ronda_resuelta = %s,
                    ultimo_numero_ganador = %s,
                    ultimo_color_ganador = %s,
                    ultimo_total_repartido = %s,
                    ultimo_ganadores = %s,
                    fase_termina_en = %s
                WHERE id = 1
            ''', (ronda_actual, numero_ganador, color_ganador, total_repartido, json.dumps(ganadores_list), pausa_fin))
            conn.commit()

            cursor.execute("SELECT sonido, luces FROM sala_live WHERE id = 1")
            efectos = cursor.fetchone()
            enviar_a_esp32_async(numero_ganador, 1 if efectos['sonido'] else 0, 1 if efectos['luces'] else 0)

            # --- Pausa mostrando el resultado ---
            for _ in range(DURACION_PAUSA):
                time.sleep(1)
                if not sala_sigue_activa(cursor):
                    break

    except Exception as e:
        print(f"[Error en Bucle de Sala] {e}")
    finally:
        try:
            cursor.execute("UPDATE sala_live SET hilo_activo = FALSE, activa = FALSE WHERE id = 1")
            conn.commit()
        except Exception:
            pass
        cursor.close()
        conn.close()

# --- RUTAS DE NAVEGACIÓN Y AUTENTICACIÓN ---

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

    # Historial de apuestas de TODOS los usuarios (monto, número/color apostado, color ganador, resultado)
    cursor.execute('SELECT * FROM historial ORDER BY id DESC LIMIT 50')
    historial = cursor.fetchall()

    cursor.execute('SELECT sonido, luces, sistema_activo FROM sala_live WHERE id = 1')
    sala = cursor.fetchone()

    cursor.close()
    conn.close()

    return render_template(
        'admin.html',
        usuarios=usuarios,
        historial=historial,
        sonido=sala['sonido'] if sala else True,
        luces=sala['luces'] if sala else True,
        sistema_activo=sala['sistema_activo'] if sala else False
    )

# --- ENDPOINTS API Y CONTROL DE SALA ---

@app.route('/api/sala/estado', methods=['GET', 'POST'])
def api_sala_estado():
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        if request.method == 'POST':
            if not es_admin_autorizado(session.get('username')):
                return jsonify({'status': 'error', 'message': 'No autorizado'}), 403

            data = request.json or {}
            abrir = data.get('abierta')

            if abrir is None and 'estado' in data:
                abrir = (data['estado'] == 'ABIERTA')

            if abrir:
                cursor.execute("UPDATE sala_live SET sistema_activo = TRUE WHERE id = 1")
                conn.commit()

                # Elección de líder ATÓMICA: solo el proceso que logre este UPDATE
                # arranca el hilo del juego. Evita hilos duplicados (misma causa
                # de que las rondas se saltaran y el popup no apareciera).
                cursor.execute('''
                    UPDATE sala_live SET hilo_activo = TRUE
                    WHERE id = 1 AND hilo_activo = FALSE
                    RETURNING id
                ''')
                soy_lider = cursor.fetchone() is not None
                conn.commit()

                if soy_lider:
                    threading.Thread(target=bucle_ciclo_continuo, daemon=True).start()
                    print("[SALA] Este proceso tomó el liderazgo del ciclo de ruleta.")
            else:
                cursor.execute("UPDATE sala_live SET sistema_activo = FALSE, activa = FALSE WHERE id = 1")
                conn.commit()
                print("[SALA] Sala cerrada.")

            cursor.execute("SELECT sistema_activo FROM sala_live WHERE id = 1")
            estado = cursor.fetchone()
            return jsonify({'status': 'ok', 'abierta': estado['sistema_activo'] if estado else False})

        cursor.execute("SELECT sistema_activo, activa FROM sala_live WHERE id = 1")
        row = cursor.fetchone()
        return jsonify({
            'abierta': row['sistema_activo'] if row else False,
            'activa': row['activa'] if row else False
        })
    finally:
        cursor.close()
        conn.close()

@app.route('/admin/cerrar_sala', methods=['POST'])
def cerrar_sala_admin():
    if not es_admin_autorizado(session.get('username')):
        return jsonify({'status': 'error', 'message': 'No autorizado'}), 403

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE sala_live SET sistema_activo = FALSE, activa = FALSE WHERE id = 1")
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({'status': 'ok', 'message': 'Sala cerrada correctamente'})

@app.route('/api/esp32/efectos', methods=['POST'])
@app.route('/admin/efectos', methods=['POST'])
def configurar_efectos():
    if not es_admin_autorizado(session.get('username')):
        return jsonify({'status': 'error', 'message': 'No autorizado'}), 403

    data = request.json or {}
    conn = get_db_connection()
    cursor = conn.cursor()

    if 'tipo' in data and 'estado' in data:
        if data['tipo'] == 'Sonido':
            cursor.execute("UPDATE sala_live SET sonido = %s WHERE id = 1", (bool(data['estado']),))
        elif data['tipo'] == 'Luces LED':
            cursor.execute("UPDATE sala_live SET luces = %s WHERE id = 1", (bool(data['estado']),))

    if 'sonido' in data:
        cursor.execute("UPDATE sala_live SET sonido = %s WHERE id = 1", (bool(data['sonido']),))
    if 'luces' in data:
        cursor.execute("UPDATE sala_live SET luces = %s WHERE id = 1", (bool(data['luces']),))

    conn.commit()
    cursor.execute("SELECT sonido, luces FROM sala_live WHERE id = 1")
    row = cursor.fetchone()
    cursor.close()
    conn.close()

    return jsonify({'status': 'ok', 'sonido': row['sonido'], 'luces': row['luces']})

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

@app.route('/cambiar_password', methods=['POST'])
def cambiar_password():
    if 'user_id' not in session:
        return jsonify({'status': 'error', 'message': 'No autorizado'}), 401

    data = request.json
    actual = data.get('actual')
    nueva = data.get('nueva')

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM usuarios WHERE id = %s AND password = %s', (session['user_id'], actual))
    user = cursor.fetchone()

    if not user:
        cursor.close()
        conn.close()
        return jsonify({'status': 'error', 'message': 'Contraseña actual incorrecta'}), 400

    cursor.execute('UPDATE usuarios SET password = %s WHERE id = %s', (nueva, session['user_id']))
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({'status': 'ok', 'message': 'Contraseña actualizada correctamente'})

@app.route('/estado_sala', methods=['GET'])
def estado_sala():
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT * FROM sala_live WHERE id = 1")
        sala = cursor.fetchone()

        saldo_actual = 0.0
        if 'user_id' in session:
            cursor.execute('SELECT saldo FROM usuarios WHERE id = %s', (session['user_id'],))
            row = cursor.fetchone()
            if row:
                saldo_actual = row['saldo']

        tiempo_restante = 0
        if sala and sala['activa'] and sala['fase_termina_en']:
            delta = (sala['fase_termina_en'] - datetime.utcnow()).total_seconds()
            tiempo_restante = max(0, round(delta))

        apuestas = []
        if sala:
            cursor.execute(
                "SELECT username, monto, numero, color FROM apuestas_ronda WHERE numero_ronda = %s",
                (sala['numero_ronda'],)
            )
            apuestas = cursor.fetchall()

        ultimo_resultado = None
        if sala and sala['ultima_ronda_resuelta']:
            ultimo_resultado = {
                "numero_ronda": sala['ultima_ronda_resuelta'],
                "numero_ganador": sala['ultimo_numero_ganador'],
                "color_ganador": sala['ultimo_color_ganador'],
                "ganadores": json.loads(sala['ultimo_ganadores']) if sala['ultimo_ganadores'] else [],
                "total_repartido": sala['ultimo_total_repartido'] or 0
            }

        return jsonify({
            "sistema_activo": sala['sistema_activo'] if sala else False,
            "activa": sala['activa'] if sala else False,
            "tiempo_restante": tiempo_restante,
            "apuestas": apuestas,
            "ultimo_resultado": ultimo_resultado,
            "sonido": sala['sonido'] if sala else True,
            "luces": sala['luces'] if sala else True,
            "numero_ronda": sala['numero_ronda'] if sala else 0,
            "saldo_usuario": saldo_actual
        })
    finally:
        cursor.close()
        conn.close()

@app.route('/apostar_sala', methods=['POST'])
def apostar_sala():
    if 'user_id' not in session:
        return jsonify({'status': 'error', 'message': 'Inicia sesión para apostar'}), 401

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT activa, numero_ronda FROM sala_live WHERE id = 1")
        sala = cursor.fetchone()

        if not sala or not sala['activa']:
            return jsonify({'status': 'error', 'message': 'Las apuestas están cerradas'}), 400

        ronda_actual = sala['numero_ronda']

        data = request.json
        monto = float(data.get('monto', 0))
        numero = int(data.get('numero', 0))
        color = data.get('color')

        if not (0 <= numero <= 23) or monto <= 0:
            return jsonify({'status': 'error', 'message': 'Apuesta no válida'}), 400

        cursor.execute('SELECT * FROM usuarios WHERE id = %s', (session['user_id'],))
        user = cursor.fetchone()

        if not user or monto > user['saldo']:
            return jsonify({'status': 'error', 'message': 'Saldo insuficiente'}), 400

        cursor.execute(
            "SELECT id FROM apuestas_ronda WHERE numero_ronda = %s AND usuario_id = %s",
            (ronda_actual, session['user_id'])
        )
        if cursor.fetchone():
            return jsonify({'status': 'error', 'message': 'Ya apostaste en esta ronda'}), 400

        nuevo_saldo = user['saldo'] - monto
        cursor.execute('UPDATE usuarios SET saldo = %s WHERE id = %s', (nuevo_saldo, session['user_id']))
        cursor.execute('''
            INSERT INTO apuestas_ronda (numero_ronda, usuario_id, username, monto, numero, color)
            VALUES (%s, %s, %s, %s, %s, %s)
        ''', (ronda_actual, session['user_id'], session['username'], monto, numero, color))
        conn.commit()

        return jsonify({'status': 'ok', 'nuevo_saldo': nuevo_saldo})
    finally:
        cursor.close()
        conn.close()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)