import time
import json
from datetime import datetime, timedelta
import threading
import requests
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import psycopg2
from psycopg2.extras import RealDictCursor
import os

app = Flask(__name__)
app.secret_key = 'tu_clave_secreta_super_segura'

# Configuración de la base de datos Supabase / PostgreSQL
DATABASE_URL = os.environ.get('DATABASE_URL', 'postgresql://postgres.voyfoiqionnheakpoint:apuestafijas2A@aws-1-us-west-2.pooler.supabase.com:6543/postgres')

# IP del ESP32
ESP32_IP = "http://192.168.18.100"

# Duración de las fases en segundos
DURACION_APUESTAS = 20
DURACION_GIRANDO = 5
DURACION_RESULTADO = 5

def get_db_connection():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    return conn

def obtener_color(numero):
    if numero == 0:
        return "Verde"
    elif numero % 2 == 0:
        return "Rojo"
    else:
        return "Negro"

def obtener_resultado_ruleta(apuestas_actuales):
    import random
    return random.randint(0, 23)

def sala_sigue_activa(cursor):
    cursor.execute("SELECT sistema_activo FROM sala_live WHERE id = 1")
    res = cursor.fetchone()
    return res and res['sistema_activo']

def latido_y_activo():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT sistema_activo FROM sala_live WHERE id = 1")
        res = cursor.fetchone()
        cursor.close()
        conn.close()
        return res and res['sistema_activo']
    except Exception:
        return False

def enviar_a_esp32_async(numero_ganador, sonido, luces):
    def tarea():
        try:
            url = f"{ESP32_IP}/girar?ganador={numero_ganador}&sonido={sonido}&luces={luces}"
            requests.get(url, timeout=3)
        except Exception as e:
            print(f"[Aviso ESP32] No se pudo contactar al ESP32: {e}")
    
    threading.Thread(target=tarea).start()

# --- BUCLE DE JUEGO CONTINUO CORREGIDO Y BLINDADO ---
def bucle_ciclo_continuo():
    try:
        while True:
            conn = get_db_connection()
            cursor = conn.cursor()
            try:
                if not sala_sigue_activa(cursor):
                    break

                cursor.execute("SELECT numero_ronda FROM sala_live WHERE id = 1")
                ronda_actual = cursor.fetchone()['numero_ronda'] + 1
                fin_apuestas = datetime.utcnow() + timedelta(seconds=DURACION_APUESTAS)

                # FASE 1: APUESTAS
                cursor.execute('''
                    UPDATE sala_live
                    SET fase = 'apuestas', numero_ronda = %s, fase_termina_en = %s, heartbeat = NOW()
                    WHERE id = 1
                ''', (ronda_actual, fin_apuestas))
                conn.commit()
            finally:
                cursor.close()
                conn.close()

            # Espera activa controlada para apuestas
            inicio_espera = time.time()
            while time.time() - inicio_espera < DURACION_APUESTAS:
                time.sleep(0.5)
                if not latido_y_activo():
                    return

            # FASE 2: GIRANDO RULETA
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                try:
                    fin_girando = datetime.utcnow() + timedelta(seconds=DURACION_GIRANDO)
                    cursor.execute('''
                        UPDATE sala_live SET fase = 'girando', fase_termina_en = %s, heartbeat = NOW() WHERE id = 1
                    ''', (fin_girando,))
                    conn.commit()
                finally:
                    cursor.close()
                    conn.close()
            except Exception as e:
                print(f"[Error fase girando] {e}")

            inicio_espera = time.time()
            while time.time() - inicio_espera < DURACION_GIRANDO:
                time.sleep(0.5)
                if not latido_y_activo():
                    return

            # FASE 3: RESOLVER Y MOSTRAR GANADOR
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                try:
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

                    fin_resultado = datetime.utcnow() + timedelta(seconds=DURACION_RESULTADO)
                    cursor.execute('''
                        UPDATE sala_live SET
                            fase = 'resultado',
                            ultima_ronda_resuelta = %s,
                            ultimo_numero_ganador = %s,
                            ultimo_color_ganador = %s,
                            ultimo_total_repartido = %s,
                            ultimo_ganadores = %s,
                            fase_termina_en = %s,
                            heartbeat = NOW()
                        WHERE id = 1
                    ''', (ronda_actual, numero_ganador, color_ganador, total_repartido, json.dumps(ganadores_list), fin_resultado))
                    conn.commit()

                    try:
                        cursor.execute("SELECT sonido, luces FROM sala_live WHERE id = 1")
                        efectos = cursor.fetchone()
                        enviar_a_esp32_async(numero_ganador, 1 if efectos['sonido'] else 0, 1 if efectos['luces'] else 0)
                    except Exception:
                        pass

                finally:
                    cursor.close()
                    conn.close()
            except Exception as e:
                print(f"[Error resolviendo ronda] {e}")

            inicio_espera = time.time()
            while time.time() - inicio_espera < DURACION_RESULTADO:
                time.sleep(0.5)
                if not latido_y_activo():
                    return

    except Exception as e:
        print(f"[Error bucle sala] {e}")
    finally:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("UPDATE sala_live SET hilo_activo = FALSE, sistema_activo = FALSE WHERE id = 1")
            conn.commit()
            cursor.close()
            conn.close()
        except Exception as e:
            print(f"[Error liberando liderazgo] {e}")

# --- RUTAS DE LA APLICACIÓN ---

@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        # Captura tanto si envían 'username' como 'usuario' desde el formulario HTML
        username = request.form.get('username') or request.form.get('usuario')
        password = request.form.get('password') or request.form.get('clave')
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Consulta robusta adaptada al esquema de usuarios
        if password:
            cursor.execute("SELECT * FROM usuarios WHERE username = %s AND password = %s", (username, password))
        else:
            cursor.execute("SELECT * FROM usuarios WHERE username = %s", (username,))
            
        user = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if user:
            session['user_id'] = user['id']
            session['username'] = user['username']
            return redirect(url_for('index'))
        else:
            error = "Credenciales incorrectas o usuario no registrado."
            
    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/api/estado_sala')
def estado_sala():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM sala_live WHERE id = 1")
    sala = cursor.fetchone()
    
    segundos_restantes = 0
    if sala and sala['fase_termina_en']:
        diferencia = sala['fase_termina_en'] - datetime.utcnow()
        segundos_restantes = max(0, int(diferencia.total_seconds()))

    cursor.close()
    conn.close()

    if not sala:
        return jsonify({'error': 'Sala no configurada'}), 404

    return jsonify({
        'sistema_activo': sala['sistema_activo'],
        'fase': sala['fase'],
        'numero_ronda': sala['numero_ronda'],
        'segundos_restantes': segundos_restantes,
        'ultimo_numero_ganador': sala['ultimo_numero_ganador'],
        'ultimo_color_ganador': sala['ultimo_color_ganador'],
        'ultimo_ganadores': json.loads(sala['ultimo_ganadores']) if sala['ultimo_ganadores'] else []
    })

@app.route('/api/apostar', methods=['POST'])
def apostar():
    if 'user_id' not in session:
        return jsonify({'error': 'No autorizado'}), 401
    
    data = request.get_json()
    numero = int(data.get('numero'))
    monto = float(data.get('monto'))
    color = obtener_color(numero)
    user_id = session['user_id']
    username = session['username']

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT saldo FROM usuarios WHERE id = %s", (user_id,))
        user = cursor.fetchone()
        if not user or user['saldo'] < monto:
            return jsonify({'error': 'Saldo insuficiente'}), 400

        cursor.execute("SELECT numero_ronda, fase FROM sala_live WHERE id = 1")
        sala = cursor.fetchone()
        if not sala or sala['fase'] != 'apuestas':
            return jsonify({'error': 'No se pueden hacer apuestas en este momento'}), 400

        ronda_actual = sala['numero_ronda']

        nuevo_saldo = user['saldo'] - monto
        cursor.execute("UPDATE usuarios SET saldo = %s WHERE id = %s", (nuevo_saldo, user_id))
        
        cursor.execute('''
            INSERT INTO apuestas_ronda (usuario_id, username, numero, color, monto, numero_ronda)
            VALUES (%s, %s, %s, %s, %s, %s)
        ''', (user_id, username, numero, color, monto, ronda_actual))
        
        conn.commit()
        return jsonify({'success': True, 'nuevo_saldo': nuevo_saldo})
    except Exception as e:
        conn.rollback()
        return jsonify({'error': str(e)}), 500
    finally:
        cursor.close()
        conn.close()

@app.route('/admin/iniciar_sala', methods=['POST'])
def iniciar_sala():
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT sistema_activo, hilo_activo FROM sala_live WHERE id = 1")
        sala = cursor.fetchone()
        
        if not sala['sistema_activo']:
            cursor.execute("UPDATE sala_live SET sistema_activo = TRUE, hilo_activo = TRUE, numero_ronda = 0 WHERE id = 1")
            conn.commit()
            
            hilo = threading.Thread(target=bucle_ciclo_continuo)
            hilo.daemon = True
            hilo.start()
            
        return jsonify({'success': True, 'mensaje': 'Sala iniciada correctamente'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        cursor.close()
        conn.close()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)