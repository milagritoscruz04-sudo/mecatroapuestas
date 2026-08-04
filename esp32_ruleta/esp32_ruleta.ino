#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>

// Configuración de la pantalla LCD I2C
LiquidCrystal_I2C lcd(0x27, 16, 2);

// Pines del Driver ULN2003 para el motor paso a paso
#define IN1 19
#define IN2 18
#define IN3 5
#define IN4 17

// Pin del Buzzer integrado
const int PIN_BUZZER = 23;

// Pin del Módulo LED
const int PIN_LED = 4;

// Configuración de red Wi-Fi
const char* ssid = "XIOMARA-2.4G";
const char* password = "Xnicole27";

// URL de tu servidor en Render
const char* serverUrl = "https://mecatroapuestas.onrender.com/api/esp32/cmd";

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

// ORDEN EXACTO DE LOS NÚMEROS EN TU RULETA FÍSICA
int ordenRuleta[24] = {0, 5, 10, 19, 8, 11, 22, 17, 2, 3, 12, 21, 20, 9, 14, 23, 18, 1, 4, 15, 6, 7, 16, 13};
int indicePosicionActual = 0;

const int PASOS_TOTALES_VUELTA = 4096;
const float PASOS_POR_CASILLA = (float)PASOS_TOTALES_VUELTA / 24.0;

int ultimaRondaProcesada = -1;
String ultimaFase = "";

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

int buscarIndiceNumero(int numeroBuscado) {
  for (int i = 0; i < 24; i++) {
    if (ordenRuleta[i] == numeroBuscado) {
      return i;
    }
  }
  return 0;
}

void ejecutarGiro(int numeroGanador, bool usarSonido, bool usarLuces) {
  mostrarMensajeLCD("  GIRANDO...", " NUM: " + String(numeroGanador));
  
  if (usarSonido) {
    tone(PIN_BUZZER, 800, 100);
  }

  int indiceDestino = buscarIndiceNumero(numeroGanador);
  int distanciaCasillas = (indiceDestino - indicePosicionActual + 24) % 24;
  
  int pasosCasillasDestino = round(distanciaCasillas * PASOS_POR_CASILLA);
  int pasosVueltaShow = PASOS_TOTALES_VUELTA * 1;
  
  int tramoFreno1 = round(pasosCasillasDestino * 0.5);
  int tramoFreno2 = pasosCasillasDestino - tramoFreno1;

  // Si las luces están activadas en el admin, enciende el LED durante el giro
  if (usarLuces) {
    digitalWrite(PIN_LED, HIGH);
  }

  darPasos(pasosVueltaShow, 1200);
  
  if (tramoFreno1 > 0) {
    darPasos(tramoFreno1, 1800);
    if (usarSonido) tone(PIN_BUZZER, 1000, 40);
  }
  
  if (tramoFreno2 > 0) {
    darPasos(tramoFreno2, 2600);
    if (usarSonido) tone(PIN_BUZZER, 1200, 60);
  }

  indicePosicionActual = indiceDestino;
  apagarMotor();

  mostrarMensajeLCD("GANADOR: " + String(numeroGanador), "¡EXCELENTE!");
  
  if (usarSonido) {
    sonidoGanador();
  }

  // Parpadeo de victoria con el LED si están activadas las luces
  if (usarLuces) {
    for (int i = 0; i < 5; i++) {
      digitalWrite(PIN_LED, LOW);
      delay(100);
      digitalWrite(PIN_LED, HIGH);
      delay(100);
    }
    digitalWrite(PIN_LED, LOW);
  }
}

void setup() {
  Serial.begin(115200);

  pinMode(IN1, OUTPUT);
  pinMode(IN2, OUTPUT);
  pinMode(IN3, OUTPUT);
  pinMode(IN4, OUTPUT);
  pinMode(PIN_BUZZER, OUTPUT);
  pinMode(PIN_LED, OUTPUT);

  digitalWrite(PIN_LED, LOW);

  lcd.init();
  lcd.backlight();
  mostrarMensajeLCD(" CONECTANDO...", " WIFI...");

  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println("\n¡WiFi Conectado!");
  mostrarMensajeLCD(" IP ASIGNADA:", WiFi.localIP().toString());
  delay(2000);
  mostrarMensajeLCD("MECATROAPUESTAS", "LISTO PARA JUGAR");
}

void loop() {
  if (WiFi.status() == WL_CONNECTED) {
    HTTPClient http;
    http.begin(serverUrl);
    int httpCode = http.GET();

    if (httpCode == 200) {
      String payload = http.getString();
      
      StaticJsonDocument<256> doc;
      DeserializationError error = deserializeJson(doc, payload);

      if (!error) {
        String fase = doc["fase"];
        int ronda = doc["ronda"];
        int ganador = doc["ganador"];
        bool sonido = doc["sonido"] == 1;
        bool luces = doc["luces"] == 1;

        // Mantener el LED encendido en tiempo real si el admin activó las luces fuera del giro
        digitalWrite(PIN_LED, luces ? HIGH : LOW);

        if (fase == "girando" && ronda != ultimaRondaProcesada) {
          ultimaRondaProcesada = ronda;
          ejecutarGiro(ganador, sonido, luces);
        } 
        else if (fase == "apuestas" && ultimaFase != "apuestas") {
          mostrarMensajeLCD(" REALIZA TU", " APUESTA (R" + String(ronda) + ")");
        } 
        else if (fase == "resultado" && ultimaFase != "resultado") {
          mostrarMensajeLCD("RONDA " + String(ronda) + " FINAL", "GANADOR: " + String(ganador));
        }

        ultimaFase = fase;
      }
    }
    http.end();
  }
  
  delay(500);
}