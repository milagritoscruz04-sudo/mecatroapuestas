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

APUESTAS_RONDA = [] 

# Función para conectar a Supabase (PostgreSQL)
def get_db_connection():
    # Obtiene la URL de conexión desde las variables de entorno de Render o tu entorno local
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:

        database_url = "postgresql://postgres:apuestafijas2A@db.voyfoiqionnheakpoint.supabase.co:5432/postgres"
        
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
    
    # Crear admin por defecto si no existe
    cursor.execute("SELECT * FROM usuarios WHERE username = 'admin'")
    if not cursor.fetchone():
        cursor.execute("INSERT INTO usuarios (id, username, password, saldo) VALUES (%s, %s, %s, %s)", ('M000', 'admin', 'admin123', 5000.0))
        
    conn.commit()
    cursor.close()
    conn.close()

# Inicializa las tablas en Supabase al arrancar
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

# Función para enviar orden al ESP32 en segundo plano
def enviar_a_esp32_async(numero_ganador, sonido, luces):
    def tarea():
        try:
            requests.get(
                f"{ESP32_IP}/girar?ganador={numero_ganador}&sonido={sonido}&luces={luces}",
                timeout=3
            )
        except Exception as e:
            print(f"[ESP32 Comms] No se pudo conectar con el hardware: {e}")
    
    threading.Thread(target=tarea).start()

# Algoritmo "La Casa Nunca Pierde"
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
    
    return render_template('index.html', 
                           usuario=user['username'], 
                           user_id=user['id'], 
                           saldo=user['saldo'], 
                           historial=historial)

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
        session.clear()  # Limpia cualquier sesión residual anterior
        session['user_id'] = user['id']
        session['username'] = user['username']
        return jsonify({'status': 'ok', 'user_id': user['id']})
    else:
        return jsonify({'status': 'error', 'message': 'Usuario o contraseña incorrectos'}), 400

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login_view'))

# PANEL ADMIN EXCLUSIVO PARA Capi admin y El diavlo
@app.route('/admin', methods=['GET', 'POST'])
def admin_panel():
    admins_autorizados = ['Capi admin', 'El diavlo']
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

# MODO INDIVIDUAL CON SUSPENSO
@app.route('/apostar_individual', methods=['POST'])
def apostar_individual():
    if 'user_id' not in session:
        return jsonify({'status': 'error', 'message': 'No autorizado'}), 401
    
    data = request.json
    monto = float(data.get('monto', 0))
    numero_elegido = int(data.get('numero', 0))
    color_elegido = data.get('color')
    sonido = 1 if data.get('sonido', True) else 0
    luces = 1 if data.get('luces', True) else 0

    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM usuarios WHERE id = %s', (session['user_id'],))
    user = cursor.fetchone()
    
    if not user or monto > user['saldo']:
        cursor.close()
        conn.close()
        return jsonify({'status': 'error', 'message': 'Saldo insuficiente o usuario inválido'}), 400

    apuesta_temp = [{'numero': numero_elegido, 'monto': monto}]
    numero_ganador = calcular_ganador_casa(apuesta_temp)
    color_ganador = obtener_color(numero_ganador)
    
    gano = (numero_elegido == numero_ganador) or (color_elegido.lower() == color_ganador.lower())
    nuevo_saldo = user['saldo'] + monto if gano else user['saldo'] - monto
    resultado_str = "GANASTE" if gano else "PERDISTE"
    
    cursor.execute('UPDATE usuarios SET saldo = %s WHERE id = %s', (nuevo_saldo, session['user_id']))
    cursor.execute('''
        INSERT INTO historial (usuario_id, username, monto, numero_elegido, color_elegido, numero_ganador, color_ganador, resultado)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    ''', (session['user_id'], session['username'], monto, numero_elegido, color_elegido, numero_ganador, color_ganador, resultado_str))
    
    conn.commit()
    cursor.close()
    conn.close()

    time.sleep(3.5)
    enviar_a_esp32_async(numero_ganador, sonido, luces)

    return jsonify({
        'status': 'ok',
        'nuevo_saldo': nuevo_saldo,
        'numero_ganador': numero_ganador,
        'color_ganador': color_ganador,
        'resultado': resultado_str
    })

# MODO SALA MULTIJUGADOR
@app.route('/registrar_apuesta_sala', methods=['POST'])
def registrar_apuesta_sala():
    if 'user_id' not in session:
        return jsonify({'status': 'error', 'message': 'No autorizado'}), 401
    
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
        return jsonify({'status': 'error', 'message': 'Saldo insuficiente'}), 400
    
    for ap in APUESTAS_RONDA:
        if ap['user_id'] == session['user_id']:
            return jsonify({'status': 'error', 'message': 'Ya registraste tu apuesta para esta ronda'}), 400

    APUESTAS_RONDA.append({
        'user_id': session['user_id'],
        'username': session['username'],
        'monto': monto,
        'numero': numero,
        'color': color
    })
    
    return jsonify({'status': 'ok', 'apuestas_ronda': APUESTAS_RONDA})

@app.route('/obtener_ronda', methods=['GET'])
def obtener_ronda():
    return jsonify({'apuestas_ronda': APUESTAS_RONDA})

@app.route('/girar_sala', methods=['POST'])
def girar_sala():
    global APUESTAS_RONDA
    
    if not APUESTAS_RONDA:
        return jsonify({'status': 'error', 'message': 'No hay apuestas en la sala'}), 400

    numero_ganador = calcular_ganador_casa(APUESTAS_RONDA)
    color_ganador = obtener_color(numero_ganador)
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    for ap in APUESTAS_RONDA:
        gano = (ap['numero'] == numero_ganador) or (ap['color'].lower() == color_ganador.lower())
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

    time.sleep(3.5)
    enviar_a_esp32_async(numero_ganador, 1, 1)

    APUESTAS_RONDA = []

    return jsonify({
        'status': 'ok',
        'numero_ganador': numero_ganador,
        'color_ganador': color_ganador
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)