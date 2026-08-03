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

# IP actualizada del ESP32
ESP32_IP = "http://192.168.18.100"

# ==========================================================
# ESTADO GLOBAL DE LA SALA
# ==========================================================

SALA_ESTADO = {
    "sistema_activo": False,
    "activa": False,
    "tiempo_restante": 0,
    "apuestas": [],
    "ultimo_resultado": None,

    # Controles globales del administrador
    "sonido": True,
    "luces": True,

    # Identificador de ronda
    "numero_ronda": 0
}


# ==========================================================
# BASE DE DATOS
# ==========================================================

def get_db_connection():
    database_url = os.environ.get("DATABASE_URL")

    if not database_url:
        database_url = "postgresql://postgres:apuestafijas2A@db.voyfoiqionnheakpoint.supabase.co:6543/postgres"

    conn = psycopg2.connect(
        database_url,
        cursor_factory=RealDictCursor
    )

    return conn


def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS usuarios (
            id VARCHAR(50) PRIMARY KEY,
            username VARCHAR(100) UNIQUE,
            password VARCHAR(100),
            saldo REAL
        )
    ''')

    # Agregamos fecha de registro sin borrar ni modificar
    # las columnas que ya existen.
    cursor.execute('''
        ALTER TABLE usuarios
        ADD COLUMN IF NOT EXISTS fecha_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
            resultado VARCHAR(50)
        )
    ''')

    cursor.execute("SELECT * FROM usuarios WHERE username = 'admin'")

    if not cursor.fetchone():
        cursor.execute(
            """
            INSERT INTO usuarios
            (id, username, password, saldo, fecha_registro)
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
            """,
            ('M000', 'admin', 'admin123', 5000.0)
        )

    conn.commit()
    cursor.close()
    conn.close()


init_db()


# ==========================================================
# GENERAR ID
# ==========================================================

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


# ==========================================================
# COLORES DE LA RULETA
# 0 VERDE
# IMPARES NEGRO
# PARES ROJO
# ==========================================================

def obtener_color(numero):
    if numero == 0:
        return "Verde"

    if numero % 2 == 0:
        return "Rojo"

    return "Negro"


# ==========================================================
# COMUNICACIÓN ESP32
# ==========================================================

def enviar_a_esp32_async(numero_ganador, sonido=1, luces=1):

    def tarea():
        try:
            requests.get(
                f"{ESP32_IP}/girar"
                f"?ganador={numero_ganador}"
                f"&sonido={sonido}"
                f"&luces={luces}",
                timeout=3
            )

        except Exception as e:
            print(
                f"[ESP32 Comms] No se pudo conectar con el hardware: {e}"
            )

    threading.Thread(target=tarea, daemon=True).start()


# ==========================================================
# GENERADOR TEMPORAL DEL RESULTADO
#
# MODO PRUEBA:
# genera aleatoriamente 0 - 23.
#
# MÁS ADELANTE:
# aquí conectaremos el resultado real del ESP32.
# ==========================================================

def obtener_resultado_ruleta():
    numero = random.randint(0, 23)
    return numero


# ==========================================================
# PÁGINA PRINCIPAL
# ==========================================================

@app.route('/')
def index():

    if 'user_id' not in session:
        return redirect(url_for('login_view'))

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        'SELECT * FROM usuarios WHERE id = %s',
        (session['user_id'],)
    )

    user = cursor.fetchone()

    cursor.execute(
        '''
        SELECT *
        FROM historial
        WHERE usuario_id = %s
        ORDER BY id DESC
        LIMIT 15
        ''',
        (session['user_id'],)
    )

    historial = cursor.fetchall()

    cursor.close()
    conn.close()

    if not user:
        session.clear()
        return redirect(url_for('login_view'))

    admins_autorizados = [
        'Capi admin',
        'El diavlo',
        'admin'
    ]

    es_admin = session.get('username') in admins_autorizados

    return render_template(
        'index.html',
        usuario=user['username'],
        user_id=user['id'],
        saldo=user['saldo'],
        historial=historial,
        fecha_registro=user.get('fecha_registro'),
        es_admin=es_admin
    )


# ==========================================================
# LOGIN / REGISTRO
# ==========================================================

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

        cursor.execute(
            'SELECT * FROM usuarios WHERE username = %s',
            (username,)
        )

        user = cursor.fetchone()

        if user:
            cursor.close()
            conn.close()

            return jsonify({
                'status': 'error',
                'message': 'El nombre de usuario ya existe'
            }), 400

        nuevo_id = generar_siguiente_id()

        cursor.execute(
            '''
            INSERT INTO usuarios
            (id, username, password, saldo, fecha_registro)
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
            ''',
            (
                nuevo_id,
                username,
                password,
                200.0
            )
        )

        conn.commit()

        cursor.close()
        conn.close()

        return jsonify({
            'status': 'ok',
            'message': 'Usuario registrado con éxito',
            'user_id': nuevo_id
        })

    else:

        cursor.execute(
            '''
            SELECT *
            FROM usuarios
            WHERE username = %s
            AND password = %s
            ''',
            (username, password)
        )

        user = cursor.fetchone()

        cursor.close()
        conn.close()

    if user:

        session.clear()

        session['user_id'] = user['id']
        session['username'] = user['username']

        return jsonify({
            'status': 'ok',
            'user_id': user['id']
        })

    else:

        return jsonify({
            'status': 'error',
            'message': 'Usuario o contraseña incorrectos'
        }), 400


# ==========================================================
# LOGOUT
# ==========================================================

@app.route('/logout')
def logout():

    session.clear()

    return redirect(url_for('login_view'))


# ==========================================================
# PANEL ADMIN
# ==========================================================

@app.route('/admin', methods=['GET', 'POST'])
def admin_panel():

    admins_autorizados = [
        'Capi admin',
        'El diavlo',
        'admin'
    ]

    usuario_actual = session.get('username')

    if not usuario_actual or usuario_actual not in admins_autorizados:
        return "Acceso denegado. Solo administradores autorizados.", 403

    conn = get_db_connection()
    cursor = conn.cursor()

    if request.method == 'POST':

        usuario_id = request.form.get('usuario_id')
        nuevo_saldo = float(
            request.form.get('nuevo_saldo', 0)
        )

        cursor.execute(
            """
            UPDATE usuarios
            SET saldo = %s
            WHERE id = %s
            """,
            (
                nuevo_saldo,
                usuario_id
            )
        )

        conn.commit()

    cursor.execute(
        'SELECT * FROM usuarios ORDER BY id'
    )

    usuarios = cursor.fetchall()

    cursor.execute(
        '''
        SELECT *
        FROM historial
        ORDER BY id DESC
        LIMIT 30
        '''
    )

    historial = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template(
        'admin.html',
        usuarios=usuarios,
        historial=historial,
        sonido=SALA_ESTADO["sonido"],
        luces=SALA_ESTADO["luces"]
    )


# ==========================================================
# CICLO AUTOMÁTICO
# ==========================================================

def bucle_ciclo_continuo():

    while SALA_ESTADO["sistema_activo"]:

        # --------------------------------------------------
        # 1. ABRIR RONDA
        # --------------------------------------------------

        SALA_ESTADO["activa"] = True
        SALA_ESTADO["tiempo_restante"] = 20
        SALA_ESTADO["apuestas"] = []
        SALA_ESTADO["ultimo_resultado"] = None
        SALA_ESTADO["numero_ronda"] += 1

        ronda_actual = SALA_ESTADO["numero_ronda"]

        while (
            SALA_ESTADO["tiempo_restante"] > 0
            and SALA_ESTADO["sistema_activo"]
        ):

            time.sleep(1)

            SALA_ESTADO["tiempo_restante"] -= 1

        if not SALA_ESTADO["sistema_activo"]:
            break

        # --------------------------------------------------
        # 2. CERRAR APUESTAS
        # --------------------------------------------------

        SALA_ESTADO["activa"] = False

        # Resultado temporal aleatorio.
        numero_ganador = obtener_resultado_ruleta()

        color_ganador = obtener_color(
            numero_ganador
        )

        # --------------------------------------------------
        # 3. PROCESAR APUESTAS
        # --------------------------------------------------

        conn = get_db_connection()
        cursor = conn.cursor()

        for ap in SALA_ESTADO["apuestas"]:

            gano = (
                ap['numero'] == numero_ganador
            )

            cursor.execute(
                '''
                SELECT saldo
                FROM usuarios
                WHERE id = %s
                ''',
                (ap['user_id'],)
            )

            user = cursor.fetchone()

            if user:

                if gano:

                    premio = ap['monto'] * 2

                    nuevo_saldo = (
                        user['saldo'] + premio
                    )

                    resultado_str = "GANASTE"

                else:

                    nuevo_saldo = (
                        user['saldo'] - ap['monto']
                    )

                    resultado_str = "PERDISTE"

                cursor.execute(
                    '''
                    UPDATE usuarios
                    SET saldo = %s
                    WHERE id = %s
                    ''',
                    (
                        nuevo_saldo,
                        ap['user_id']
                    )
                )

                cursor.execute(
                    '''
                    INSERT INTO historial
                    (
                        usuario_id,
                        username,
                        monto,
                        numero_elegido,
                        color_elegido,
                        numero_ganador,
                        color_ganador,
                        resultado
                    )
                    VALUES
                    (%s, %s, %s, %s, %s, %s, %s, %s)
                    ''',
                    (
                        ap['user_id'],
                        ap['username'],
                        ap['monto'],
                        ap['numero'],
                        ap['color'],
                        numero_ganador,
                        color_ganador,
                        resultado_str
                    )
                )

        conn.commit()

        cursor.close()
        conn.close()

        # --------------------------------------------------
        # 4. GUARDAR RESULTADO
        # --------------------------------------------------

        SALA_ESTADO["ultimo_resultado"] = {
            "numero_ganador": numero_ganador,
            "color_ganador": color_ganador,
            "apuestas_ronda": SALA_ESTADO["apuestas"].copy(),
            "numero_ronda": ronda_actual
        }

        # --------------------------------------------------
        # 5. ENVIAR ORDEN AL ESP32
        # --------------------------------------------------

        enviar_a_esp32_async(
            numero_ganador,
            1 if SALA_ESTADO["sonido"] else 0,
            1 if SALA_ESTADO["luces"] else 0
        )

        # --------------------------------------------------
        # 6. PAUSA ANTES DE LA SIGUIENTE RONDA
        # --------------------------------------------------

        tiempo_pausa = 5

        while (
            tiempo_pausa > 0
            and SALA_ESTADO["sistema_activo"]
        ):

            time.sleep(1)

            tiempo_pausa -= 1


# ==========================================================
# ABRIR SALA
# ==========================================================

@app.route('/admin/abrir_sala', methods=['POST'])
def abrir_sala_admin():

    admins_autorizados = [
        'Capi admin',
        'El diavlo',
        'admin'
    ]

    if session.get('username') not in admins_autorizados:

        return jsonify({
            'status': 'error',
            'message': 'No autorizado'
        }), 403

    if SALA_ESTADO["sistema_activo"]:

        return jsonify({
            'status': 'error',
            'message': 'El sistema de ciclos ya está activo'
        }), 400

    SALA_ESTADO["sistema_activo"] = True

    threading.Thread(
        target=bucle_ciclo_continuo,
        daemon=True
    ).start()

    return jsonify({
        'status': 'ok',
        'message': 'Ciclo continuo de sala iniciado con éxito'
    })


# ==========================================================
# CERRAR SALA
# ==========================================================

@app.route('/admin/cerrar_sala', methods=['POST'])
def cerrar_sala_admin():

    admins_autorizados = [
        'Capi admin',
        'El diavlo',
        'admin'
    ]

    if session.get('username') not in admins_autorizados:

        return jsonify({
            'status': 'error',
            'message': 'No autorizado'
        }), 403

    SALA_ESTADO["sistema_activo"] = False
    SALA_ESTADO["activa"] = False
    SALA_ESTADO["tiempo_restante"] = 0

    return jsonify({
        'status': 'ok',
        'message': 'Sala cerrada por completo'
    })


# ==========================================================
# CONTROL GLOBAL DE SONIDO Y LUCES
# ==========================================================

@app.route('/admin/efectos', methods=['POST'])
def configurar_efectos():

    admins_autorizados = [
        'Capi admin',
        'El diavlo',
        'admin'
    ]

    if session.get('username') not in admins_autorizados:

        return jsonify({
            'status': 'error',
            'message': 'No autorizado'
        }), 403

    data = request.json or {}

    if 'sonido' in data:
        SALA_ESTADO["sonido"] = bool(data["sonido"])

    if 'luces' in data:
        SALA_ESTADO["luces"] = bool(data["luces"])

    return jsonify({
        'status': 'ok',
        'sonido': SALA_ESTADO["sonido"],
        'luces': SALA_ESTADO["luces"]
    })


# ==========================================================
# ESTADO DE LA SALA
# ==========================================================

@app.route('/estado_sala', methods=['GET'])
def estado_sala():

    return jsonify({

        "sistema_activo":
            SALA_ESTADO["sistema_activo"],

        "activa":
            SALA_ESTADO["activa"],

        "tiempo_restante":
            SALA_ESTADO["tiempo_restante"],

        "apuestas":
            SALA_ESTADO["apuestas"],

        "ultimo_resultado":
            SALA_ESTADO["ultimo_resultado"],

        "sonido":
            SALA_ESTADO["sonido"],

        "luces":
            SALA_ESTADO["luces"],

        "numero_ronda":
            SALA_ESTADO["numero_ronda"]
    })


# ==========================================================
# REALIZAR APUESTA
# ==========================================================

@app.route('/apostar_sala', methods=['POST'])
def apostar_sala():

    if 'user_id' not in session:

        return jsonify({
            'status': 'error',
            'message': 'No autorizado'
        }), 401

    if not SALA_ESTADO["activa"]:

        return jsonify({
            'status': 'error',
            'message':
                'No hay ninguna ronda abierta en este momento'
        }), 400

    data = request.json

    monto = float(
        data.get('monto', 0)
    )

    numero = int(
        data.get('numero', 0)
    )

    color = data.get('color')

    if numero < 0 or numero > 23:

        return jsonify({
            'status': 'error',
            'message': 'Número inválido'
        }), 400

    if monto <= 0:

        return jsonify({
            'status': 'error',
            'message': 'Monto inválido'
        }), 400

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        '''
        SELECT *
        FROM usuarios
        WHERE id = %s
        ''',
        (session['user_id'],)
    )

    user = cursor.fetchone()

    cursor.close()
    conn.close()

    if not user or monto > user['saldo']:

        return jsonify({
            'status': 'error',
            'message':
                'Saldo insuficiente o usuario inválido'
        }), 400

    for ap in SALA_ESTADO["apuestas"]:

        if ap['user_id'] == session['user_id']:

            return jsonify({
                'status': 'error',
                'message':
                    'Ya registraste tu apuesta para esta ronda'
            }), 400

    SALA_ESTADO["apuestas"].append({

        'user_id':
            session['user_id'],

        'username':
            session['username'],

        'monto':
            monto,

        'numero':
            numero,

        'color':
            color
    })

    return jsonify({
        'status': 'ok',
        'message':
            'Apuesta registrada en la sala'
    })


if __name__ == '__main__':

    app.run(
        host='0.0.0.0',
        port=5000,
        debug=True
    )