#include <WiFi.h>
#include <WebServer.h>
#include <ArduinoJson.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>

LiquidCrystal_I2C lcd(0x27, 16, 2);

#define IN1 19
#define IN2 18
#define IN3 5
#define IN4 17

const int PIN_BUZZER = 23;

WebServer server(80);

// XIOMAREX CAMBIAS ESTO POR LA RED A LA QUE TE VAS A CONECTAR PES
const char* ssid = "TU_WIFI";
const char* password = "TU_WIFI_PASSWORD";

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

int pasoActual = 0;
int contadorRondasUsuario = 0; 

void mostrarMensajeLCD(String linea1, String linea2) {
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print(linea1);
  lcd.setCursor(0, 1);
  lcd.print(linea2);
}

void darPasosConSonido(int cantidadPasos, int retardoMicrosegundos, int frecuenciaSonido) {
  for (int i = 0; i < cantidadPasos; i++) {
    pasoActual = (pasoActual + 1) % 8;
    digitalWrite(IN1, pasoSecuencia[pasoActual][0]);
    digitalWrite(IN2, pasoSecuencia[pasoActual][1]);
    digitalWrite(IN3, pasoSecuencia[pasoActual][2]);
    digitalWrite(IN4, pasoSecuencia[pasoActual][3]);
    delayMicroseconds(retardoMicrosegundos);

    if (i % frecuenciaSonido == 0) {
      tone(PIN_BUZZER, 1200, 5); 
    }
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

void sonidoPerdedor() {
  tone(PIN_BUZZER, 400, 200); delay(230);
  tone(PIN_BUZZER, 350, 200); delay(230);
  tone(PIN_BUZZER, 300, 400); delay(450);
  noTone(PIN_BUZZER);
}

String obtenerColor(int numero) {
  if (numero == 0) return "Verde";
  else if (numero % 2 == 0) return "Rojo";
  else return "Negro";
}

void handleGirar() {
  if (server.hasArg("ganador") || true) {
    
    mostrarMensajeLCD("  GIRANDO...", "  ¡SUERTE!");

    int PASOS_UNA_VUELTA = 4096;

    // Giro de la ruleta con sonido
    darPasosConSonido(PASOS_UNA_VUELTA * 2, 1400, 45);
    int difFrecuencia = 60;
    for (int retardo = 1600; retardo <= 3000; retardo += 350) {
      darPasosConSonido(PASOS_UNA_VUELTA / 4, retardo, difFrecuencia);
      difFrecuencia += 25;
    }
    for (int retardo = 3400; retardo <= 5500; retardo += 500) {
      darPasosConSonido(PASOS_UNA_VUELTA / 8, retardo, difFrecuencia);
      difFrecuencia += 40;
    }
    apagarMotor();

    contadorRondasUsuario++;
    int numGanador;

    bool forzarVictoria = false;
    if (contadorRondasUsuario <= 3) {
      if (random(100) < 60) forzarVictoria = true; 
    } else {
      if (random(100) < 40) forzarVictoria = true; 
    }

    numGanador = random(0, 24);
    String colorGanador = obtenerColor(numGanador);

    if (forzarVictoria) {
      mostrarMensajeLCD("NUM: " + String(numGanador), "FELICIDADES GAN!");
      sonidoGanador();
    } else {
      mostrarMensajeLCD("NUM: " + String(numGanador), "INTENTALO DE NUEVO");
      sonidoPerdedor();
    }

    String jsonRespuesta = "{\"status\":\"ok\", \"ganador\": " + String(numGanador) + "}";
    server.send(200, "application/json", jsonRespuesta);
  } else {
    server.send(400, "application/json", "{\"status\":\"error\"}");
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

  // Conectarse a la red Wi-Fi del router
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println("");
  Serial.println("¡WiFi Conectado!");
  Serial.print("IP del ESP32: ");
  Serial.println(WiFi.localIP());

  mostrarMensajeLCD(" IP ASIGNADA:", WiFi.localIP().toString());
  delay(2000);
  mostrarMensajeLCD("MECATROAPUESTAS", "LISTO PARA JUGAR");

  // Configurar la ruta HTTP que consultará Python
  server.on("/girar", handleGirar);
  server.begin();
}

void loop() {
  server.handleClient();
}