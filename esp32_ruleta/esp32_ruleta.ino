#include <WiFi.h>
#include <WebServer.h>
#include <ArduinoJson.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>

// Configuración de la pantalla LCD I2C (Dirección 0x27, 16 columnas, 2 filas)
LiquidCrystal_I2C lcd(0x27, 16, 2);

// Pines del Driver ULN2003 para el motor paso a paso
#define IN1 19
#define IN2 18
#define IN3 5
#define IN4 17

// Pin del Buzzer integrado
const int PIN_BUZZER = 23;

WebServer server(80);

const char* ssid = "XIOMARA-2.4G";
const char* password = "Xnicole27";

// Secuencia de pasos para el motor 28BYJ-48
const int pasoSecuencia[8][4] = {
  {1, 0, 0, 0},
  {1, 1, 0, 0},
  {0, 1, 0, 0},
  {0, 1, 1, 0},
  {0, 0, 1, 0},
  {0, 0, 1, 1},
  {0, 0, 0, 1},
  {1, 0, 0, 1}
};

int pasoActualMotor = 0;

// ORDEN EXACTO DE LOS NÚMEROS EN TU RULETA FÍSICA (En sentido horario)
int ordenRuleta[24] = {0, 5, 10, 19, 8, 11, 22, 17, 2, 3, 12, 21, 20, 9, 14, 23, 18, 1, 4, 15, 6, 7, 16, 13};
int indicePosicionActual = 0; // Posición inicial en el array (empieza en el 0)

const int PASOS_TOTALES_VUELTA = 4096;
const float PASOS_POR_CASILLA = (float)PASOS_TOTALES_VUELTA / 24.0; // ~170.66 pasos por número

void mostrarMensajeLCD(String linea1, String linea2) {
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print(linea1);
  lcd.setCursor(0, 1);
  lcd.print(linea2);
}

void darPasos(int cantidadPasos, int retardoMicrosegundos) {
  for (int i = 0; i < cantidadPasos; i++) {
    pasoActualMotor = (pasoActualMotor + 1) % 8;
    digitalWrite(IN1, pasoSecuencia[pasoActualMotor][0]);
    digitalWrite(IN2, pasoSecuencia[pasoActualMotor][1]);
    digitalWrite(IN3, pasoSecuencia[pasoActualMotor][2]);
    digitalWrite(IN4, pasoSecuencia[pasoActualMotor][3]);
    delayMicroseconds(retardoMicrosegundos);
  }
}

void apagarMotor() {
  digitalWrite(IN1, LOW);
  digitalWrite(IN2, LOW);
  digitalWrite(IN3, LOW);
  digitalWrite(IN4, LOW);
}

void sonidoGanador() {
  tone(PIN_BUZZER, 523, 100); delay(120);
  tone(PIN_BUZZER, 659, 100); delay(120);
  tone(PIN_BUZZER, 784, 100); delay(120);
  tone(PIN_BUZZER, 1046, 300); delay(350);
  noTone(PIN_BUZZER);
}

// Función para buscar en qué índice del array está el número ganador que manda Python
int buscarIndiceNumero(int numeroBuscado) {
  for (int i = 0; i < 24; i++) {
    if (ordenRuleta[i] == numeroBuscado) {
      return i;
    }
  }
  return 0;
}

void handleGirar() {
  if (server.hasArg("ganador")) {
    int numeroGanador = server.arg("ganador").toInt();
    
    // 1. Mostrar en LCD que está girando
    mostrarMensajeLCD("  GIRANDO...", " NUM: " + String(numeroGanador));
    tone(PIN_BUZZER, 800, 100);

    // 2. Calcular casilla destino y pasos exactos redondeados
    int indiceDestino = buscarIndiceNumero(numeroGanador);
    int distanciaCasillas = (indiceDestino - indicePosicionActual + 24) % 24;
    
    int pasosCasillasDestino = round(distanciaCasillas * PASOS_POR_CASILLA);
    int pasosVueltaShow = PASOS_TOTALES_VUELTA * 1; // 1 vuelta completa rápida
    
    // Tramos de desaceleración para el tramo final
    int tramoFreno1 = round(pasosCasillasDestino * 0.5);
    int tramoFreno2 = pasosCasillasDestino - tramoFreno1; // Residuo exacto

    // 3. Ejecutar giro rápido + frenado progresivo (~6 a 8 segundos en total)
    darPasos(pasosVueltaShow, 1200);
    
    if (tramoFreno1 > 0) {
      darPasos(tramoFreno1, 1800);
      tone(PIN_BUZZER, 1000, 40);
    }
    
    if (tramoFreno2 > 0) {
      darPasos(tramoFreno2, 2600);
      tone(PIN_BUZZER, 1200, 60);
    }

    // Actualizar posición global y liberar bobinas del motor
    indicePosicionActual = indiceDestino;
    apagarMotor();

    // 4. Mostrar el resultado final en la LCD y reproducir tono de victoria
    mostrarMensajeLCD("GANADOR: " + String(numeroGanador), "¡EXCELENTE!");
    sonidoGanador();

    // Responder a Python que el giro concluyó con éxito
    server.send(200, "application/json", "{\"status\":\"ok\", \"ganador\": " + String(numeroGanador) + "}");
  } else {
    server.send(400, "application/json", "{\"status\":\"error\", \"mensaje\":\"Falta argumento ganador\"}");
  }
}

void setup() {
  Serial.begin(115200);

  pinMode(IN1, OUTPUT);
  pinMode(IN2, OUTPUT);
  pinMode(IN3, OUTPUT);
  pinMode(IN4, OUTPUT);
  pinMode(PIN_BUZZER, OUTPUT);

  // Inicializar Pantalla LCD
  lcd.init();
  lcd.backlight();
  mostrarMensajeLCD(" CONECTANDO...", " WIFI...");

  // Conexión Wi-Fi
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println("");
  Serial.println("¡WiFi Conectado!");
  Serial.print("IP del ESP32: 192.168.18.100");
  Serial.println(WiFi.localIP());

  mostrarMensajeLCD(" IP ASIGNADA: 192.168.18.100", WiFi.localIP().toString());
  delay(2500);
  mostrarMensajeLCD("MECATROAPUESTAS", "LISTO PARA JUGAR");

  // Endpoint web consumido por el backend en Python
  server.on("/girar", handleGirar);
  server.begin();
}

void loop() {
  server.handleClient();
}