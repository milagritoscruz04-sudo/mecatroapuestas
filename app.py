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

# Estado global de la sala en vivo controlada por el administrador
SALA_ESTADO = {
    "activa": False,
    "tiempo_restante": 0,
    "apuestas": [],
    "ultimo_resultado": None
}

def get_db_connection():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        database_url = "postgresql://postgres:apuestafijas2A@db.voyfoiqionnheakpoint.supabase.co:6543/postgres"
        
    conn = psycopg2.connect(database_url, cursor_factory=RealDictCursor)
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
        cursor.execute("INSERT INTO usuarios (id, username, password, saldo) VALUES (%s, %s, %s, %s)", ('M000', 'admin', 'admin123', 5000.0))
        
    conn.commit()
    cursor.close()
    conn.close()

init_db()

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
        return 'Verde'
    elif numero in [8, 23, 5, 2, 7, 3, 4, 9, 15, 14, 20, 13]:
        return 'Negro'
    else:
        return 'Rojo'

def enviar_a_esp32_async(numero_ganador, sonido=1, luces=1):
    def tarea():
        try:
            requests.get(
                f"{ESP32_IP}/girar?ganador={numero_ganador}&sonido={sonido}&luces={luces}",
                timeout=3
            )
        except Exception as e:
            print(f"[ESP32 Comms] No se pudo conectar con el hardware: {e}")
    
    threading.Thread(target=tarea).start()

def calcular_ganador_casa(apuestas_activas):
    todos_los_numeros = list(range(0, 24))
    
    if not apuestas_activas:
        return random.randint(0, 23)
    
    if random.random() < 0.30:
        return random.randint(0, 23)
        
    numeros_apostados = [a['numero'] for a in apuestas_activas]
    numeros_no_apostados = [n for n in todos_los_numeros if n not in numeros_apostados]
    
    if numeros_no_apostados:
        return random.choice(numeros_no_apostados)
    
    return random.randint(0, 23)

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
    
    admins_autorizados = ['Capi admin', 'El diavlo', 'admin']
    es_admin = session.get('username') in admins_autorizados

    return render_template('index.html', 
                           usuario=user['username'], 
                           user_id=user['id'], 
                           saldo=user['saldo'], 
                           historial=historial,
                           es_admin=es_admin)

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
        user = cursor.fetchone()
        if user:
            cursor.close()
            conn.close()
            return jsonify({'status': 'error', 'message': 'El nombre de usuario ya existe'}), 400
        
        nuevo_id = generar_siguiente_id()
        cursor.execute('INSERT INTO usuarios (id, username, password, saldo) VALUES (%s, %s, %s, %s)', (nuevo_id, username, password, 200.0))
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({'status': 'ok', 'message': 'Usuario registrado con éxito'})
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
        return jsonify({'status': 'error', 'message': 'Usuario o contraseña incorrectos'}), 400

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login_view'))

@app.route('/admin', methods=['GET', 'POST'])
def admin_panel():
    admins_autorizados = ['Capi admin', 'El diavlo', 'admin']
    usuario_actual = session.get('username')
    
    if not usuario_actual or usuario_actual not in admins_autorizados:
        return "Acceso denegado. Solo administradores autorizados.", 403
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if request.method == 'POST':
        usuario_id = request.form.get('usuario_id')
        nuevo_saldo = float(request.form.get('nuevo_saldo', 0))
        cursor.execute("UPDATE usuarios SET saldo = %s WHERE id = %s", (nuevo_saldo, usuario_id))
        conn.commit()

    cursor.execute('SELECT * FROM usuarios')
    usuarios = cursor.fetchall()
    
    cursor.execute('SELECT * FROM historial ORDER BY id DESC LIMIT 30')
    historial = cursor.fetchall()
    
    cursor.close()
    conn.close()
    
    return render_template('admin.html', usuarios=usuarios, historial=historial)

@app.route('/admin/abrir_sala', methods=['POST'])
def abrir_sala_admin():
    admins_autorizados = ['Capi admin', 'El diavlo', 'admin']
    if session.get('username') not in admins_autorizados:
        return jsonify({'status': 'error', 'message': 'No autorizado'}), 403
    
    if SALA_ESTADO["activa"]:
        return jsonify({'status': 'error', 'message': 'Ya hay una sala activa'}), 400

    SALA_ESTADO["activa"] = True
    SALA_ESTADO["tiempo_restante"] = 20
    SALA_ESTADO["apuestas"] = []
    SALA_ESTADO["ultimo_resultado"] = None

    def temporizador_sala():
        while SALA_ESTADO["tiempo_restante"] > 0:
            time.sleep(1)
            SALA_ESTADO["tiempo_restante"] -= 1

        SALA_ESTADO["activa"] = False
        
        numero_ganador = calcular_ganador_casa(SALA_ESTADO["apuestas"])
        color_ganador = obtener_color(numero_ganador)
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        for ap in SALA_ESTADO["apuestas"]:
            gano = (ap['numero'] == numero_ganador) and (ap['color'].lower() == color_ganador.lower())
            cursor.execute('SELECT saldo FROM usuarios WHERE id = %s', (ap['user_id'],))
            user = cursor.fetchone()
            if user:
                nuevo_saldo = user['saldo'] + ap['monto'] if gano else user['saldo'] - ap['monto']
                resultado_str = "GANASTE" if gano else "PERDISTE"
                
                cursor.execute('UPDATE usuarios SET saldo = %s WHERE id = %s', (nuevo_saldo, ap['user_id']))
                cursor.execute('''
                    INSERT INTO historial (usuario_id, username, monto, numero_elegido, color_elegido, numero_ganador, color_ganador, resultado)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ''', (ap['user_id'], ap['username'], ap['monto'], ap['numero'], ap['color'], numero_ganador, color_ganador, resultado_str))

        conn.commit()
        cursor.close()
        conn.close()

        SALA_ESTADO["ultimo_resultado"] = {
            "numero_ganador": numero_ganador,
            "color_ganador": color_ganador,
            "apuestas_ronda": SALA_ESTADO["apuestas"]
        }

        time.sleep(1)
        enviar_a_esp32_async(numero_ganador, 1, 1)

    threading.Thread(target=temporizador_sala).start()
    return jsonify({'status': 'ok', 'message': 'Sala abierta con éxito'})

@app.route('/estado_sala', methods=['GET'])
def estado_sala():
    return jsonify({
        "activa": SALA_ESTADO["activa"],
        "tiempo_restante": SALA_ESTADO["tiempo_restante"],
        "apuestas": SALA_ESTADO["apuestas"],
        "ultimo_resultado": SALA_ESTADO["ultimo_resultado"]
    })

@app.route('/apostar_sala', methods=['POST'])
def apostar_sala():
    if 'user_id' not in session:
        return jsonify({'status': 'error', 'message': 'No autorizado'}), 401
    
    if not SALA_ESTADO["activa"]:
        return jsonify({'status': 'error', 'message': 'No hay ninguna sala abierta en este momento'}), 400
    
    data = request.json
    monto = float(data.get('monto', 0))
    numero = int(data.get('numero', 0))
    color = data.get('color')
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM usuarios WHERE id = %s', (session['user_id'],))
    user = cursor.fetchone()
    cursor.close()
    conn.close()
    
    if not user or monto > user['saldo']:
        return jsonify({'status': 'error', 'message': 'Saldo insuficiente o usuario inválido'}), 400
    
    for ap in SALA_ESTADO["apuestas"]:
        if ap['user_id'] == session['user_id']:
            return jsonify({'status': 'error', 'message': 'Ya registraste tu apuesta para esta ronda'}), 400

    SALA_ESTADO["apuestas"].append({
        'user_id': session['user_id'],
        'username': session['username'],
        'monto': monto,
        'numero': numero,
        'color': color
    })
    
    return jsonify({'status': 'ok', 'message': 'Apuesta registrada en la sala'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)